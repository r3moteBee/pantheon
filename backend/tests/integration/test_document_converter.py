"""Verify document conversion subsystem and POST /api/files/convert endpoint.

Run: pytest backend/tests/integration/test_document_converter.py -v
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock

# Isolated settings setup
test_dir = tempfile.mkdtemp(prefix="pantheon-test-conv-")
os.environ["DATA_DIR"] = test_dir
os.environ["AUTH_PASSWORD"] = ""

from main import app  # noqa: E402
from config import get_settings  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from utils.document_converter import DocumentConverter, BinaryMissingError  # noqa: E402
from api.files import _get_workspace  # noqa: E402

settings = get_settings()

@pytest.fixture
def client():
    # Make sure workspace dir is fresh and isolated
    return TestClient(app)

@pytest.fixture
def converter():
    return DocumentConverter()


def test_md_to_html_conversion(converter, tmp_path):
    source = tmp_path / "test.md"
    target = tmp_path / "test.html"
    
    source.write_text("# Hello World\nThis is a test.", encoding="utf-8")
    converter.convert_file(source, target)
    
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert "Hello World" in content
    assert "This is a test." in content


def test_html_to_md_conversion(converter, tmp_path):
    source = tmp_path / "test.html"
    target = tmp_path / "test.md"
    
    source.write_text("<h1>Title</h1><p>Paragraph</p>", encoding="utf-8")
    converter.convert_file(source, target)
    
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert "Title" in content
    assert "Paragraph" in content


@patch("subprocess.run")
def test_soffice_pdf_conversion_mocked(mock_run, converter, tmp_path):
    source = tmp_path / "test.docx"
    target = tmp_path / "test.pdf"
    
    source.write_text("dummy docx contents", encoding="utf-8")
    
    # Mock find_soffice to return a dummy path
    with patch.object(converter, "find_soffice", return_value="/usr/bin/soffice"):
        # Mock subprocess.run to simulate successful conversion and create the output file
        def fake_run(cmd, **kwargs):
            out_pdf = Path(cmd[5]) / "test.pdf"
            out_pdf.write_text("dummy pdf output", encoding="utf-8")
            res = MagicMock()
            res.returncode = 0
            return res
            
        mock_run.side_effect = fake_run
        
        converter.convert_file(source, target)
        
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "dummy pdf output"
        assert mock_run.called
        cmd_args = mock_run.call_args[0][0]
        assert "--headless" in cmd_args
        assert "--convert-to" in cmd_args
        assert "pdf" in cmd_args


@patch("subprocess.run")
def test_pandoc_docx_conversion_mocked(mock_run, converter, tmp_path):
    source = tmp_path / "test.md"
    target = tmp_path / "test.docx"
    
    source.write_text("markdown text", encoding="utf-8")
    
    with patch.object(converter, "find_pandoc", return_value="/usr/bin/pandoc"):
        def fake_run(cmd, **kwargs):
            out_docx = Path(cmd[4])
            out_docx.write_text("dummy docx output", encoding="utf-8")
            res = MagicMock()
            res.returncode = 0
            return res
            
        mock_run.side_effect = fake_run
        
        converter.convert_file(source, target)
        
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "dummy docx output"
        assert mock_run.called
        cmd_args = mock_run.call_args[0][0]
        assert "/usr/bin/pandoc" == cmd_args[0]
        assert "-s" in cmd_args
        assert "-o" in cmd_args


def test_missing_binary_raises_error(converter, tmp_path):
    source = tmp_path / "test.md"
    target = tmp_path / "test.docx"
    source.write_text("test markdown", encoding="utf-8")
    
    with patch.object(converter, "find_pandoc", return_value=None):
        with pytest.raises(BinaryMissingError) as exc_info:
            converter.convert_file(source, target)
        
        assert "Pandoc is required" in str(exc_info.value)
        assert "Required binary 'pandoc' was not found" in str(exc_info.value)


def test_api_convert_markdown_to_html(client):
    workspace = _get_workspace("default")
    
    # Clean workspace
    for p in workspace.glob("*.md"):
        p.unlink()
    for p in workspace.glob("*.html"):
        p.unlink()
        
    doc1 = workspace / "doc1.md"
    doc2 = workspace / "doc2.md"
    doc1.write_text("# Doc One", encoding="utf-8")
    doc2.write_text("# Doc Two", encoding="utf-8")
    
    # 1. Batch convert using wildcard
    resp = client.post("/api/files/convert", json={
        "paths": ["*.md"],
        "target_format": "html"
    })
    
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_files"] == 2
    assert data["successful_count"] == 2
    assert data["failed_count"] == 0
    
    conv_results = {item["source_path"]: item for item in data["converted"]}
    assert "doc1.md" in conv_results
    assert "doc2.md" in conv_results
    assert conv_results["doc1.md"]["success"] is True
    assert conv_results["doc1.md"]["target_path"] == "doc1.html"
    
    assert (workspace / "doc1.html").exists()
    assert (workspace / "doc2.html").exists()
    assert "Doc One" in (workspace / "doc1.html").read_text(encoding="utf-8")


def test_api_convert_to_out_dir(client):
    workspace = _get_workspace("default")
    doc = workspace / "doc_to_dir.md"
    doc.write_text("# Content", encoding="utf-8")
    
    resp = client.post("/api/files/convert", json={
        "paths": ["doc_to_dir.md"],
        "target_format": "html",
        "out_dir": "nested/output"
    })
    
    assert resp.status_code == 200
    data = resp.json()
    assert data["successful_count"] == 1
    assert data["converted"][0]["target_path"] == "nested/output/doc_to_dir.html"
    
    assert (workspace / "nested" / "output" / "doc_to_dir.html").exists()


def test_api_convert_path_traversal_denied(client):
    resp = client.post("/api/files/convert", json={
        "paths": ["../outside.md"],
        "target_format": "html"
    })
    assert resp.status_code == 400
    assert "Path traversal not allowed" in resp.json()["detail"]

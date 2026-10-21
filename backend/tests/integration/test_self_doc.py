"""get_self_documentation / utils.self_doc: content, context awareness, no secrets.

Run: pytest backend/tests/integration/test_self_doc.py -v
"""
from __future__ import annotations

import asyncio
import os
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")
os.makedirs("/tmp/pantheon-tests-data/db", exist_ok=True)

from main import app
from config import get_settings
from utils import self_doc
from utils.self_doc import generate_self_doc

SECRETS = {   # values no real doc text contains
    "vault_master_key": "Qz7vK9qpW2xR4jmT8wnB6yhJ3",
    "secret_key": "Xq8Zw3Jk5Vp1Qy7Rf2Ug9Hb4",
    "llm_api_key": "sk-Wq4Zx8Kj2Yv6Pm9Qr3Tz7",
}


@pytest.fixture
def clean_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def secret_settings(clean_settings, monkeypatch):
    s = get_settings()
    for k, v in SECRETS.items():
        monkeypatch.setattr(s, k, v, raising=False)
    return s


def test_generate_self_doc(clean_settings):
    doc = generate_self_doc()
    assert doc.startswith("# Pantheon self-documentation")
    for section in ("## How Pantheon works", "## This conversation", "## This instance"):
        assert section in doc
    # the capability guide carries this instance's real numbers
    assert "tool rounds per turn" in doc and "TOOL_RESULT_MAX_CHARS" in doc
    assert "chat-attachments/" in doc                      # uploads are artifacts, not workspace
    assert "iteration/<job id, 8 chars>/turn-01.md" in doc


def test_self_doc_never_shows_secrets(secret_settings):
    doc = generate_self_doc()
    for name, value in SECRETS.items():
        assert name not in doc                             # not even the key name, masked
        for i in range(len(value) - 3):
            assert value[i:i + 4] not in doc, f"fragment of {name} leaked"
    # harmless settings that the old substring mask hid are shown
    assert f"llm_max_tokens={secret_settings.llm_max_tokens}" in doc
    assert "auth_session_days=" in doc


def test_settings_allowlist_has_no_credentials():
    for key in self_doc.SETTINGS_SHOWN:
        assert not any(w in key for w in ("secret", "password", "api_key", "master")), key
        assert not key.endswith(("_key", "_token", "_secret", "_password")), key


def test_urls_reduced_to_host():
    assert self_doc._host("https://user:pw@api.example.com:8443/v1?key=abc") == "api.example.com:8443"
    assert self_doc._is_local("10.42.0.1:8080") and self_doc._is_local("searxng.ns.svc.cluster.local")
    assert not self_doc._is_local("routellm.abacus.ai")


def test_context_section_follows_the_caller(clean_settings):
    web = generate_self_doc(interactive=True, host_exec=True)
    bg = generate_self_doc(interactive=False, host_exec=False)
    assert "web chat driven by the user" in web
    assert "not interactive" in bg
    assert "code_execute, run_command, git_*" in bg.split("Not available here:")[1]
    web_off = web.split("Not available here:")[1].split("\n")[0] if "Not available here:" in web else ""
    assert "code_execute" not in web_off


def test_self_doc_tool_runs_off_the_event_loop(clean_settings, monkeypatch):
    from agent import tools
    seen = {}

    def fake(project_id=None, interactive=True, host_exec=True):
        import threading
        seen.update(thread=threading.current_thread().name, args=(project_id, interactive, host_exec))
        return "doc"

    monkeypatch.setattr(self_doc, "generate_self_doc", fake)
    out = asyncio.run(tools.execute_tool("get_self_documentation", {}, None, project_id="p1",
                                         interactive=False, host_exec=False))
    assert out == "doc"
    assert seen["args"] == ("p1", False, False)
    assert seen["thread"] != "MainThread"


def test_self_doc_endpoint(clean_settings):
    r = TestClient(app).get("/api/system/self-doc")
    assert r.status_code == 200, r.text
    assert r.json()["documentation"].startswith("# Pantheon self-documentation")


def test_self_doc_tool_registered():
    from agent.tools import TOOL_SCHEMAS
    assert "get_self_documentation" in [t["function"]["name"] for t in TOOL_SCHEMAS]

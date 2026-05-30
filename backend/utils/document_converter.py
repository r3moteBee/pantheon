"""Document conversion utility for converting files between formats.

Supports:
- markdown/txt -> html
- html -> markdown
- docx -> html/markdown
- pdf -> text/markdown
- docx/xlsx/pptx/html/md -> pdf (via LibreOffice soffice)
- md/html -> docx/epub (via Pandoc)
"""
from __future__ import annotations
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class BinaryMissingError(Exception):
    """Exception raised when a required system binary is missing."""
    def __init__(self, binary_name: str, message: str):
        self.binary_name = binary_name
        self.message = message
        super().__init__(self._build_detailed_message())

    def _build_detailed_message(self) -> str:
        plat = sys.platform
        instructions = ""
        if self.binary_name == "soffice":
            if plat == "darwin":
                instructions = "On macOS: Install via Homebrew: 'brew install --cask libreoffice' or download from libreoffice.org"
            elif plat.startswith("linux"):
                instructions = "On Linux (Debian/Ubuntu): run 'sudo apt-get update && sudo apt-get install -y libreoffice'"
            else:
                instructions = "On Windows: Download and install from https://www.libreoffice.org/download/download/"
        elif self.binary_name == "pandoc":
            if plat == "darwin":
                instructions = "On macOS: Install via Homebrew: 'brew install pandoc'"
            elif plat.startswith("linux"):
                instructions = "On Linux (Debian/Ubuntu): run 'sudo apt-get update && sudo apt-get install -y pandoc'"
            else:
                instructions = "On Windows: Run 'winget install jgm.pandoc' or install via chocolatey 'choco install pandoc'"
        
        return f"{self.message}\nRequired binary '{self.binary_name}' was not found.\nInstallation instructions:\n{instructions}"


class DocumentConverter:
    """Unified service for converting document formats."""

    def find_pandoc(self) -> str | None:
        """Find the path to the pandoc binary."""
        path = shutil.which("pandoc")
        if path:
            return path
        return None

    def find_soffice(self) -> str | None:
        """Find the path to the LibreOffice (soffice) binary."""
        # 1. Check path
        for name in ["soffice", "libreoffice"]:
            path = shutil.which(name)
            if path:
                return path

        # 2. Check platform-specific standard paths
        if sys.platform == "darwin":  # macOS
            mac_path = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
            if os.path.exists(mac_path):
                return mac_path
        elif sys.platform == "win32":  # Windows
            win_paths = [
                r"C:\Program Files\LibreOffice\program\soffice.exe",
                r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
            ]
            for p in win_paths:
                if os.path.exists(p):
                    return p
        elif sys.platform.startswith("linux"):
            linux_paths = [
                "/usr/bin/soffice",
                "/usr/bin/libreoffice",
                "/usr/local/bin/soffice",
            ]
            for p in linux_paths:
                if os.path.exists(p):
                    return p

        return None

    def convert_file(
        self,
        source_path: Path,
        target_path: Path,
        target_format: str | None = None,
    ) -> None:
        """Convert a single file from one format to another.

        If target_format is omitted, it will be inferred from target_path's extension.
        """
        if not source_path.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")

        src_ext = source_path.suffix.lower()
        tgt_ext = target_path.suffix.lower()
        
        if not target_format:
            target_format = tgt_ext.lstrip(".")

        target_format = target_format.lower()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Converting file: %s (%s) -> %s (%s)",
            source_path, src_ext, target_path, target_format
        )

        # Case 1: Identical formats (copy)
        if src_ext == f".{target_format}":
            shutil.copy2(source_path, target_path)
            return

        # Case 2: Markdown/Text to HTML (pure Python)
        if src_ext in [".md", ".markdown", ".txt"] and target_format == "html":
            self._md_to_html(source_path, target_path)
            return

        # Case 3: HTML to Markdown (pure Python)
        if src_ext in [".html", ".htm"] and target_format in ["md", "markdown"]:
            self._html_to_md(source_path, target_path)
            return

        # Case 4: DOCX to HTML or Markdown (pure Python via Mammoth)
        if src_ext == ".docx" and target_format in ["html", "md", "markdown"]:
            self._docx_to_text(source_path, target_path, target_format)
            return

        # Case 5: PDF to Markdown/Text (pure Python via pypdf)
        if src_ext == ".pdf" and target_format in ["txt", "md", "markdown"]:
            self._pdf_to_text(source_path, target_path)
            return

        # Case 6: Target is PDF (using LibreOffice headless)
        if target_format == "pdf":
            self._convert_to_pdf(source_path, target_path)
            return

        # Case 7: Markdown/HTML to DOCX (requires Pandoc)
        if src_ext in [".md", ".markdown", ".html", ".htm"] and target_format == "docx":
            self._convert_to_docx(source_path, target_path)
            return

        # Case 8: General fallback using Pandoc if available
        pandoc_path = self.find_pandoc()
        if pandoc_path:
            try:
                cmd = [pandoc_path, "-s", str(source_path), "-o", str(target_path)]
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if res.returncode == 0:
                    return
                logger.debug("General pandoc conversion fallback failed: %s", res.stderr)
            except Exception as e:
                logger.debug("Failed running pandoc fallback: %s", e)

        raise ValueError(
            f"Unsupported conversion format pathway: from {src_ext} to .{target_format}. "
            "Please install Pandoc and LibreOffice to enable advanced conversions."
        )

    def _md_to_html(self, source_path: Path, target_path: Path) -> None:
        """Convert Markdown to HTML using pure-Python markdown library."""
        try:
            import markdown
        except ImportError:
            raise RuntimeError("The 'markdown' package is not installed. Please add it to requirements.")
        
        content = source_path.read_text(encoding="utf-8")
        # Use common markdown extensions for rich styling/features
        html = markdown.markdown(content, extensions=["extra", "tables", "toc"])
        target_path.write_text(html, encoding="utf-8")

    def _html_to_md(self, source_path: Path, target_path: Path) -> None:
        """Convert HTML to Markdown using pure-Python markdownify library."""
        try:
            import markdownify
        except ImportError:
            raise RuntimeError("The 'markdownify' package is not installed. Please add it to requirements.")
        
        content = source_path.read_text(encoding="utf-8")
        md = markdownify.markdownify(content)
        target_path.write_text(md, encoding="utf-8")

    def _docx_to_text(self, source_path: Path, target_path: Path, format_type: str) -> None:
        """Convert DOCX to HTML or Markdown using Mammoth (pure Python)."""
        try:
            import mammoth
        except ImportError:
            raise RuntimeError("The 'mammoth' package is not installed. Please add it to requirements.")

        with open(source_path, "rb") as docx_file:
            if format_type == "html":
                result = mammoth.convert_to_html(docx_file)
            else:
                result = mammoth.convert_to_markdown(docx_file)
            
            if result.messages:
                logger.warning("Mammoth warnings during conversion: %s", result.messages)
            
            target_path.write_text(result.value, encoding="utf-8")

    def _pdf_to_text(self, source_path: Path, target_path: Path) -> None:
        """Extract text from PDF using pypdf (pure Python)."""
        try:
            import pypdf
        except ImportError:
            raise RuntimeError("The 'pypdf' package is not installed. Please add it to requirements.")

        with open(source_path, "rb") as f:
            reader = pypdf.PdfReader(f)
            text = []
            for i, page in enumerate(reader.pages):
                page_text = page.extract_text()
                if page_text:
                    text.append(page_text)
            
            full_text = "\n\n--- Page Break ---\n\n".join(text)
            target_path.write_text(full_text, encoding="utf-8")

    def _convert_to_pdf(self, source_path: Path, target_path: Path) -> None:
        """Convert document to PDF using headless LibreOffice."""
        soffice_path = self.find_soffice()
        if not soffice_path:
            raise BinaryMissingError(
                "soffice",
                "LibreOffice is required to render documents (DOCX, XLSX, PPTX, HTML, Markdown) to PDF."
            )

        src_ext = source_path.suffix.lower()

        with tempfile.TemporaryDirectory() as tmpdir:
            # If input is Markdown, first compile it to HTML using pure-Python, then let LibreOffice convert it to PDF
            if src_ext in [".md", ".markdown"]:
                try:
                    import markdown
                except ImportError:
                    raise RuntimeError("The 'markdown' package is not installed.")
                
                md_content = source_path.read_text(encoding="utf-8")
                html_content = markdown.markdown(md_content, extensions=["extra", "tables"])
                temp_source = Path(tmpdir) / f"{source_path.stem}.html"
                temp_source.write_text(html_content, encoding="utf-8")
            else:
                # Copy source to tmpdir to prevent output directory pollution
                temp_source = Path(tmpdir) / source_path.name
                shutil.copy2(source_path, temp_source)

            # Run headless LibreOffice
            cmd = [
                soffice_path,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                tmpdir,
                str(temp_source),
            ]
            
            logger.info("Running LibreOffice command: %s", " ".join(cmd))
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode != 0:
                raise RuntimeError(
                    f"LibreOffice PDF conversion failed (code {res.returncode}):\n{res.stderr or res.stdout}"
                )

            # Locate the generated PDF
            generated_pdf = Path(tmpdir) / f"{temp_source.stem}.pdf"
            if not generated_pdf.exists():
                raise RuntimeError(
                    f"LibreOffice executed successfully but did not produce '{temp_source.stem}.pdf'. stdout: {res.stdout}"
                )

            # Move to target destination
            shutil.move(str(generated_pdf), str(target_path))

    def _convert_to_docx(self, source_path: Path, target_path: Path) -> None:
        """Convert Markdown/HTML to DOCX using Pandoc."""
        pandoc_path = self.find_pandoc()
        if not pandoc_path:
            raise BinaryMissingError(
                "pandoc",
                "Pandoc is required to convert documents to DOCX."
            )

        cmd = [
            pandoc_path,
            "-s",
            str(source_path),
            "-o",
            str(target_path)
        ]

        logger.info("Running Pandoc command: %s", " ".join(cmd))
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            raise RuntimeError(
                f"Pandoc DOCX conversion failed (code {res.returncode}):\n{res.stderr or res.stdout}"
            )

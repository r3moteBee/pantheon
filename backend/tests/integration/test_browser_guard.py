"""Browser-tool SSRF guard against a real Chromium (skipped when Playwright
isn't installed). Private = 127.0.0.1, "public" = localhost (faked)."""
from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

pa = pytest.importorskip("playwright.async_api")


@pytest.fixture
def server():
    hits: list[str] = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            port = self.server.server_address[1]
            if self.path == "/public":
                body = (f"<html><body>public page<img src='http://127.0.0.1:{port}/secret-img'>"
                        f"<script>fetch('http://127.0.0.1:{port}/secret-fetch')</script></body></html>")
                self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
                self.wfile.write(body.encode())
            elif self.path == "/redir":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{port}/internal")
                self.end_headers()
            else:
                self.send_response(200); self.end_headers(); self.wfile.write(b"INTERNAL SECRET")

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], hits
    srv.shutdown()


@pytest.mark.asyncio
async def test_browser_blocks_internal_subresources_and_redirects(server, monkeypatch):
    from agent import browser_tools as bt
    from utils.net import UnsafeURLError

    for var in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY"):
        monkeypatch.delenv(var, raising=False)
    port, hits = server
    if os.path.exists("/opt/pw-browsers/chromium") and not os.getenv("BROWSER_EXECUTABLE_PATH"):
        monkeypatch.setenv("BROWSER_EXECUTABLE_PATH", "/opt/pw-browsers/chromium")

    async def fake_check(url):
        if "127.0.0.1" in url:
            raise UnsafeURLError("private")
        return ["93.184.216.34"]

    bt._HOST_OK.clear()
    try:
        with patch("utils.net.check_public_url", fake_check):
            try:
                await bt._ensure_browser()
            except Exception as e:  # no browser binary available
                pytest.skip(f"chromium unavailable: {e}")
            out = await bt.browser_open(f"http://localhost:{port}/public", "guard-test")
            assert out.startswith("Opened")
            assert "public page" in await bt.browser_read("guard-test")
            out = await bt.browser_open(f"http://localhost:{port}/redir", "guard-test")
            assert "refused" in out
            out = await bt.browser_open(f"http://127.0.0.1:{port}/internal", "guard-test")
            assert "refused" in out
    finally:
        await bt.shutdown()
    # No request of any kind reached the "internal" endpoints.
    assert not [h for h in hits if h in ("/internal", "/secret-img", "/secret-fetch")], hits

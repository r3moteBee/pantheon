"""Regression tests for the 2026-10-22 API security fixes: project import
(.git restore, zip bombs), projects.json writes, the origin check (ports),
middleware order, MCP OAuth redirect_uri, MCP URL/header masking, the
password throttle, messaging allowlists and the vault v1 migration."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

HOMELY = "homely.tail752d22.ts.net"


# ── helpers ──────────────────────────────────────────────────────────────────

@pytest.fixture
def data(tmp_path):
    """Point every module-level `settings` used here at tmp_path."""
    from config import get_settings
    import api.project_export, api.project_import, api.projects, api.personas
    s = get_settings()
    stack = contextlib.ExitStack()
    objs = {id(m.settings): m.settings for m in
            (api.projects, api.project_export, api.project_import, api.personas)}
    objs[id(s)] = s
    with stack:
        for obj in objs.values():
            stack.enter_context(patch.object(obj, "data_dir", tmp_path))
        s.db_dir.mkdir(parents=True, exist_ok=True)
        yield tmp_path


def _archive(members: dict[str, bytes], components=("files",), compress=zipfile.ZIP_DEFLATED) -> bytes:
    from api.project_export import EXPORT_FORMAT_VERSION, EXPORT_MAGIC
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compress) as zf:
        zf.writestr("manifest.json", json.dumps({
            "magic": EXPORT_MAGIC, "format_version": EXPORT_FORMAT_VERSION,
            "project_id": "src", "project_name": "Src", "components": list(components),
            "checksums": {},
        }))
        for name, body in members.items():
            zf.writestr(name, body)
    return buf.getvalue()


# ── 1. project import / export ───────────────────────────────────────────────

def test_import_never_restores_git_dirs_or_repo_checkouts(data):
    from api.project_import import import_project
    archive = _archive({
        "files/workspace/repos/evil__repo/.git/config": b"[core]\n\tfsmonitor = touch /tmp/pwned\n",
        "files/workspace/repos/evil__repo/.git/hooks/post-checkout": b"#!/bin/sh\nid\n",
        "files/workspace/repos/evil__repo/README.md": b"readme",
        "files/workspace/other/.git/config": b"[core]\n\thooksPath = /tmp\n",
        "files/workspace/other/.GIT/HEAD": b"ref: refs/heads/main\n",
        "files/workspace/.hg/hgrc": b"[hooks]\n",
        "files/workspace/notes.md": b"keep me",
        "files/personality/soul.md": b"soul",
    })
    for skip_scan in (False, True):
        res = import_project(archive, target_project_id=f"imp{int(skip_scan)}",
                             target_project_name="Imp", skip_scan=skip_scan)
        assert res.success, res
        ws = data / "projects" / f"imp{int(skip_scan)}" / "workspace"
        assert (ws / "notes.md").read_text() == "keep me"
        assert (data / "projects" / f"imp{int(skip_scan)}" / "personality" / "soul.md").exists()
        restored = [p.relative_to(ws).as_posix() for p in ws.rglob("*") if p.is_file()]
        assert restored == ["notes.md"], restored
        assert not any(part.lower() == ".git" for p in ws.rglob("*") for part in p.parts)
        assert any("VCS" in w for w in res.warnings)
        assert any("git_sync_repo" in w for w in res.warnings)


def test_export_skips_git_dirs_and_symlinks(tmp_path):
    from api.project_export import _add_directory_to_zip
    src = tmp_path / "ws"
    (src / "repos" / "o__r" / ".git" / "hooks").mkdir(parents=True)
    (src / "repos" / "o__r" / ".git" / "config").write_text("[core]")
    (src / "repos" / "o__r" / "main.py").write_text("print(1)")
    (src / "a.md").write_text("a")
    (tmp_path / "secret.txt").write_text("outside")
    (src / "link.txt").symlink_to(tmp_path / "secret.txt")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        n = _add_directory_to_zip(zf, src, "files/workspace")
        names = zf.namelist()
    assert sorted(names) == ["files/workspace/a.md", "files/workspace/repos/o__r/main.py"]
    assert n == 2


def test_zip_bomb_is_rejected_before_extraction(data):
    from api import project_import as pi
    bomb = _archive({"files/workspace/zeros.txt": b"\0" * (20 * 1024 * 1024)})
    scan = pi.scan_archive(bomb)
    assert not scan.passed
    assert any("compression ratio" in f.message for f in scan.findings)
    # also with the scan skipped
    res = pi.import_project(bomb, target_project_id="bomb", skip_scan=True)
    assert not res.success and "ratio" in res.message
    assert not (data / "projects" / "bomb" / "workspace" / "zeros.txt").exists()


def test_total_uncompressed_cap(data):
    from api import project_import as pi
    archive = _archive({f"files/workspace/f{i}.txt": os.urandom(4096) for i in range(5)},
                       compress=zipfile.ZIP_STORED)
    with patch.object(pi, "_MAX_TOTAL_UNCOMPRESSED", 10_000):
        res = pi.import_project(archive, target_project_id="big", skip_scan=True)
    assert not res.success and "expands" in res.message


@pytest.mark.parametrize("name", ["/etc/passwd", "files/../../x", "C:/x", "files\\..\\..\\x"])
def test_unsafe_member_paths_are_rejected(name):
    from api.project_import import ArchiveRejected, check_archive_limits
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo(name), b"x")
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf, pytest.raises(ArchiveRejected):
        check_archive_limits(zf)


# ── 3. projects.json ─────────────────────────────────────────────────────────

def test_corrupt_projects_json_is_never_overwritten(data):
    import asyncio
    from fastapi import HTTPException
    from api import projects
    meta = data / "db" / "projects.json"
    meta.write_text('{"default": {"id": "default"}, "mine": {"id": "mine"')   # truncated
    with pytest.raises(HTTPException) as e:
        projects._load_projects()
    assert e.value.status_code == 500 and "projects.json.bak" in e.value.detail
    with pytest.raises(HTTPException):
        asyncio.run(projects.create_project(projects.CreateProjectRequest(name="x", id="x")))
    assert meta.read_text().endswith('"mine": {"id": "mine"')


def test_projects_json_writes_are_atomic_with_backup_and_locked(data):
    import asyncio
    import threading
    from api import projects
    from utils.jsonfile import backup_path, read_json, update_json
    projects._load_projects()          # creates default
    asyncio.run(projects.create_project(projects.CreateProjectRequest(name="A", id="a")))
    meta = data / "db" / "projects.json"
    assert set(read_json(meta)) == {"default", "a"}
    assert set(json.loads(backup_path(meta).read_text())) == {"default"}
    assert not [p for p in meta.parent.iterdir() if p.name.endswith(".tmp")]

    def add(i):
        with update_json(meta, dict) as d:
            d[f"t{i}"] = {"id": f"t{i}"}
    threads = [threading.Thread(target=add, args=(i,)) for i in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert {f"t{i}" for i in range(20)} <= set(read_json(meta))


def test_persona_writer_uses_the_locked_helper(data):
    from api import personas
    meta = data / "db" / "projects.json"
    meta.write_text(json.dumps({"p1": {"id": "p1"}}))
    personas._update_project_persona("p1", "hermes")
    assert json.loads(meta.read_text())["p1"]["persona_id"] == "hermes"
    meta.write_text("{broken")
    personas._update_project_persona("p1", None)      # logged, not overwritten
    assert meta.read_text() == "{broken"


# ── 4. origin check: scheme + host + port ────────────────────────────────────

def _auth_settings(**kw):
    base = dict(auth_password="", secret_key="s", allowed_hosts=HOMELY,
                cors_origins="", cors_origins_list=[], trusted_proxies="127.0.0.1,::1",
                db_dir=Path(tempfile.mkdtemp()), auth_session_days=30,
                allow_insecure_defaults=False, public_base_url="",
                vault_master_key="k" * 40)
    base.update(kw)
    return SimpleNamespace(**base)


def _probe_app():
    from api.auth import origin_is_allowed
    app = FastAPI()

    @app.post("/probe")
    async def probe(request: Request):
        return {"ok": origin_is_allowed(request.headers.get("origin"), conn=request)}
    return app


# What the homely router nginx sends to the pod (tailscale serve :18790 ->
# oauth2-proxy --pass-host-header -> nginx `proxy_set_header Host $host`,
# which DROPS the port): plain http from a pod IP, no X-Forwarded-Port.
HOMELY_HEADERS = {
    "host": HOMELY,
    "x-forwarded-host": HOMELY,
    "x-forwarded-proto": "https",
    "x-forwarded-for": "100.101.102.103, 10.42.0.1",
    "x-forwarded-preferred-username": "brent@r3motely.net",
}


def _client(app, peer="10.42.0.57"):
    """TestClient whose requests come from ``peer`` (sets scope["client"])."""
    async def with_peer(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = {**scope, "client": (peer, 50000)}
        await app(scope, receive, send)
    return TestClient(with_peer)


def test_homely_header_set_allows_pantheon_origin_and_blocks_other_ports():
    cfg = _auth_settings(cors_origins_list=[f"https://{HOMELY}:18790"])
    with patch("api.auth.get_settings", return_value=cfg):
        c = _client(_probe_app())
        ok = c.post("/probe", headers={**HOMELY_HEADERS, "origin": f"https://{HOMELY}:18790"})
        assert ok.json() == {"ok": True}
        for other in (f"https://{HOMELY}:8443", f"https://{HOMELY}", f"https://{HOMELY}:2283",
                      f"http://{HOMELY}:18790", "https://evil.example"):
            r = c.post("/probe", headers={**HOMELY_HEADERS, "origin": other})
            assert r.json() == {"ok": False}, other


def test_homely_full_middleware_csrf_and_websocket():
    """The real app + WebSocket guard with homely's headers and settings."""
    from main import app
    from api.chat import websocket_chat
    from starlette.websockets import WebSocketDisconnect
    cfg = _auth_settings(cors_origins_list=[f"https://{HOMELY}:18790"])
    with patch("api.auth.get_settings", return_value=cfg), patch("main.get_settings", return_value=cfg):
        c = _client(app)
        good = c.post("/api/auth/login", json={"password": ""},
                      headers={**HOMELY_HEADERS, "origin": f"https://{HOMELY}:18790"})
        assert good.status_code == 200
        bad = c.post("/api/auth/login", json={"password": ""},
                     headers={**HOMELY_HEADERS, "origin": f"https://{HOMELY}:8443"})
        assert bad.status_code == 403

        ws_app = FastAPI()
        ws_app.websocket("/ws/chat")(websocket_chat)
        wc = _client(ws_app)
        with pytest.raises(WebSocketDisconnect):
            with wc.websocket_connect("/ws/chat", headers={**HOMELY_HEADERS,
                                                           "origin": f"https://{HOMELY}:8443"}) as ws:
                ws.receive_text()
        from api.auth import authorize_websocket

        class _WS:
            def __init__(self, origin):
                from starlette.datastructures import Headers, URL
                self.headers = Headers({**HOMELY_HEADERS, "origin": origin})
                self.client = SimpleNamespace(host="10.42.0.57")
                self.url = URL(f"ws://{HOMELY}/ws/chat")
                self.cookies = {}
                self.closed = None

            async def close(self, code=1000):
                self.closed = code
        import asyncio
        good_ws = _WS(f"https://{HOMELY}:18790")
        assert asyncio.run(authorize_websocket(good_ws)) is True
        bad_ws = _WS(f"https://{HOMELY}:8443")
        assert asyncio.run(authorize_websocket(bad_ws)) is False and bad_ws.closed == 1008


def test_same_origin_without_cors_entry_uses_host_port():
    cfg = _auth_settings()
    with patch("api.auth.get_settings", return_value=cfg):
        c = _client(_probe_app(), peer="192.168.1.5")
        assert c.post("/probe", headers={"host": "box.lan:8000", "origin": "http://box.lan:8000"}).json()["ok"]
        assert not c.post("/probe", headers={"host": "box.lan:8000", "origin": "http://box.lan:8443"}).json()["ok"]
        # Host without a port = default port of the scheme
        assert c.post("/probe", headers={"host": "box.lan", "origin": "http://box.lan"}).json()["ok"]
        assert not c.post("/probe", headers={"host": "box.lan", "origin": "http://box.lan:8443"}).json()["ok"]


def test_forwarded_headers_only_from_trusted_proxies():
    hdrs = {"host": "127.0.0.1:8000", "x-forwarded-host": "pantheon.example.com:8443",
            "x-forwarded-proto": "https", "origin": "https://pantheon.example.com:8443"}
    with patch("api.auth.get_settings", return_value=_auth_settings()):
        assert _client(_probe_app(), peer="127.0.0.1").post("/probe", headers=hdrs).json()["ok"]
        assert not _client(_probe_app(), peer="203.0.113.9").post("/probe", headers=hdrs).json()["ok"]
    hdrs2 = {"host": "pantheon.example.com", "x-forwarded-proto": "https",
             "x-forwarded-port": "8443", "origin": "https://pantheon.example.com:8443"}
    with patch("api.auth.get_settings", return_value=_auth_settings(trusted_proxies="10.0.0.0/8")):
        assert _client(_probe_app(), peer="10.1.2.3").post("/probe", headers=hdrs2).json()["ok"]


def test_client_ip_uses_forwarded_for_only_from_trusted_proxy():
    from api.auth import client_ip
    app = FastAPI()

    @app.get("/ip")
    async def ip(request: Request):
        return {"ip": client_ip(request)}
    xff = {"x-forwarded-for": "6.6.6.6, 100.101.102.103, 10.42.0.1"}
    with patch("api.auth.get_settings", return_value=_auth_settings(trusted_proxies="10.42.0.0/16")):
        assert _client(app, "10.42.0.9").get("/ip", headers=xff).json()["ip"] == "100.101.102.103"
        assert _client(app, "8.8.8.8").get("/ip", headers=xff).json()["ip"] == "8.8.8.8"
    with patch("api.auth.get_settings", return_value=_auth_settings()):
        assert _client(app, "10.42.0.9").get("/ip", headers=xff).json()["ip"] == "10.42.0.9"


# ── 7. CORS outermost ────────────────────────────────────────────────────────

def test_cors_wraps_auth_preflight_and_401():
    import main
    cfg = _auth_settings(auth_password="pw", cors_origins_list=["http://localhost:5173"])
    origin = "http://localhost:5173"
    assert main.app.user_middleware[0].cls.__name__ == "CORSMiddleware"
    with patch("api.auth.get_settings", return_value=cfg), patch("main.get_settings", return_value=cfg):
        c = TestClient(main.app)
        pre = c.options("/api/projects", headers={"origin": origin, "access-control-request-method": "GET"})
        assert pre.status_code == 200
        assert pre.headers.get("access-control-allow-origin") == origin
        r = c.get("/api/projects", headers={"origin": origin})
        assert r.status_code == 401
        assert r.headers.get("access-control-allow-origin") == origin


# ── 2. MCP OAuth redirect_uri ────────────────────────────────────────────────

def _req(headers, peer="10.42.0.57", scheme="http"):
    from starlette.requests import Request as R
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return R({"type": "http", "method": "POST", "path": "/api/mcp/connections/x/start-oauth",
              "headers": raw, "client": (peer, 1), "scheme": scheme, "server": ("pod", 8000),
              "query_string": b""})


def test_oauth_callback_url_from_request():
    from mcp_client.oauth import callback_url_for, DEFAULT_CALLBACK_URL
    cfg = _auth_settings(cors_origins_list=[f"https://{HOMELY}:18790"])
    with patch("api.auth.get_settings", return_value=cfg), patch("config.get_settings", return_value=cfg):
        assert callback_url_for(_req({**HOMELY_HEADERS, "origin": f"https://{HOMELY}:18790"})) == \
            f"https://{HOMELY}:18790/api/mcp/oauth/callback"
        # a foreign Origin is never used
        assert callback_url_for(_req({**HOMELY_HEADERS, "origin": f"https://{HOMELY}:8443"})) == \
            f"http://{HOMELY}/api/mcp/oauth/callback"
        assert callback_url_for(_req({"host": "localhost:8000"}, peer="127.0.0.1")) == DEFAULT_CALLBACK_URL
        assert callback_url_for(None) == DEFAULT_CALLBACK_URL
    cfg2 = _auth_settings(public_base_url="https://p.example.com:9000/")
    with patch("api.auth.get_settings", return_value=cfg2), patch("config.get_settings", return_value=cfg2):
        assert callback_url_for(_req({"host": "x"})) == "https://p.example.com:9000/api/mcp/oauth/callback"


def test_oauth_flow_stores_redirect_uri_for_the_exchange():
    import asyncio
    from mcp_client import oauth
    as_meta = oauth.AuthServerMetadata(issuer="https://as", authorization_endpoint="https://as/auth",
                                       token_endpoint="https://as/token")
    url = oauth.build_authorize_url(name="c", as_meta=as_meta, client_id="cid",
                                    redirect_uri=f"https://{HOMELY}:18790/api/mcp/oauth/callback",
                                    scopes=[], resource="")
    from urllib.parse import parse_qs, urlsplit
    q = parse_qs(urlsplit(url).query)
    pending = oauth.take_pending(q["state"][0])
    assert pending.redirect_uri == q["redirect_uri"][0] == f"https://{HOMELY}:18790/api/mcp/oauth/callback"
    sent = {}

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"access_token": "a"}

    class _C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, **k):
            sent.update(data)
            return _Resp()
    with patch("mcp_client.oauth.httpx.AsyncClient", _C):
        asyncio.run(oauth.exchange_code(pending=pending, code="x"))
    assert sent["redirect_uri"] == pending.redirect_uri


# ── 6. MCP masking ───────────────────────────────────────────────────────────

def test_mask_and_restore_url():
    from mcp_client.manager import mask_url, restore_masked_url
    real = "https://user:pa55@mcp.tavily.com/mcp/?tavilyApiKey=tvly-SECRET&mode=x#frag"
    masked = mask_url(real)
    assert "SECRET" not in masked and "pa55" not in masked and "user" not in masked
    assert "tavilyApiKey=***" in masked and "mode=***" in masked
    # the UI sends back an edited, still-masked URL
    edited = masked.replace("/mcp/", "/mcp2/")
    restored = restore_masked_url(edited, real)
    assert restored.startswith("https://user:pa55@mcp.tavily.com/mcp2/?")
    assert "tavilyApiKey=tvly-SECRET" in restored and "mode=x" in restored
    assert restore_masked_url("https://new.example/mcp", real) == "https://new.example/mcp"


def test_list_connections_and_debug_mask_secrets():
    import asyncio
    from mcp_client.manager import MCPManager
    from api import mcp as mcp_api
    mgr = MCPManager.__new__(MCPManager)
    mgr._configs = [{"name": "t", "url": "https://mcp.example/?apiKey=SECRET1",
                     "api_key": "", "headers": {"X-Api-Key": "SECRET2", "Authorization": "Bearer SECRET3"}}]
    mgr._clients = {}
    with patch("mcp_client.budget.limits", return_value={}), patch("mcp_client.budget.unit", return_value="call"), \
         patch("mcp_client.budget.preset_for", return_value=None):
        listed = json.dumps(mgr.list_connections())
    assert "SECRET" not in listed
    with patch("api.mcp.get_mcp_manager", return_value=mgr):
        dbg = json.dumps(asyncio.run(mcp_api.debug_connection("t")))
    assert "SECRET" not in dbg


# ── 9. update/execute throttle ───────────────────────────────────────────────

def test_update_execute_password_is_throttled():
    import api.auth as auth
    from api.system import router
    app = FastAPI()
    app.include_router(router, prefix="/api")
    auth._failures.clear()
    cfg = _auth_settings(auth_password="right")
    with patch("api.auth.get_settings", return_value=cfg), patch("config.get_settings", return_value=cfg):
        c = TestClient(app)
        codes = [c.post("/api/system/update/execute", json={"password": "wrong"}).status_code
                 for _ in range(auth._MAX_FAILURES + 1)]
    auth._failures.clear()
    assert codes[:auth._MAX_FAILURES] == [401] * auth._MAX_FAILURES
    assert codes[-1] == 429


# ── 5. allowlists re-read per message ────────────────────────────────────────

def test_slack_and_discord_allowlists_are_not_captured_at_start():
    import inspect
    from messaging.adapters import discord as d, slack as s
    for mod, getter in ((s, "_get_allowed_channels()"), (d, "_get_allowed_guild_ids()")):
        src = inspect.getsource(mod)
        assert f"= self.{getter}" not in src
        assert f"adapter.{getter}" in src


def test_telegram_skill_callback_checks_allowlist():
    import inspect
    from messaging.adapters import telegram
    src = inspect.getsource(telegram)
    body = src[src.index("async def skill_callback"):src.index("# ── Register handlers")]
    assert "_is_allowed(update)" in body
    assert "_TG_MAX_PENDING" in src


# ── 11. vault v1 migration ───────────────────────────────────────────────────

def test_vault_migration_aborts_when_a_row_is_unreadable(tmp_path):
    import sqlite3
    from secrets.vault import SecretsVault
    key = "k" * 40
    db = str(tmp_path / "vault.db")
    good = SecretsVault._legacy_fernet(key)
    other = SecretsVault._legacy_fernet("x" * 40)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE secrets (key TEXT PRIMARY KEY, encrypted_value BLOB NOT NULL, "
                 "created_at REAL, updated_at REAL)")
    conn.execute("INSERT INTO secrets VALUES ('ok', ?, 0, 0)", (good.encrypt(b"v1"),))
    conn.execute("INSERT INTO secrets VALUES ('bad', ?, 0, 0)", (other.encrypt(b"v2"),))
    conn.commit()
    conn.close()
    v = SecretsVault(db_path=db, master_key=key)
    conn = sqlite3.connect(db)
    meta = dict(conn.execute("SELECT key, value FROM vault_meta").fetchall())
    blobs = dict(conn.execute("SELECT key, encrypted_value FROM secrets").fetchall())
    conn.close()
    assert meta.get("kdf_version") != SecretsVault._KDF_VERSION
    assert good.decrypt(blobs["ok"]) == b"v1"          # left v1-encrypted
    assert v.get_secret("ok") == "v1"                   # still readable

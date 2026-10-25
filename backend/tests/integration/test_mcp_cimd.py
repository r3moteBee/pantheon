"""MCP OAuth client registration per spec 2026-07-28: pre-registered
credentials first (bound to the issuer), then a Client ID Metadata Document
when the authorization server resolves URL client ids, then Dynamic Client
Registration, else a clear error; plus the spec's authorization-server
metadata discovery order with issuer validation, the hosted metadata
document, and the CIMD -> DCR fallback when the AS rejects our document."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, patch

import httpx
import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from mcp_client import oauth  # noqa: E402

CIMD = "https://pantheon.example.com/api/mcp/oauth/client-metadata.json"
CB = "https://pantheon.example.com/api/mcp/oauth/callback"


def _settings(**kw):
    from config import Settings
    base = dict(public_base_url="https://pantheon.example.com", mcp_cimd_enabled=True, mcp_client_id_metadata_url="")
    base.update(kw)
    return Settings(**base)


def _as(issuer="https://as.example", *, cimd=False, dcr=True):
    return oauth.AuthServerMetadata(
        issuer=issuer, authorization_endpoint=f"{issuer}/auth", token_endpoint=f"{issuer}/token",
        registration_endpoint=f"{issuer}/register" if dcr else None,
        client_id_metadata_document_supported=cimd)


# ── Discovery ────────────────────────────────────────────────────────────────

def test_metadata_urls_follow_the_spec_order():
    assert oauth.auth_server_metadata_urls("https://auth.example.com") == [
        "https://auth.example.com/.well-known/oauth-authorization-server",
        "https://auth.example.com/.well-known/openid-configuration"]
    assert oauth.auth_server_metadata_urls("https://auth.example.com/tenant1/") == [
        "https://auth.example.com/.well-known/oauth-authorization-server/tenant1",
        "https://auth.example.com/.well-known/openid-configuration/tenant1",
        "https://auth.example.com/tenant1/.well-known/openid-configuration"]


def test_metadata_issuer_must_match_and_cimd_flag_is_read():
    raw = {"issuer": "https://honest.example/", "authorization_endpoint": "a", "token_endpoint": "t",
           "client_id_metadata_document_supported": True}
    meta = oauth.parse_auth_server_metadata(raw, "https://honest.example")
    assert meta.issuer == "https://honest.example" and meta.client_id_metadata_document_supported
    with pytest.raises(RuntimeError, match="claims issuer"):
        oauth.parse_auth_server_metadata(raw, "https://attacker.example")
    assert not oauth.parse_auth_server_metadata({"issuer": "https://x.example", "authorization_endpoint": "a",
                                                 "token_endpoint": "t"}, "https://x.example").client_id_metadata_document_supported


@pytest.mark.asyncio
async def test_fetch_tries_path_insertion_first_and_validates():
    hits = []

    def h(req):
        hits.append(str(req.url))
        if str(req.url).endswith("/.well-known/oauth-authorization-server/t1"):
            return httpx.Response(404)
        if str(req.url).endswith("/.well-known/openid-configuration/t1"):
            return httpx.Response(200, json={"issuer": "https://as.example/t1", "authorization_endpoint": "a",
                                             "token_endpoint": "t"})
        return httpx.Response(500)
    real = httpx.AsyncClient
    with patch.object(oauth.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(h))):
        meta = await oauth.fetch_auth_server_metadata("https://as.example/t1")
    assert meta.issuer == "https://as.example/t1" and len(hits) == 2


# ── CIMD helpers ─────────────────────────────────────────────────────────────

def test_cimd_url_rules_and_public_hosts():
    assert oauth.valid_cimd_url(CIMD)
    for bad in ("http://pantheon.example.com/x.json", "https://pantheon.example.com", "https://pantheon.example.com/",
                "https://pantheon.example.com/a/../b.json", "https://pantheon.example.com/x.json#f",
                "https://u:p@pantheon.example.com/x.json"):
        assert not oauth.valid_cimd_url(bad), bad
    assert oauth.valid_cimd_url("https://pantheon.example.com:8443/x.json")
    assert oauth.host_looks_public("pantheon.example.com") and oauth.host_looks_public("8.8.8.8")
    for private in ("localhost", "homely.tail752d22.ts.net", "nas.local", "10.42.0.1", "192.168.1.5", "homely", "::1"):
        assert not oauth.host_looks_public(private), private


def test_cimd_client_id_derivation_and_overrides():
    with patch("config.get_settings", return_value=_settings()):
        assert oauth.cimd_client_id() == CIMD
    with patch("config.get_settings", return_value=_settings(mcp_cimd_enabled=False)):
        assert oauth.cimd_client_id() is None
    with patch("config.get_settings", return_value=_settings(public_base_url="https://homely.tail752d22.ts.net:18790")):
        assert oauth.cimd_client_id() is None   # the AS could never fetch it
    with patch("config.get_settings", return_value=_settings(public_base_url="http://pantheon.example.com")):
        assert oauth.cimd_client_id() is None   # not https
    with patch("config.get_settings", return_value=_settings(public_base_url="")):
        assert oauth.cimd_client_id() is None
        assert oauth.cimd_client_id("https://pantheon.example.com:8443") == \
            "https://pantheon.example.com:8443" + oauth.CIMD_PATH
    with patch("config.get_settings", return_value=_settings(mcp_client_id_metadata_url="https://cdn.example/p/client.json")):
        assert oauth.cimd_client_id() == "https://cdn.example/p/client.json"
    with patch("config.get_settings", return_value=_settings(mcp_client_id_metadata_url="http://cdn.example/p.json")):
        assert oauth.cimd_client_id() is None
    # Dev escape hatch: plain http is accepted only for a private host.
    with patch("config.get_settings", return_value=_settings(mcp_client_id_metadata_url="http://10.43.0.5:8000/c.json")):
        assert oauth.cimd_client_id() == "http://10.43.0.5:8000/c.json"


def test_cimd_document_shape():
    doc = oauth.cimd_document(CIMD, [CB, CB])
    assert doc == {"client_id": CIMD, "client_name": "Pantheon", "client_uri": "https://pantheon.example.com",
                   "redirect_uris": [CB], "grant_types": ["authorization_code", "refresh_token"],
                   "response_types": ["code"], "token_endpoint_auth_method": "none"}
    assert "client_secret" not in doc


# ── Registration priority in the manager ─────────────────────────────────────

def _mgr(cfg):
    from mcp_client.manager import MCPManager
    mgr = MCPManager.__new__(MCPManager)
    mgr._configs = [cfg]
    mgr._clients = {}
    mgr._save_configs = lambda: None
    return mgr


def _oauth_cfg(**extra):
    return {"name": "n", "url": "https://mcp.example/mcp", "auth_type": "oauth2", **extra}


@pytest.mark.asyncio
async def test_preregistered_wins_and_is_bound_to_the_issuer():
    cfg = _oauth_cfg(oauth_client={"client_id": "my-id", "issuer": None})
    mgr = _mgr(cfg)
    register = AsyncMock()
    with patch.object(oauth, "load_preregistered_secret", return_value="s3cret"), \
         patch.object(oauth, "register_client", register), \
         patch("config.get_settings", return_value=_settings()):
        client = await mgr._resolve_oauth_client(cfg, _as(cimd=True), CB, [])
    assert client.registration == "preregistered" and client.client_id == "my-id" \
        and client.client_secret == "s3cret" and client.token_endpoint_auth_method == "client_secret_basic"
    assert cfg["oauth_client"]["issuer"] == "https://as.example"
    register.assert_not_called()
    # Same credentials against another AS: refuse, do not silently reuse.
    with patch.object(oauth, "load_preregistered_secret", return_value=None), \
         pytest.raises(RuntimeError, match="belongs to authorization server https://as.example"):
        await mgr._resolve_oauth_client(cfg, _as("https://other.example"), CB, [])


@pytest.mark.asyncio
async def test_cimd_is_used_when_the_as_supports_it_else_dcr():
    cfg = _oauth_cfg()
    mgr = _mgr(cfg)
    dcr = AsyncMock(return_value=oauth.RegisteredClient(client_id="dcr-1", client_secret=None,
                                                        token_endpoint_auth_method="none"))
    with patch.object(oauth, "register_client", dcr), patch("config.get_settings", return_value=_settings()):
        c = await mgr._resolve_oauth_client(cfg, _as(cimd=True), CB, [])
        assert c.registration == "cimd" and c.client_id == CIMD and c.client_secret is None
        dcr.assert_not_called()
        c = await mgr._resolve_oauth_client(cfg, _as(cimd=False), CB, [])
        assert c.registration == "dcr" and c.client_id == "dcr-1"
    # CIMD advertised but Pantheon has no public address: DCR.
    with patch.object(oauth, "register_client", dcr), \
         patch("config.get_settings", return_value=_settings(public_base_url="https://homely.tail752d22.ts.net:18790")):
        c = await mgr._resolve_oauth_client(cfg, _as(cimd=True), CB, [])
        assert c.registration == "dcr"
    # Neither: a message that says what to register by hand.
    with patch("config.get_settings", return_value=_settings()), \
         pytest.raises(RuntimeError, match=f"redirect URI: {CB}"):
        await mgr._resolve_oauth_client(cfg, _as(cimd=False, dcr=False), CB, [])


@pytest.mark.asyncio
async def test_stored_registration_is_reused_per_issuer_and_redirect():
    cfg = _oauth_cfg(oauth={"client_id": "dcr-old", "issuer": "https://as.example", "redirect_uri": CB,
                            "registration": "dcr", "token_endpoint_auth_method": "client_secret_basic"})
    mgr = _mgr(cfg)
    with patch.object(oauth, "register_client", AsyncMock()) as dcr, \
         patch.object(oauth, "load_client_secret", return_value="dcr-secret"), \
         patch("config.get_settings", return_value=_settings()):
        c = await mgr._resolve_oauth_client(cfg, _as(cimd=True), CB, [])
        assert c.client_id == "dcr-old" and c.client_secret == "dcr-secret" and c.registration == "dcr"
        dcr.assert_not_called()
        # The AS moved: forget tokens + registration, start over (CIMD here).
        with patch.object(oauth, "delete_tokens") as dt, patch.object(oauth, "delete_registration") as dr:
            c = await mgr._resolve_oauth_client(cfg, _as("https://as2.example", cimd=True), CB, [])
        assert c.registration == "cimd" and dt.called and dr.called
    # A pre-2026 config without `registration` counts as DCR.
    cfg2 = _oauth_cfg(oauth={"client_id": "legacy", "issuer": "https://as.example", "redirect_uri": CB})
    mgr2 = _mgr(cfg2)
    with patch.object(oauth, "load_client_secret", return_value=None), patch("config.get_settings", return_value=_settings()):
        assert (await mgr2._resolve_oauth_client(cfg2, _as(cimd=True), CB, [])).client_id == "legacy"


@pytest.mark.asyncio
async def test_rejected_cimd_falls_back_to_dcr_next_time():
    cfg = _oauth_cfg()
    mgr = _mgr(cfg)
    with patch("config.get_settings", return_value=_settings()):
        url = oauth.build_authorize_url(name="n", as_meta=_as(cimd=True), client_id=CIMD, redirect_uri=CB,
                                        scopes=[], resource="r", registration="cimd")
    state = url.split("state=")[1].split("&")[0]
    assert mgr.note_oauth_error("nope", "invalid_client") is None
    noted = mgr.note_oauth_error(state, "invalid_client")
    assert noted == {"name": "n", "registration": "cimd", "fallback_to_dcr": True}
    assert cfg["oauth"]["cimd_rejected_issuer"] == "https://as.example"
    dcr = AsyncMock(return_value=oauth.RegisteredClient(client_id="dcr-2", client_secret=None,
                                                        token_endpoint_auth_method="none"))
    with patch.object(oauth, "register_client", dcr), patch("config.get_settings", return_value=_settings()):
        c = await mgr._resolve_oauth_client(cfg, _as(cimd=True), CB, [])
    assert c.registration == "dcr"
    # A DCR failure does not mark anything.
    with patch("config.get_settings", return_value=_settings()):
        url = oauth.build_authorize_url(name="n", as_meta=_as(), client_id="dcr-2", redirect_uri=CB,
                                        scopes=[], resource="r", registration="dcr")
    state = url.split("state=")[1].split("&")[0]
    assert mgr.note_oauth_error(state, "access_denied")["fallback_to_dcr"] is False


@pytest.mark.asyncio
async def test_start_oauth_records_registration_and_keeps_cimd_note():
    cfg = _oauth_cfg(oauth={"cimd_rejected_issuer": "https://as.example", "cimd_rejected_error": "invalid_client"})
    mgr = _mgr(cfg)
    prm = oauth.ResourceMetadata(resource="https://mcp.example/mcp", authorization_servers=["https://as.example"])
    dcr = AsyncMock(return_value=oauth.RegisteredClient(client_id="dcr-3", client_secret=None,
                                                        token_endpoint_auth_method="none"))
    with patch.object(oauth, "probe_for_oauth", AsyncMock(return_value="https://mcp.example/.well-known/prm")), \
         patch.object(oauth, "fetch_resource_metadata", AsyncMock(return_value=prm)), \
         patch.object(oauth, "fetch_auth_server_metadata", AsyncMock(return_value=_as(cimd=True))), \
         patch.object(oauth, "register_client", dcr), \
         patch("config.get_settings", return_value=_settings()):
        r = await mgr.start_oauth("n", redirect_uri=CB)
    assert r["registration"] == "dcr" and r["client_id"] == "dcr-3" and "client_id=dcr-3" in r["authorize_url"]
    assert cfg["oauth"]["registration"] == "dcr" and cfg["oauth"]["cimd_rejected_issuer"] == "https://as.example"


# ── Pre-registered credentials storage + listing ─────────────────────────────

@pytest.mark.asyncio
async def test_preregistered_credentials_are_stored_masked_and_cleared():
    cfg = _oauth_cfg()
    mgr = _mgr(cfg)
    vault: dict[str, str] = {}
    with patch.object(oauth, "save_preregistered_secret", lambda n, s: vault.__setitem__(n, s)), \
         patch.object(oauth, "load_preregistered_secret", lambda n: vault.get(n)), \
         patch.object(oauth, "delete_preregistered", lambda n: vault.pop(n, None)), \
         patch.object(oauth, "delete_tokens") as dt, \
         patch.object(oauth, "load_tokens", return_value=None):
        mgr._set_preregistered(cfg, "abc", "shh")
        assert cfg["oauth_client"] == {"client_id": "abc", "issuer": None} and vault == {"n": "shh"} and dt.called
        listed = mgr.list_connections()[0]["oauth"]
        assert listed["preregistered_client_id"] == "abc" and listed["has_preregistered_secret"] is True
        assert "shh" not in str(listed)
        mgr._set_preregistered(cfg, None, "")        # clear the secret only
        assert vault == {} and cfg["oauth_client"]["client_id"] == "abc"
        mgr._set_preregistered(cfg, "", None)        # clear the client
        assert "oauth_client" not in cfg


def test_revoke_keeps_the_registration():
    """delete_tokens no longer forgets the DCR secret: a revoked token is
    re-issued for the same registered client on the next Authorize."""
    calls = []

    class V:
        def delete_secret(self, k):
            calls.append(k)
    with patch("secrets.vault.get_vault", return_value=V()):
        oauth.delete_tokens("n")
        assert calls == ["mcp_oauth_tokens__n"]
        oauth.delete_registration("n")
        oauth.delete_preregistered("n")
    assert calls == ["mcp_oauth_tokens__n", "mcp_oauth_client_secret__n", "mcp_oauth_prereg_secret__n"]


# ── The hosted document ──────────────────────────────────────────────────────

def _app():
    from fastapi import FastAPI
    from api.mcp_oauth import router
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return app


def test_client_metadata_document_is_served_or_404():
    from starlette.testclient import TestClient
    client = TestClient(_app())
    with patch("config.get_settings", return_value=_settings()):
        r = client.get("/api/mcp/oauth/client-metadata.json")
        assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=3600"
        assert r.json()["client_id"] == CIMD and r.json()["redirect_uris"] == [CB]
        info = client.get("/api/mcp/oauth/info").json()
        assert info == {"redirect_uri": CB, "client_name": "Pantheon", "client_id_metadata_url": CIMD, "cimd_enabled": True}
    with patch("config.get_settings", return_value=_settings(mcp_cimd_enabled=False)):
        assert client.get("/api/mcp/oauth/client-metadata.json").status_code == 404
        assert client.get("/api/mcp/oauth/info").json()["cimd_enabled"] is False


def test_callback_error_marks_cimd_rejection():
    from starlette.testclient import TestClient
    from api import mcp_oauth
    cfg = _oauth_cfg()
    mgr = _mgr(cfg)
    with patch("config.get_settings", return_value=_settings()):
        url = oauth.build_authorize_url(name="n", as_meta=_as(cimd=True), client_id=CIMD, redirect_uri=CB,
                                        scopes=[], resource="r", registration="cimd")
    state = url.split("state=")[1].split("&")[0]
    with patch.object(mcp_oauth, "get_mcp_manager", return_value=mgr):
        r = TestClient(_app()).get(f"/api/mcp/oauth/callback?error=invalid_client&state={state}")
    assert r.status_code == 200 and "Dynamic Client Registration" in r.text and "click Authorize again" in r.text
    assert cfg["oauth"]["cimd_rejected_issuer"] == "https://as.example"

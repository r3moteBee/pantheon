"""Security fixes from the 2026-09-27 review (SEC-1..4, 9, 10)."""
from __future__ import annotations

import asyncio
import os
import tempfile
from types import SimpleNamespace

import httpx
import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))


# ── SEC-1: no adapter fetches a loopback URL ─────────────────────────────────

LOOPBACK_TARGETS = (
    "http://127.0.0.1:8000/api/health",
    "http://localhost:8000/api/settings",
    "http://[::1]:8000/",
    "https://www.sec.gov.127.0.0.1.nip.io/x",
)


@pytest.mark.asyncio
async def test_no_adapter_sends_a_request_to_loopback(monkeypatch):
    from sources import registry
    import sources.adapters  # noqa: F401 — registers all adapters
    from sources.base import IngestRequest

    monkeypatch.setattr(registry, "_ADAPTERS", dict(registry._ADAPTERS))
    hits: list[str] = []

    async def fake_send(self, request, *a, **k):
        host = (request.url.host or "").lower()
        if host in ("127.0.0.1", "localhost", "::1") or host.endswith(".nip.io"):
            hits.append(str(request.url))
        raise httpx.ConnectError("blocked in test", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    assert len(registry._ADAPTERS) >= 25
    for st, adapter in registry._ADAPTERS.items():
        for target in LOOPBACK_TARGETS:
            req = IngestRequest(source_type=st, identifier=target, project_id="default")
            try:
                await asyncio.wait_for(adapter.fetch(req), timeout=10)
            except Exception:
                pass
    assert hits == [], f"adapters fetched loopback URLs: {hits}"


@pytest.mark.asyncio
async def test_sec_adapter_only_fetches_sec_hosts():
    from sources.adapters.sec_edgar import _sec_get
    for bad in ("https://evil.example/data/1/", "http://www.sec.gov/x", "https://sec.gov.evil.io/"):
        with pytest.raises(ValueError, match="Not an SEC URL"):
            await _sec_get(bad)


# ── SEC-2: secrets can't be pointed at arbitrary URLs ────────────────────────

def test_search_key_names():
    from agent.search_providers import default_search_key_name, is_search_key_name
    for ok in ("brave_api_key", "brave_api_key_2", "tavily_api_key", "search_key__my-searx"):
        assert is_search_key_name(ok), ok
    for bad in ("llm_api_key", "embedding_api_key", "llm_endpoint_key__openai",
                "mcp_oauth_tokens__x", "auth_password", "", "skill_security_override_password"):
        assert not is_search_key_name(bad), bad
    assert default_search_key_name("My Searx!") == "search_key__my-searx"


def test_search_provider_refuses_foreign_vault_key(monkeypatch):
    from agent import search_providers as sp
    mgr = sp.SearchProviderManager.__new__(sp.SearchProviderManager)
    looked_up = []
    monkeypatch.setattr("secrets.vault.get_vault",
                        lambda: SimpleNamespace(get_secret=lambda k: looked_up.append(k) or "SECRET"))
    assert mgr._get_api_key({"name": "x", "api_key_vault_key": "llm_endpoint_key__openai"}) == ""
    assert looked_up == []
    assert mgr._get_api_key({"name": "x", "api_key_vault_key": "brave_api_key"}) == "SECRET"


@pytest.mark.asyncio
async def test_put_search_providers_validates_vault_key(monkeypatch):
    from fastapi import HTTPException
    from api import settings as api_settings
    stored = {}
    monkeypatch.setattr(api_settings, "get_vault",
                        lambda: SimpleNamespace(set_secret=lambda k, v: stored.__setitem__(k, v)))
    fake_mgr = SimpleNamespace(set_providers=lambda p: None, get_providers=lambda: [], get_usage=lambda: {})
    monkeypatch.setattr("agent.search_providers.get_search_manager", lambda: fake_mgr)
    bad = api_settings.SearchProvidersUpdate(providers=[api_settings.SearchProviderConfig(
        name="evil", type="generic", url="https://attacker.example", api_key_vault_key="llm_api_key")])
    with pytest.raises(HTTPException) as e:
        await api_settings.set_search_providers(bad)
    assert e.value.status_code == 400
    ok = api_settings.SearchProvidersUpdate(providers=[api_settings.SearchProviderConfig(
        name="My Searx", type="generic", url="https://s.example", api_key="k123")])
    await api_settings.set_search_providers(ok)
    assert stored == {"search_key__my-searx": "k123"}


@pytest.mark.asyncio
async def test_create_job_rejects_internal_types():
    from fastapi import HTTPException
    from api.jobs import CreateJobRequest, create_job
    for jt in ("scheduled_job", "extraction", "file_indexing", "image_extraction", "nope"):
        with pytest.raises(HTTPException) as e:
            await create_job(CreateJobRequest(job_type=jt, project_id="default", title="t"))
        assert e.value.status_code == 400


# ── SEC-3: project ids can't escape projects_dir ─────────────────────────────

def test_personality_rejects_traversal(tmp_path):
    from agent import personality
    from utils.paths import InvalidProjectId
    for bad in ("../../etc", "..", "a/b", "a\\b"):
        with pytest.raises(InvalidProjectId):
            personality.save_soul("x", project_id=bad)
        with pytest.raises(InvalidProjectId):
            personality.load_project_personality(bad)


def test_personality_api_returns_400():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    r = client.put("/api/personality/soul", params={"project_id": "../../.."}, json={"content": "pwned"})
    assert r.status_code == 400

@pytest.mark.asyncio
async def test_export_routes_reject_dotdot():
    from api.projects import export_debug
    from utils.paths import InvalidProjectId
    with pytest.raises(InvalidProjectId):
        await export_debug("..")


# ── SEC-4: public default secrets → local clients only ───────────────────────

@pytest.fixture
def defaults(monkeypatch):
    from api import auth
    cfg = SimpleNamespace(vault_master_key="dev-key-change-in-production-32x",
                          secret_key="real", auth_password="", allow_insecure_defaults=False)
    monkeypatch.setattr(auth, "get_settings", lambda: cfg)
    return cfg


def test_remote_blocked_rules(defaults):
    from api.auth import insecure_defaults, remote_blocked
    assert insecure_defaults() == ["VAULT_MASTER_KEY"]
    assert remote_blocked("127.0.0.1", {}) is None
    assert remote_blocked("::1", {}) is None
    assert "VAULT_MASTER_KEY" in remote_blocked("192.168.1.20", {})
    assert remote_blocked("127.0.0.1", {"x-forwarded-for": "203.0.113.9"})      # via proxy
    assert remote_blocked("127.0.0.1", {"x-forwarded-for": "127.0.0.1"}) is None
    defaults.allow_insecure_defaults = True
    assert remote_blocked("192.168.1.20", {}) is None
    defaults.allow_insecure_defaults = False
    defaults.vault_master_key = "a" * 64
    assert remote_blocked("192.168.1.20", {}) is None


def test_placeholder_password_counts_as_insecure(defaults):
    from api.auth import insecure_defaults
    defaults.vault_master_key = "x" * 64
    defaults.auth_password = "insert-auth-password-here"
    assert insecure_defaults() == ["AUTH_PASSWORD"]


def test_middleware_503_for_remote_with_defaults(defaults, monkeypatch):
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.get("/api/health").status_code == 200
    r = client.get("/api/health", headers={"X-Forwarded-For": "203.0.113.9"})
    assert r.status_code == 503 and "rotate_vault_key" in r.json()["error"]


# ── Vault key rotation ──────────────────────────────────────────────────────

def test_rotate_master_key(tmp_path):
    from secrets.vault import SecretsVault
    db = str(tmp_path / "vault.db")
    v = SecretsVault(db_path=db, master_key="old-key")
    v.set_secret("a", "1")
    v.set_secret("b", "two")
    assert v.rotate_master_key("new-key") == 2
    v2 = SecretsVault(db_path=db, master_key="new-key")
    assert (v2.get_secret("a"), v2.get_secret("b")) == ("1", "two")
    v2.clear_cache()
    assert SecretsVault(db_path=db, master_key="old-key").get_secret("a") is None


def test_rotate_refuses_with_wrong_current_key(tmp_path):
    from secrets.vault import SecretsVault, _cache
    db = str(tmp_path / "vault.db")
    SecretsVault(db_path=db, master_key="right").set_secret("a", "1")
    _cache.clear()
    wrong = SecretsVault(db_path=db, master_key="wrong")
    with pytest.raises(ValueError, match="can't be decrypted"):
        wrong.rotate_master_key("new")
    _cache.clear()
    assert SecretsVault(db_path=db, master_key="right").get_secret("a") == "1"


def test_rotate_script_updates_env(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "rotate_vault_key", Path(__file__).resolve().parents[3] / "scripts" / "rotate_vault_key.py")
    cwd = os.getcwd()
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    os.chdir(cwd)
    env = tmp_path / ".env"
    env.write_text("FOO=1\nVAULT_MASTER_KEY=old\nBAR=2\n")
    mod._write_env(env, "NEWKEY")
    assert env.read_text() == "FOO=1\nVAULT_MASTER_KEY=NEWKEY\nBAR=2\n"


# ── SEC-9 / SEC-10 ───────────────────────────────────────────────────────────

def test_skill_override_uses_constant_time_compare():
    import inspect
    from api import skills
    src = inspect.getsource(skills)
    assert "hmac.compare_digest" in src and "req.override_password != stored_pw" not in src


def test_mcp_request_log_hides_credentials():
    import inspect
    from mcp_client import client
    src = inspect.getsource(client.MCPClient._send_jsonrpc)
    assert 'self.api_key[:6]' not in src and 'v[:10]' not in src

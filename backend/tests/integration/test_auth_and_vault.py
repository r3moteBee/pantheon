"""Session auth (random, expiring, cookie-borne tokens) and vault v2 KDF."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))


# ── Vault ────────────────────────────────────────────────────────────────────

def _legacy_vault_db(path: str, key: str, secrets: dict[str, str]) -> None:
    from secrets.vault import SecretsVault
    f = SecretsVault._legacy_fernet(key)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE secrets (key TEXT PRIMARY KEY, encrypted_value BLOB NOT NULL, "
                 "created_at REAL NOT NULL, updated_at REAL NOT NULL)")
    for k, v in secrets.items():
        conn.execute("INSERT INTO secrets VALUES (?, ?, 0, 0)", (k, f.encrypt(v.encode())))
    conn.commit()
    conn.close()


def test_legacy_vault_is_migrated_to_v2(tmp_path):
    from secrets.vault import SecretsVault, _cache
    _cache.clear()
    db = str(tmp_path / "vault.db")
    key = "a" * 64
    _legacy_vault_db(db, key, {"api": "sk-123", "pat": "ghp_x"})

    v = SecretsVault(db_path=db, master_key=key)
    _cache.clear()
    assert v.get_secret("api") == "sk-123"
    assert v.get_secret("pat") == "ghp_x"

    # Old derivation can no longer read the rows; salt is random + stored.
    conn = sqlite3.connect(db)
    blob = conn.execute("SELECT encrypted_value FROM secrets WHERE key='api'").fetchone()[0]
    salt = conn.execute("SELECT value FROM vault_meta WHERE key='kdf_salt'").fetchone()[0]
    conn.close()
    from cryptography.fernet import InvalidToken
    with pytest.raises(InvalidToken):
        SecretsVault._legacy_fernet(key).decrypt(blob)
    assert len(bytes.fromhex(salt)) == 16

    # Re-opening doesn't migrate again and still reads.
    _cache.clear()
    assert SecretsVault(db_path=db, master_key=key).get_secret("api") == "sk-123"


def test_v2_uses_full_key_length(tmp_path):
    """Keys sharing a 32-char prefix must not decrypt each other's vaults
    (v1 truncated to 32 bytes)."""
    from secrets.vault import SecretsVault, _cache
    _cache.clear()
    db = str(tmp_path / "v.db")
    SecretsVault(db_path=db, master_key="p" * 32 + "A").set_secret("x", "1")
    _cache.clear()
    assert SecretsVault(db_path=db, master_key="p" * 32 + "B").get_secret("x") is None


# ── Sessions ─────────────────────────────────────────────────────────────────

@pytest.fixture
def auth_env(tmp_path):
    cfg = SimpleNamespace(
        auth_password="pw", secret_key="s", db_dir=tmp_path, auth_session_days=30,
        cors_origins_list=[], allowed_hosts="",
    )
    with patch("api.auth.get_settings", return_value=cfg):
        yield cfg


def test_session_tokens_are_random_and_revocable(auth_env):
    from api import auth
    t1, t2 = auth.create_session(), auth.create_session()
    assert t1 != t2 and len(t1) >= 40
    assert auth.token_is_valid(t1)
    auth.revoke_session(t1)
    assert not auth.token_is_valid(t1)
    assert auth.token_is_valid(t2)


def test_session_expires(auth_env):
    from api import auth
    t = auth.create_session()
    with patch("api.auth.time.time", return_value=time.time() + 31 * 86400):
        assert not auth.token_is_valid(t)


def test_password_change_invalidates_sessions(auth_env):
    from api import auth
    t = auth.create_session()
    auth_env.auth_password = "new-pw"
    assert not auth.token_is_valid(t)


def test_query_string_token_is_not_accepted(auth_env):
    from api import auth
    t = auth.create_session()
    assert auth.request_token({}, {}) == ""
    assert auth.request_token({"authorization": f"Bearer {t}"}, {}) == t
    assert auth.request_token({}, {auth.SESSION_COOKIE: t}) == t


def test_login_sets_httponly_strict_cookie(auth_env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.auth import router, SESSION_COOKIE, _failures
    _failures.clear()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    r = TestClient(app).post("/api/auth/login", json={"password": "pw"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert SESSION_COOKIE in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie

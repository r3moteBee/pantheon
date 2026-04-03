"""Fernet-encrypted secrets vault backed by SQLite.

The vault master key is resolved in order:
1. VAULT_MASTER_KEY environment variable
2. Docker secret at /run/secrets/vault_master_key
3. /etc/pantheon/vault.key file (root-owned, mode 600)
4. Fallback to config.py default (dev mode only — logs a warning)
"""
from __future__ import annotations
import base64
import hashlib
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

# In-memory cache: {key: (value, timestamp)}
_cache: dict[str, tuple[str, float]] = {}
CACHE_TTL = 300  # seconds

# Standard external key file locations
_SYSTEM_KEY_FILE = Path("/etc/pantheon/vault.key")
_DOCKER_SECRET_FILE = Path("/run/secrets/vault_master_key")


def _resolve_master_key() -> str:
    """Resolve the vault master key from the most secure available source.

    Priority:
      1. VAULT_MASTER_KEY env var (set by systemd EnvironmentFile, etc.)
      2. Docker secret at /run/secrets/vault_master_key
      3. /etc/pantheon/vault.key file (root-owned, outside user space)
      4. Config default (dev fallback — insecure)
    """
    # 1. Environment variable (preferred for production)
    env_key = os.environ.get("VAULT_MASTER_KEY", "").strip()
    if env_key and env_key != "dev-key-change-in-production-32x":
        logger.info("Vault master key loaded from environment variable")
        return env_key

    # 2. Docker secret (preferred for container environments)
    if _DOCKER_SECRET_FILE.exists():
        try:
            key = _DOCKER_SECRET_FILE.read_text(encoding="utf-8").strip()
            if key:
                logger.info("Vault master key loaded from Docker secret")
                return key
        except Exception as e:
            logger.error("Error reading Docker secret: %s", e)

    # 3. System key file (preferred for bare-metal Linux deploys)
    if _SYSTEM_KEY_FILE.exists():
        try:
            key = _SYSTEM_KEY_FILE.read_text(encoding="utf-8").strip()
            if key:
                logger.info("Vault master key loaded from %s", _SYSTEM_KEY_FILE)
                return key
        except PermissionError:
            logger.error(
                "Cannot read %s — check file ownership and permissions "
                "(should be root:root 600, readable by the service user)",
                _SYSTEM_KEY_FILE,
            )
        except Exception as e:
            logger.error("Error reading %s: %s", _SYSTEM_KEY_FILE, e)

    # 3. Fallback to config default (dev mode only)
    from config import get_settings
    settings = get_settings()
    key = settings.vault_master_key
    if key == "dev-key-change-in-production-32x":
        logger.warning(
            "⚠️  Using default dev vault key — NOT SAFE FOR PRODUCTION. "
            "Set VAULT_MASTER_KEY env var or create %s",
            _SYSTEM_KEY_FILE,
        )
    else:
        logger.info("Vault master key loaded from config/env")
    return key


class SecretsVault:
    """Encrypted key-value store for sensitive configuration."""

    def __init__(self, db_path: str | None = None, master_key: str | None = None):
        from config import get_settings
        settings = get_settings()
        self.db_path = db_path or settings.vault_db_path
        resolved_key = master_key or _resolve_master_key()
        self._fernet = self._init_fernet(resolved_key)
        self._init_db()

    def _init_fernet(self, master_key: str) -> Fernet:
        """Derive a Fernet key from the master key using PBKDF2."""
        salt = b"agent-harness-vault-salt-v1"
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100_000,
        )
        key_bytes = master_key.encode("utf-8")[:32].ljust(32, b"\0")
        derived = kdf.derive(key_bytes)
        return Fernet(base64.urlsafe_b64encode(derived))

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS secrets (
                    key TEXT PRIMARY KEY,
                    encrypted_value BLOB NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            conn.commit()

    def set_secret(self, key: str, value: str) -> None:
        """Encrypt and store a secret."""
        encrypted = self._fernet.encrypt(value.encode("utf-8"))
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO secrets (key, encrypted_value, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    encrypted_value = excluded.encrypted_value,
                    updated_at = excluded.updated_at
            """, (key, encrypted, now, now))
            conn.commit()
        _cache[key] = (value, time.time())
        logger.info(f"Secret set: {key}")

    def get_secret(self, key: str, default: str | None = None) -> str | None:
        """Decrypt and return a secret value."""
        if key in _cache:
            value, ts = _cache[key]
            if time.time() - ts < CACHE_TTL:
                return value
            else:
                del _cache[key]

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT encrypted_value FROM secrets WHERE key = ?", (key,)
            ).fetchone()

        if not row:
            return default

        try:
            value = self._fernet.decrypt(row[0]).decode("utf-8")
            _cache[key] = (value, time.time())
            return value
        except InvalidToken:
            logger.error(f"Failed to decrypt secret: {key}")
            return default

    def delete_secret(self, key: str) -> bool:
        """Delete a secret. Returns True if it existed."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM secrets WHERE key = ?", (key,))
            conn.commit()
        if key in _cache:
            del _cache[key]
        return cursor.rowcount > 0

    def list_secrets(self) -> list[str]:
        """Return all secret keys (never values)."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT key FROM secrets ORDER BY key").fetchall()
        return [r[0] for r in rows]

    def has_secret(self, key: str) -> bool:
        """Check if a secret exists without decrypting."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM secrets WHERE key = ? LIMIT 1", (key,)
            ).fetchone()
        return row is not None

    def clear_cache(self) -> None:
        """Clear the in-memory cache."""
        _cache.clear()


_vault_instance: SecretsVault | None = None


def get_vault() -> SecretsVault:
    global _vault_instance
    if _vault_instance is None:
        _vault_instance = SecretsVault()
    return _vault_instance

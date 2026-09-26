"""Fernet-encrypted secrets vault backed by SQLite."""
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

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# In-memory cache: {key: (value, timestamp)}
_cache: dict[str, tuple[str, float]] = {}
CACHE_TTL = 300  # seconds


class SecretsVault:
    """Encrypted key-value store for sensitive configuration."""

    # v2 key derivation: PBKDF2-SHA256 over the FULL master key with a
    # random per-vault salt (stored in vault_meta) and 600k iterations.
    # v1 truncated the key to 32 bytes and used a fixed salt shared by
    # every install; v1 vaults are re-encrypted to v2 on first open.
    _KDF_VERSION = "2"
    _V2_ITERATIONS = 600_000

    def __init__(self, db_path: str | None = None, master_key: str | None = None):
        self.db_path = db_path or settings.vault_db_path
        self._master_key = master_key or settings.vault_master_key
        self._init_db()
        self._fernet = self._load_or_migrate_fernet()

    @staticmethod
    def _legacy_fernet(master_key: str) -> Fernet:
        """v1 derivation — kept only to read/migrate old vaults."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"agent-harness-vault-salt-v1",
            iterations=100_000,
        )
        key_bytes = master_key.encode("utf-8")[:32].ljust(32, b"\0")
        return Fernet(base64.urlsafe_b64encode(kdf.derive(key_bytes)))

    @classmethod
    def _v2_fernet(cls, master_key: str, salt: bytes) -> Fernet:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=cls._V2_ITERATIONS,
        )
        return Fernet(base64.urlsafe_b64encode(kdf.derive(master_key.encode("utf-8"))))

    def _get_meta(self, conn, key: str) -> str | None:
        row = conn.execute("SELECT value FROM vault_meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _load_or_migrate_fernet(self) -> Fernet:
        with self._connect() as conn:
            version = self._get_meta(conn, "kdf_version")
            salt_hex = self._get_meta(conn, "kdf_salt")
            if version == self._KDF_VERSION and salt_hex:
                return self._v2_fernet(self._master_key, bytes.fromhex(salt_hex))

            salt = os.urandom(16)
            new = self._v2_fernet(self._master_key, salt)
            legacy = self._legacy_fernet(self._master_key)
            rows = conn.execute("SELECT key, encrypted_value FROM secrets").fetchall()
            migrated = failed = 0
            try:
                conn.execute("BEGIN IMMEDIATE")
                for key, blob in rows:
                    try:
                        plain = legacy.decrypt(blob)
                    except InvalidToken:
                        # Can't read it with this master key either way —
                        # leave the row untouched rather than destroying it.
                        failed += 1
                        continue
                    conn.execute(
                        "UPDATE secrets SET encrypted_value = ? WHERE key = ?",
                        (new.encrypt(plain), key),
                    )
                    migrated += 1
                conn.execute(
                    "INSERT OR REPLACE INTO vault_meta (key, value) VALUES ('kdf_salt', ?)",
                    (salt.hex(),),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO vault_meta (key, value) VALUES ('kdf_version', ?)",
                    (self._KDF_VERSION,),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        if rows:
            logger.warning(
                "Vault key derivation upgraded to v2: %d secret(s) re-encrypted%s",
                migrated, f", {failed} unreadable (left as-is)" if failed else "",
            )
        _cache.clear()
        return new

    def _connect(self) -> sqlite3.Connection:
        from db_utils import apply_sqlite_pragmas, ClosingConnection
        conn = sqlite3.connect(self.db_path)
        apply_sqlite_pragmas(conn)
        return ClosingConnection(conn)  # type: ignore

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS secrets (
                    key TEXT PRIMARY KEY,
                    encrypted_value BLOB NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vault_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            conn.commit()

    def set_secret(self, key: str, value: str) -> None:
        """Encrypt and store a secret."""
        encrypted = self._fernet.encrypt(value.encode("utf-8"))
        now = time.time()
        with self._connect() as conn:
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

        with self._connect() as conn:
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
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM secrets WHERE key = ?", (key,))
            conn.commit()
        if key in _cache:
            del _cache[key]
        return cursor.rowcount > 0

    def list_secrets(self) -> list[str]:
        """Return all secret keys (never values)."""
        with self._connect() as conn:
            rows = conn.execute("SELECT key FROM secrets ORDER BY key").fetchall()
        return [r[0] for r in rows]

    def clear_cache(self) -> None:
        """Clear the in-memory cache."""
        _cache.clear()


_vault_instance: SecretsVault | None = None


def get_vault() -> SecretsVault:
    global _vault_instance
    if _vault_instance is None:
        _vault_instance = SecretsVault()
    return _vault_instance

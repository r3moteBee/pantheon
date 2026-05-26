"""Shared SQLite connection helpers.

Centralizes the PRAGMA set we want on every long-lived store. Call
``apply_sqlite_pragmas(conn)`` immediately after ``sqlite3.connect`` for
any store opened in the request/worker path.

- ``journal_mode=WAL`` — readers don't block the writer and vice versa.
  This is a file-level setting that persists in the database header, so
  re-applying on every connection is a cheap no-op.
- ``synchronous=NORMAL`` — pairs with WAL, trades a tiny crash-window
  for a real throughput win. Safe for Pantheon's single-process workload.
- ``foreign_keys=ON`` — SQLite defaults to off; we want enforcement.
"""
from __future__ import annotations

import sqlite3


def apply_sqlite_pragmas(conn: sqlite3.Connection) -> None:
    """Apply Pantheon's standard PRAGMA set to a SQLite connection."""
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")


class ClosingConnection:
    """Wrapper that automatically closes a sqlite3 connection on exit from context block."""
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self) -> sqlite3.Connection:
        self.conn.__enter__()
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            return self.conn.__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.conn.close()

    def __getattr__(self, name):
        return getattr(self.conn, name)


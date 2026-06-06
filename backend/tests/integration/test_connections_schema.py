"""Regression: _connect() must create the github_connections +
project_repo_bindings tables on a fresh database.

A refactor that added the ClosingConnection wrapper once placed the
return above the CREATE TABLE statements, leaving the DDL unreachable —
fresh installs then 500'd with "no such table: github_connections" on
the GitHub connections page.
"""
import os

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")

from unittest.mock import patch


def test_connect_creates_schema_on_fresh_db(tmp_path):
    import api.connections as connections
    with patch.object(connections, "_db_path",
                      return_value=str(tmp_path / "sources.db")):
        with connections._connect() as conn:
            tables = {r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
    assert "github_connections" in tables
    assert "project_repo_bindings" in tables


def test_connect_is_idempotent(tmp_path):
    import api.connections as connections
    with patch.object(connections, "_db_path",
                      return_value=str(tmp_path / "sources.db")):
        for _ in range(2):  # second call re-runs DDL + migration fast-path
            with connections._connect() as conn:
                conn.execute("SELECT COUNT(*) FROM github_connections").fetchone()

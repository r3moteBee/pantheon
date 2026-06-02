"""Integration tests for system update endpoints (/system/update/check and /system/update/execute).

Run: pytest backend/tests/integration/test_system_updater.py -v
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")
os.makedirs("/tmp/pantheon-tests-data/db", exist_ok=True)

from main import app
from config import get_settings
from api.auth import compute_token


@pytest.fixture
def clean_settings():
    """Clear lru_cache for settings before and after tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    return TestClient(app)


@patch("subprocess.run")
def test_check_update_no_auth(mock_run, clean_settings, client):
    """Test checking for updates returns all correct fields including auth_enabled=False."""
    # Ensure AUTH_PASSWORD is empty
    if "AUTH_PASSWORD" in os.environ:
        del os.environ["AUTH_PASSWORD"]

    # Mock subprocess git fetches
    mock_run.return_value = MagicMock(returncode=0, stdout="a1b2c3d Commit message\n")

    r = client.get("/api/system/update/check")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "update_available" in body
    assert "message" in body
    assert "commits" in body
    assert body["auth_enabled"] is False


@patch("subprocess.run")
def test_check_update_with_auth(mock_run, clean_settings, client):
    """Test checking for updates returns auth_enabled=True when AUTH_PASSWORD is set."""
    os.environ["AUTH_PASSWORD"] = "test-admin-pass"
    settings = get_settings()
    token = compute_token(settings.auth_password, settings.secret_key)
    
    # Mock subprocess git fetches
    mock_run.return_value = MagicMock(returncode=0, stdout="")

    headers = {"Authorization": f"Bearer {token}"}
    r = client.get("/api/system/update/check", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["auth_enabled"] is True


@patch("subprocess.Popen")
def test_execute_update_confirm_gate_no_auth(mock_popen, clean_settings, client):
    """When auth is disabled, confirm field is checked. Rejecting confirm gives 400."""
    if "AUTH_PASSWORD" in os.environ:
        del os.environ["AUTH_PASSWORD"]

    # No confirm -> 400
    r = client.post("/api/system/update/execute", json={"confirm": False})
    assert r.status_code == 400
    assert "Confirmation required" in r.json()["detail"]

    # With confirm -> 200 (Success)
    r = client.post("/api/system/update/execute", json={"confirm": True})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    mock_popen.assert_called_once()


@patch("subprocess.Popen")
def test_execute_update_password_gate_with_auth(mock_popen, clean_settings, client):
    """When auth is enabled, password field is checked. Invalid password gives 401."""
    os.environ["AUTH_PASSWORD"] = "super-secret"
    settings = get_settings()
    token = compute_token(settings.auth_password, settings.secret_key)
    headers = {"Authorization": f"Bearer {token}"}
    
    # Missing password -> 401
    r = client.post("/api/system/update/execute", json={}, headers=headers)
    assert r.status_code == 401

    # Invalid password -> 401
    r = client.post("/api/system/update/execute", json={"password": "wrong-password"}, headers=headers)
    assert r.status_code == 401

    # Valid password -> 200
    r = client.post("/api/system/update/execute", json={"password": "super-secret"}, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    mock_popen.assert_called_once()

"""Tests for the LLM endpoints + role mapping subsystem."""
from __future__ import annotations
import os

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")
os.makedirs("/tmp/pantheon-tests-data/db", exist_ok=True)

import pytest


def test_saved_endpoint_validates_api_type():
    from llm_config.models import SavedEndpoint
    e = SavedEndpoint(name="primary", base_url="https://api.openai.com/v1", api_type="openai")
    assert e.name == "primary"
    with pytest.raises(ValueError):
        SavedEndpoint(name="x", base_url="https://x", api_type="bogus")


def test_saved_endpoint_slugifies_name():
    from llm_config.models import SavedEndpoint
    e = SavedEndpoint(name="My Local Ollama!", base_url="http://localhost:11434", api_type="ollama")
    assert e.name == "my-local-ollama"


def test_saved_endpoint_rejects_empty_name():
    from llm_config.models import SavedEndpoint
    with pytest.raises(ValueError):
        SavedEndpoint(name="", base_url="https://x", api_type="openai")


def test_role_assignment_validates_role_name():
    from llm_config.models import RoleAssignment
    r = RoleAssignment(role="chat", endpoint="primary", model="gpt-4o")
    assert r.role == "chat"
    with pytest.raises(ValueError):
        RoleAssignment(role="bogus", endpoint="primary", model="x")

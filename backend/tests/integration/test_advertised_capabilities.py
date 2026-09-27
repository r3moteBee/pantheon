"""Model capabilities learned from what endpoints publish in /models."""
from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from llm_config.models import EndpointWithKey  # noqa: E402
from llm_config.probe import capabilities_from_entry as caps  # noqa: E402


@pytest.fixture
def vault(monkeypatch, tmp_path):
    from secrets import vault as _v
    fresh = _v.SecretsVault(db_path=str(tmp_path / "vault.db"), master_key="k")
    monkeypatch.setattr(_v, "_vault_instance", fresh)
    monkeypatch.setattr(_v, "_cache", {})
    fresh.set_secret("llm_config_migrated_v1", "true")
    return fresh


def test_openrouter_shape():
    assert caps({"id": "m", "context_length": 131072,
                 "supported_parameters": ["temperature", "tools", "tool_choice"],
                 "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]}}) == {
        "tools": True, "vision": True, "image_gen": False, "context_window": 131072}


def test_capabilities_list_shape():
    assert caps({"id": "m", "capabilities": ["completion", "tools"], "max_model_len": 32768}) == {
        "tools": True, "vision": False, "embedding": False, "image_gen": False, "context_window": 32768}
    assert caps({"capabilities": {"function_calling": True, "vision": False}}) == {"tools": True, "vision": False}


def test_litellm_lmstudio_ollama_show_shapes():
    assert caps({"supports_function_calling": True, "supports_vision": True, "max_input_tokens": 8000}) == {
        "tools": True, "vision": True, "context_window": 8000}
    assert caps({"type": "embeddings", "max_context_length": 512}) == {"embedding": True, "context_window": 512}
    assert caps({"capabilities": ["completion", "vision", "tools"],
                 "model_info": {"gemma3.context_length": 131072}})["context_window"] == 131072


def test_plain_openai_entry_advertises_nothing():
    assert caps({"id": "gpt-4o", "object": "model", "owned_by": "openai"}) == {}


@pytest.mark.asyncio
async def test_probe_records_and_profile_prefers_endpoint(vault, monkeypatch):
    from llm_config import probe, store
    store.save_endpoint(EndpointWithKey(name="homely", base_url="http://10.0.0.5:8080/v1",
                                        api_type="openai", api_key=""))

    async def fake_get(url, *, headers, timeout=15):
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": [
            {"id": "gemma2-custom", "capabilities": ["completion", "tools"], "context_length": 65536},
            {"id": "plain-model"},
        ]})
    monkeypatch.setattr(probe, "_async_get", fake_get)
    assert store.get_profile("homely", "gemma2-custom").tools is False      # name guess
    assert await store.refresh_advertised() == {"homely": 1}
    p = store.get_profile("homely", "gemma2-custom")
    assert (p.tools, p.context_window, p.source) == (True, 65536, "endpoint")
    assert store.get_profile("homely", "plain-model").source == "unknown"
    # a user edit still wins
    from llm_config.models import ModelProfile
    store.set_profiles({"homely/gemma2-custom": ModelProfile(tools=False)})
    assert store.get_profile("homely", "gemma2-custom").source == "user"
    # deleting the endpoint forgets what it advertised
    store.delete_endpoint("homely")
    assert store._advertised() == {}


@pytest.mark.asyncio
async def test_ollama_probe_uses_api_show(monkeypatch):
    from llm_config import probe

    async def fake_get(url, *, headers, timeout=15):
        assert url.endswith("/api/tags")
        return SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: {"models": [{"name": "gemma4:26b"}]})

    async def fake_post(url, *, json, headers, timeout=15):
        assert url.endswith("/api/show") and json == {"model": "gemma4:26b"}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "capabilities": ["completion", "tools", "vision"],
            "model_info": {"gemma4.context_length": 131072}})
    monkeypatch.setattr(probe, "_async_get", fake_get)
    monkeypatch.setattr(probe, "_async_post", fake_post)
    r = await probe.probe_models(base_url="http://h:11434/v1", api_type="ollama", api_key="")
    assert r.capabilities["gemma4:26b"]["tools"] is True
    assert r.capabilities["gemma4:26b"]["context_window"] == 131072

"""Task-class routing: routes/migration/inheritance, fallback, profiles,
usage logging, and image generation."""
from __future__ import annotations

import base64
import os
import tempfile
from unittest.mock import AsyncMock, patch

import httpx
import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from llm_config.models import EndpointWithKey, RoleAssignment, RouteEntry  # noqa: E402


@pytest.fixture
def vault(monkeypatch, tmp_path):
    from secrets import vault as _v
    fresh = _v.SecretsVault(db_path=str(tmp_path / "vault.db"), master_key="k")
    monkeypatch.setattr(_v, "_vault_instance", fresh)
    monkeypatch.setattr(_v, "_cache", {})
    fresh.set_secret("llm_config_migrated_v1", "true")
    from models import provider
    provider.reset_provider()
    yield fresh
    provider.reset_provider()


@pytest.fixture
def usage_db(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from llm_config import usage
    monkeypatch.setattr(usage, "get_settings", lambda: SimpleNamespace(db_dir=tmp_path))
    return usage


def _endpoints(*names):
    from llm_config.store import save_endpoint
    for n in names:
        save_endpoint(EndpointWithKey(name=n, base_url=f"https://{n}.test/v1",
                                      api_type="openai", api_key=f"key-{n}"))


# ── Store ────────────────────────────────────────────────────────────────────

def test_routes_seeded_from_legacy_roles(vault):
    from llm_config.store import get_routes, set_role_mapping
    _endpoints("main", "cheap")
    set_role_mapping([
        RoleAssignment(role="chat", endpoint="main", model="gpt-4o"),
        RoleAssignment(role="prefill", endpoint="cheap", model="gpt-4o-mini"),
        RoleAssignment(role="embed", endpoint="main", model="text-embedding-3-small"),
    ])
    r = get_routes()
    assert r["agent"] == [{"endpoint": "main", "model": "gpt-4o"}]
    assert r["extract"] == [{"endpoint": "main", "model": "gpt-4o"}]   # ingest keeps chat model
    assert r["summarize"] == [{"endpoint": "cheap", "model": "gpt-4o-mini"}]
    assert r["embed"] == [{"endpoint": "main", "model": "text-embedding-3-small"}]
    assert r["code"] == [] and r["image_gen"] == []


def test_inheritance_and_fallback_order(vault):
    from llm_config.store import resolve_route, set_routes
    _endpoints("a", "b")
    set_routes({
        "agent": [RouteEntry(endpoint="a", model="m1"), RouteEntry(endpoint="b", model="m2")],
        "summarize": [RouteEntry(endpoint="b", model="small")],
    })
    assert [(r.endpoint_name, r.model) for r in resolve_route("code")] == [("a", "m1"), ("b", "m2")]
    assert [r.model for r in resolve_route("extract")] == ["small"]   # extract -> summarize
    assert resolve_route("vision") == []
    assert resolve_route("code")[1].api_key == "key-b"


def test_set_routes_validation_and_warnings(vault):
    from llm_config.store import set_routes
    _endpoints("a")
    with pytest.raises(ValueError, match="unknown endpoint"):
        set_routes({"agent": [RouteEntry(endpoint="zzz", model="m")]})
    with pytest.raises(ValueError, match="at most 1"):
        set_routes({"embed": [RouteEntry(endpoint="a", model="e1"), RouteEntry(endpoint="a", model="e2")]})
    warnings = set_routes({
        "agent": [RouteEntry(endpoint="a", model="text-embedding-3-small")],
        "image_gen": [RouteEntry(endpoint="a", model="gpt-image-1")],
    })
    assert any("Agent" in w and "tools" in w for w in warnings)
    assert not any("Image generation" in w for w in warnings)


def test_profiles_guess_and_user_override(vault):
    from llm_config.models import ModelProfile
    from llm_config.store import delete_profile, get_profile, set_profiles
    p = get_profile("a", "claude-haiku-4-5")
    assert p.tools and p.vision and p.tier == "fast" and p.source == "known"
    assert get_profile("a", "nomic-embed-text").embedding
    assert get_profile("a", "my-private-finetune").source == "unknown"
    set_profiles({"a/my-private-finetune": ModelProfile(tools=True, tier="frontier")})
    q = get_profile("a", "my-private-finetune")
    assert q.tools and q.tier == "frontier" and q.source == "user"
    delete_profile("a", "my-private-finetune")
    assert get_profile("a", "my-private-finetune").source == "unknown"


def test_deleting_endpoint_scrubs_routes(vault):
    from llm_config.store import delete_endpoint, get_routes, set_routes
    _endpoints("a", "b")
    set_routes({"agent": [RouteEntry(endpoint="a", model="m"), RouteEntry(endpoint="b", model="n")]})
    delete_endpoint("a")
    assert get_routes()["agent"] == [{"endpoint": "b", "model": "n"}]


# ── RoutedProvider fallback ─────────────────────────────────────────────────

def _prov(model):
    from models.provider import ModelProvider
    return ModelProvider(base_url=f"https://{model}.test/v1", api_key="k", model=model)


@pytest.mark.asyncio
async def test_complete_falls_back_on_429_and_logs(usage_db):
    from models.provider import LLMHTTPError, RoutedProvider
    p1, p2 = _prov("m1"), _prov("m2")
    p1.chat_complete = AsyncMock(side_effect=LLMHTTPError(429, "rate limited"))
    p2.chat_complete = AsyncMock(return_value={"content": "hi", "tool_calls": [],
                                               "usage": {"prompt_tokens": 5, "completion_tokens": 2}})
    rp = RoutedProvider("agent", [(p1, "a"), (p2, "b")])
    out = await rp.chat_complete([{"role": "user", "content": "x"}])
    assert out["content"] == "hi"
    rows = {r["model"]: r for r in usage_db.summary(1)["rows"]}
    assert rows["m1"]["errors"] == 1
    assert rows["m2"]["served_as_fallback"] == 1 and rows["m2"]["prompt_tokens"] == 5


@pytest.mark.asyncio
async def test_complete_does_not_fall_back_on_400(usage_db):
    from models.provider import LLMHTTPError, RoutedProvider
    p1, p2 = _prov("m1"), _prov("m2")
    p1.chat_complete = AsyncMock(side_effect=LLMHTTPError(400, "bad request"))
    p2.chat_complete = AsyncMock()
    with pytest.raises(LLMHTTPError):
        await RoutedProvider("agent", [(p1, "a"), (p2, "b")]).chat_complete([])
    p2.chat_complete.assert_not_called()


def _stream(events):
    async def gen(*a, **k):
        for e in events:
            yield e
    return gen


@pytest.mark.asyncio
async def test_stream_falls_back_only_before_first_chunk(usage_db):
    from models.provider import RoutedProvider
    p1, p2 = _prov("m1"), _prov("m2")
    p1.chat = _stream([{"type": "error", "message": "down", "status": 503}])
    p2.chat = _stream([{"type": "text_delta", "content": "ok"}, {"type": "done"}])
    events = [e async for e in RoutedProvider("agent", [(p1, "a"), (p2, "b")]).chat([], stream=True)]
    assert [e["type"] for e in events] == ["text_delta", "done"]

    p3, p4 = _prov("m3"), _prov("m4")
    p3.chat = _stream([{"type": "text_delta", "content": "par"},
                       {"type": "error", "message": "reset", "status": "network"}])
    p4.chat = _stream([{"type": "text_delta", "content": "SHOULD NOT APPEAR"}])
    events = [e async for e in RoutedProvider("agent", [(p3, "a"), (p4, "b")]).chat([], stream=True)]
    assert [e["type"] for e in events] == ["text_delta", "error"]


def test_get_provider_for_classes(vault):
    from llm_config.store import set_routes
    from models import provider
    _endpoints("a")
    set_routes({"agent": [RouteEntry(endpoint="a", model="gpt-4o")],
                "embed": [RouteEntry(endpoint="a", model="text-embedding-3-small")]})
    provider.reset_provider()
    assert provider.get_provider_for("code").model == "gpt-4o"          # inherits agent
    assert provider.get_provider_for("image_gen") is None
    emb = provider.get_embedding_provider()
    assert isinstance(emb, provider.ModelProvider) and emb.embedding_model == "text-embedding-3-small"
    assert provider.get_provider().base_url == "https://a.test/v1"


# ── Image generation ────────────────────────────────────────────────────────

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.mark.asyncio
async def test_provider_generate_image_decodes_b64_and_url():
    import json as _json
    p = _prov("gpt-image-1")

    def handler(req):
        body = _json.loads(req.content)
        assert body["model"] == "gpt-image-1" and body["size"] == "512x512"
        return httpx.Response(200, json={"data": [
            {"b64_json": base64.b64encode(_PNG).decode()},
            {"url": "https://cdn.test/img.png"},
        ]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fake_get = AsyncMock(return_value=httpx.Response(200, content=b"URLIMG",
                                                     request=httpx.Request("GET", "https://cdn.test")))
    with patch("utils.http.shared_client", return_value=client), \
         patch("utils.net.safe_http_get", fake_get):
        imgs = await p.generate_image("a cat", size="512x512", n=2)
    await client.aclose()
    assert imgs == [_PNG, b"URLIMG"]


@pytest.mark.asyncio
async def test_generate_image_tool_saves_artifact_and_displays(tmp_path):
    from agent.tools import execute_tool
    from artifacts.store import ArtifactStore

    store = ArtifactStore(db_path=str(tmp_path / "a.db"), blobs_dir=tmp_path / "blobs")
    fake = AsyncMock()
    fake.generate_image = AsyncMock(return_value=[_PNG])
    fake.model = "gpt-image-1"
    with patch("models.provider.get_provider_for", return_value=fake), \
         patch("artifacts.store.get_store", return_value=store):
        res = await execute_tool("generate_image", {"prompt": "A red fox, watercolor"},
                                 None, project_id="p1", session_id="s1")
    assert "[DISPLAY:artifact://" in res
    art_id = res.split("[DISPLAY:artifact://")[1].split("]")[0]
    a = store.get(art_id)
    assert a["content_type"] == "image/png"
    assert "images/generated/" in a["path"] and a["path"].endswith("a-red-fox-watercolor.png")


@pytest.mark.asyncio
async def test_generate_image_tool_unconfigured():
    from agent.tools import execute_tool
    with patch("models.provider.get_provider_for", return_value=None):
        res = await execute_tool("generate_image", {"prompt": "x"}, None)
    assert "isn't configured" in res


# ── API ──────────────────────────────────────────────────────────────────────

def test_routes_api_round_trip(vault):
    from fastapi.testclient import TestClient
    from main import app
    _endpoints("a")
    c = TestClient(app)
    r = c.put("/api/llm/routes", json={"routes": {
        "agent": [{"endpoint": "a", "model": "gpt-4o"}],
        "image_gen": [{"endpoint": "a", "model": "gpt-image-1"}],
    }})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["routes"]["image_gen"] == [{"endpoint": "a", "model": "gpt-image-1"}]
    assert body["profiles"]["a/gpt-image-1"]["image_gen"] is True
    assert c.get("/api/llm/task-classes").json()["task_classes"][0]["id"] == "agent"
    assert c.put("/api/llm/routes", json={"routes": {"bogus": []}}).status_code == 422


# ── Chat-completions image mode (Abacus RouteLLM / OpenRouter style) ────────

@pytest.mark.asyncio
async def test_generate_image_falls_back_to_chat_modalities_and_remembers():
    import json as _json
    from models import provider as pmod
    p = _prov("gemini-3.1-flash-image")
    pmod._IMAGE_API_STYLE.clear()
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path.endswith("/images/generations"):
            return httpx.Response(404, json={"error": {"message": "Not Found"}})
        body = _json.loads(req.content)
        assert body["modalities"] == ["image", "text"]
        assert body["image_config"] == {"num_images": 1, "aspect_ratio": "3:2"}
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "here you go",
            "images": [{"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(_PNG).decode()}}],
        }}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("utils.http.shared_client", return_value=client):
        assert await p.generate_image("a puppy", size="1536x1024") == [_PNG]
        assert await p.generate_image("a kitten", size="1536x1024") == [_PNG]
    await client.aclose()
    # Second call goes straight to chat mode.
    assert calls == ["/v1/images/generations", "/v1/chat/completions", "/v1/chat/completions"]


@pytest.mark.asyncio
async def test_chat_image_mode_explains_text_only_reply():
    from models import provider as pmod
    p = _prov("text-only-model")
    pmod._IMAGE_API_STYLE[(p.base_url, p.model)] = "chat"

    def handler(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": "I can't draw"}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("utils.http.shared_client", return_value=client):
        with pytest.raises(RuntimeError, match="no image.*can't draw"):
            await p.generate_image("x")
    await client.aclose()


def test_image_model_names_detected():
    from llm_config.known_models import guess_profile
    for m in ("qwen_image_edit", "gemini-3.1-flash-image", "nano-banana-pro", "flux-2-pro"):
        assert guess_profile(m).image_gen, m
    assert not guess_profile("gpt-5.4-nano").image_gen


# ── Named size presets (fal-style "square_hd", …) ───────────────────────────

def test_size_conversions():
    from models.provider import _aspect_ratio, _pixel_size, _size_preset
    assert _aspect_ratio("16:9") == "16:9" and _aspect_ratio("square_hd") == "1:1"
    assert _pixel_size("landscape_16_9") == "1024x576" and _pixel_size("1536x1024") == "1536x1024"
    assert _size_preset("1024x1024") == "square_hd"
    assert _size_preset("512x512") == "square"
    assert _size_preset("1536x1024") == "landscape_4_3"
    assert _size_preset("1024x1792") == "portrait_16_9"
    assert _size_preset("16:9", ["square_hd", "portrait_4_3"]) == "square_hd"


@pytest.mark.asyncio
async def test_chat_image_mode_retries_with_named_preset_and_remembers():
    import json as _json
    from models import provider as pmod
    p = _prov("qwen_image_edit")
    pmod._IMAGE_API_STYLE[(p.base_url, p.model)] = "chat"
    pmod._IMAGE_WANTS_PRESET.clear()
    seen = []

    def handler(req):
        cfg = _json.loads(req.content)["image_config"]
        seen.append(cfg)
        if cfg.get("image_size") != "landscape_16_9":
            return httpx.Response(422, json={"detail": [{
                "loc": ["body", "image_size"],
                "msg": "Input should be 'square_hd', 'square', 'portrait_4_3', "
                       "'portrait_16_9', 'landscape_4_3' or 'landscape_16_9'"}]})
        return httpx.Response(200, json={"choices": [{"message": {"images": [{"image_url": {
            "url": "data:image/png;base64," + base64.b64encode(_PNG).decode()}}]}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("utils.http.shared_client", return_value=client):
        assert await p.generate_image("a skyline", size="16:9") == [_PNG]
        assert await p.generate_image("a skyline", size="1792x1024") == [_PNG]
    await client.aclose()
    assert len(seen) == 3  # rejected, retried with preset, then preset directly
    assert seen[1]["image_size"] == "landscape_16_9" and seen[1]["aspect_ratio"] == "16:9"
    assert pmod._IMAGE_WANTS_PRESET[(p.base_url, p.model)] == "image_size"


@pytest.mark.asyncio
async def test_images_api_retries_with_named_preset():
    import json as _json
    from models import provider as pmod
    p = _prov("flux-dev")
    pmod._IMAGE_API_STYLE.clear()
    pmod._IMAGE_WANTS_PRESET.clear()
    sizes = []

    def handler(req):
        assert req.url.path.endswith("/images/generations")
        size = _json.loads(req.content)["size"]
        sizes.append(size)
        if size != "square_hd":
            return httpx.Response(400, json={"error": {"message": "size must be one of square_hd, landscape_4_3"}})
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(_PNG).decode()}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("utils.http.shared_client", return_value=client):
        assert await p.generate_image("a cat") == [_PNG]
    await client.aclose()
    assert sizes == ["1024x1024", "square_hd"]


@pytest.mark.asyncio
async def test_generate_image_tool_accepts_presets_and_ratios():
    from agent.tools import execute_tool
    fake = AsyncMock()
    fake.generate_image = AsyncMock(return_value=[_PNG])
    fake.model = "m"
    with patch("models.provider.get_provider_for", return_value=fake):
        for size in ("square_hd", "16:9", "1024x1024"):
            res = await execute_tool("generate_image", {"prompt": "x", "size": size}, None)
            assert "invalid size" not in res, (size, res)
        res = await execute_tool("generate_image", {"prompt": "x", "size": "big; rm -rf"}, None)
        assert "invalid size" in res

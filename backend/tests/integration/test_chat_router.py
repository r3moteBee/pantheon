"""Phase 2 routing: the per-turn chat router (llm_config/router.py) and its
wiring into api/chat.py."""
from __future__ import annotations

import os
import tempfile
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from llm_config.models import EndpointWithKey, RouteEntry  # noqa: E402


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
    from llm_config import usage
    monkeypatch.setattr(usage, "get_settings", lambda: SimpleNamespace(db_dir=tmp_path))
    return usage


def _setup(**classes):
    """Endpoint "ep" + routes. Default agent: qwen3-max (tools, no vision, 32k)."""
    from llm_config.store import save_endpoint, set_routes
    save_endpoint(EndpointWithKey(name="ep", base_url="https://ep.test/v1",
                                  api_type="openai", api_key="k"))
    routes = {"agent": [RouteEntry(endpoint="ep", model="qwen3-max")]}
    for cls, model in classes.items():
        routes[cls] = [RouteEntry(endpoint="ep", model=model)]
    set_routes(routes)


def _sid():
    return str(uuid.uuid4())


async def _decide(msg, sid=None, **kw):
    from llm_config.router import decide
    return await decide(msg, session_id=sid or _sid(), **kw)


@pytest.mark.asyncio
async def test_nothing_extra_configured_stays_on_agent(vault):
    _setup()
    for msg in ("hi!", "```py\nprint(1)\n```", "[image: a.png (artifact:abc)] what is this"):
        d = await _decide(msg)
        assert d.task_class == "agent", msg
        assert d.model == "qwen3-max"


@pytest.mark.asyncio
async def test_quick_only_for_short_no_tool_messages(vault):
    _setup(quick="gpt-5.4-nano")
    d = await _decide("thanks! what's the capital of France?")
    assert (d.task_class, d.rule, d.model) == ("quick", "quick", "gpt-5.4-nano")
    for msg in ("search my artifacts for Nvidia", "schedule a daily ingest", "see https://x.com",
                "x" * 500):
        assert (await _decide(msg)).task_class == "agent", msg


@pytest.mark.asyncio
async def test_code_signal_is_sticky_for_a_few_turns(vault):
    _setup(code="claude-sonnet-4-5", quick="gpt-5.4-nano")
    sid = _sid()
    d = await _decide("why does this fail?\nTraceback (most recent call last):\n  File x", sid)
    assert (d.task_class, d.rule) == ("code", "code")
    # "ok" would be quick, but the conversation is in code mode.
    rules = [(await _decide("ok and then?", sid)).rule for _ in range(4)]
    assert rules == ["sticky", "sticky", "sticky", "quick"]


@pytest.mark.asyncio
async def test_image_goes_to_a_tool_and_vision_capable_model(vault):
    # vision-class model can't do tools; long_context (gemini) can do both.
    _setup(vision="gemini-3.1-flash-image", long_context="gemini-3-pro")
    d = await _decide("[image: chart.png (artifact:a1)] explain this chart")
    assert d.rule == "vision" and d.model == "gemini-3-pro"


@pytest.mark.asyncio
async def test_image_without_capable_model_explains(vault):
    _setup(vision="gemini-3.1-flash-image")
    d = await _decide("[image: chart.png (artifact:a1)] explain")
    assert d.task_class == "agent"
    assert "no tool-capable vision model" in d.public()["reason"]


@pytest.mark.asyncio
async def test_long_history_moves_to_long_context(vault):
    _setup(long_context="gemini-3-pro")
    assert (await _decide("continue", history_chars=10_000)).task_class == "agent"
    d = await _decide("continue", history_chars=200_000)   # ~62k tokens vs 32k window
    assert (d.task_class, d.rule) == ("long_context", "long")


@pytest.mark.asyncio
async def test_long_context_must_actually_be_larger(vault):
    _setup(long_context="qwen3-max")   # same 32k window
    d = await _decide("continue", history_chars=200_000)
    assert d.task_class == "agent" and "not larger" in d.public()["reason"]


@pytest.mark.asyncio
async def test_override_and_model_command(vault):
    from llm_config.router import get_override, parse_model_command, set_override
    _setup(code="claude-sonnet-4-5")
    sid = _sid()
    set_override(sid, "code")
    d = await _decide("hello", sid)
    assert (d.task_class, d.rule) == ("code", "override")
    set_override(sid, "auto")
    assert get_override(sid) is None
    with pytest.raises(ValueError):
        set_override(sid, "gpt-4")
    assert parse_model_command("/model code fix it") == ("code", "fix it")
    assert parse_model_command("/model") == (None, "")
    assert parse_model_command("/models are cool") is None
    assert parse_model_command("hello /model code") is None


@pytest.mark.asyncio
async def test_pin_to_unusable_class_falls_through(vault):
    from llm_config.router import set_override
    _setup()
    sid = _sid()
    set_override(sid, "quick")
    d = await _decide("hello", sid)
    assert d.task_class == "agent" and "pinned quick unusable" in d.public()["reason"]


@pytest.mark.asyncio
async def test_disabled_router_still_honours_pin(vault):
    from llm_config.router import set_config, set_override
    _setup(quick="gpt-5.4-nano", code="claude-sonnet-4-5")
    set_config({"enabled": False})
    assert (await _decide("hi")).rule == "disabled"
    sid = _sid()
    set_override(sid, "code")
    assert (await _decide("hi", sid)).task_class == "code"
    with pytest.raises(ValueError):
        set_config({"bogus": 1})


@pytest.mark.asyncio
async def test_non_tool_model_is_never_chosen(vault):
    _setup(quick="text-embedding-3-small")
    d = await _decide("hi there")
    assert d.task_class == "agent"


@pytest.mark.asyncio
async def test_skill_model_class(vault):
    _setup(code="claude-sonnet-4-5")
    skill = SimpleNamespace(manifest=SimpleNamespace(pantheon=SimpleNamespace(model_class="code")))
    d = await _decide("do the thing", skill=skill)
    assert (d.task_class, d.rule) == ("code", "skill")


def test_skill_manifest_accepts_model_class():
    from skills.models import SkillManifest
    m = SkillManifest(name="x", pantheon={"model_class": "code"})
    assert m.pantheon.model_class == "code"
    assert SkillManifest(name="y").pantheon.model_class is None


@pytest.mark.asyncio
async def test_classifier_breaks_ties_and_times_out_safely(vault):
    from llm_config import router
    _setup(code="claude-sonnet-4-5")
    router.set_config({"classifier": True})
    fake = SimpleNamespace(chat_complete=AsyncMock(return_value={"content": "Code."}))
    with patch("models.provider.get_provider_for", return_value=fake):
        d = await _decide("I need help restructuring how the ingest module handles retries " * 5)
    assert (d.task_class, d.rule) == ("code", "classifier")

    async def boom(*a, **k):
        raise RuntimeError("down")
    fake2 = SimpleNamespace(chat_complete=boom)
    with patch("models.provider.get_provider_for", return_value=fake2):
        d = await _decide("a different ambiguous request about the project structure " * 5)
    assert d.task_class == "agent"


# ── Wiring into api/chat.py ────────────────────────────────────────────────

class _FakeAgent:
    def __init__(self):
        self.provider = "default-provider"
        self.seen_provider = None

    def _get_working_messages(self):
        return [{"role": "user", "content": "earlier"}]

    async def chat(self, message, stream=True):
        from models.provider import _note_served
        self.seen_provider = self.provider
        yield {"type": "text_delta", "content": "hi"}
        _note_served("ep", "fallback-model")
        yield {"type": "done", "full_response": "hi"}


@pytest.mark.asyncio
async def test_stream_turn_routes_emits_and_logs(vault, usage_db):
    from api.chat import _stream_turn
    _setup(quick="gpt-5.4-nano")
    agent = _FakeAgent()
    sent = []

    async def send(ev):
        sent.append(ev)

    sid = _sid()
    full, route = await _stream_turn(agent, "thanks!", sid, send)
    assert full == "hi"
    assert sent[0]["type"] == "model_route" and sent[0]["task_class"] == "quick"
    assert agent.seen_provider is not None and agent.seen_provider != "default-provider"
    assert agent.seen_provider.task_class == "quick"
    done = sent[-1]
    assert done["type"] == "done" and done["route"]["served_model"] == "fallback-model"
    assert route["model"] == "gpt-5.4-nano"
    rows = usage_db.decision_summary(1)["rows"]
    assert rows == [{"task_class": "quick", "rule": "quick", "turns": 1,
                     "last_ts": rows[0]["last_ts"], "fell_back": 1}]


def test_model_command_reply():
    from api.chat import _model_command_reply
    sid = _sid()
    reply, rest, pinned = _model_command_reply(sid, "/model code fix the bug")
    assert pinned == "code" and rest == "fix the bug" and "pinned" in reply
    reply, rest, pinned = _model_command_reply(sid, "/model")
    assert pinned == "code" and rest == ""
    reply, rest, pinned = _model_command_reply(sid, "/model auto")
    assert pinned == "auto"
    reply, rest, _ = _model_command_reply(sid, "/model nonsense")
    assert "unknown chat class" in reply and rest == ""
    assert _model_command_reply(sid, "hello")[0] is None


@pytest.mark.asyncio
async def test_router_api(vault):
    from api.llm_endpoints import RouterConfigPayload, read_router, update_router
    _setup(quick="gpt-5.4-nano", vision="gemini-3.1-flash-image")
    view = await read_router()
    classes = {c["name"]: c for c in view["classes"]}
    assert classes["agent"]["usable"] and classes["quick"]["usable"]
    assert not classes["code"]["usable"] and not classes["vision"]["usable"]
    assert view["config"]["enabled"] is True
    view = await update_router(RouterConfigPayload(classifier=True, quick_max_chars=100))
    assert view["config"]["classifier"] is True and view["config"]["quick_max_chars"] == 100


# ── Short messages that still need the agent (router loop, 2026-10-01) ──────

@pytest.mark.parametrize("msg", [
    "What's the weather in Lisbon right now?", "Is Tailscale 1.102 out yet?", "What's Nvidia's stock price?",
    "Who won the Champions League final this year?", "Any news about the Artemis III launch?",
    "Set a reminder to call mum at 6", "Email this to Sarah", "Plan a 3-day itinerary for Porto",
])
def test_time_sensitive_actions_and_plans_are_not_quick(msg):
    from llm_config.router import looks_quick
    assert not looks_quick(msg, 240)


@pytest.mark.parametrize("msg", [
    # measured fine on quick: small talk, trivia, arithmetic, the user's own memory (recall supplies it)
    "hi", "thanks!", "What's the capital of France?", "Is 7919 a prime number?",
    "What's 17% of 2,340 plus VAT at 23%?", "What's my car's license plate?", "Did I mention a speech earlier?",
    "Remind me what we decided about the offsite", "What was the name of that book I liked?",
])
def test_small_talk_trivia_arithmetic_and_memory_stay_quick(msg):
    from llm_config.router import looks_quick
    assert looks_quick(msg, 240)

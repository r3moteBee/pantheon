"""MCP elicitation (spec 2026-07-28 MRTR): the client puts a modern server's
``input_required`` requests to the user through an elicitor and retries the
call with their answers; without an elicitor the capability is not declared
and the request is reported to the model. Also the answer validator and the
chat-side broker (``mcp_client/elicitation.py``), and the WebSocket wiring
(a turn keeps streaming while the socket receives the form answer)."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from mcp_client import elicitation as el  # noqa: E402
from tests.integration.test_mcp_dual_era import FakeServer, make_client, serve  # noqa: E402

FORM = {"method": "elicitation/create", "params": {
    "mode": "form", "message": "Please provide your GitHub username",
    "requestedSchema": {"type": "object", "properties": {
        "name": {"type": "string", "minLength": 2},
        "notify": {"type": "boolean", "default": True}}, "required": ["name"]}}}
URL = {"method": "elicitation/create", "params": {
    "mode": "url", "message": "Connect your account", "url": "https://connect.example.com/start?x=1"}}


def _elicit_server(requests, *, state="s1", final_text="done"):
    """A modern FakeServer whose ``echo`` asks ``requests`` until it gets
    inputResponses, then answers with what it received."""
    srv = FakeServer("modern")
    calls: list[dict] = []

    def tool(args, body):
        params = body["params"]
        calls.append(params)
        if "inputResponses" not in params:
            res = {"resultType": "input_required", "inputRequests": requests}
            if state is not None:
                res["requestState"] = state
            return res
        return {"resultType": "complete", "content": [{"type": "text", "text": final_text}],
                "structuredContent": {"got": params["inputResponses"], "state": params.get("requestState")}}

    srv.handlers["echo"] = tool
    return srv, calls


# ── Client: the MRTR round trip ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_form_elicitation_answered_and_retried_with_state():
    srv, calls = _elicit_server({"login": FORM})
    prompts = []

    async def elicit(prompt):
        prompts.append(prompt)
        return {"login": {"action": "accept", "content": {"name": "octocat", "notify": False}}}

    c = make_client()
    with serve(srv):
        res = await c.call_tool("echo", {"text": "hi"}, elicit=elicit)
    assert not res["is_error"]
    # The model learns that the user answered, and which fields, never the values.
    assert res["text"].startswith("[The user filled in the server's form (name, notify).]")
    assert "octocat" not in res["text"] and res["text"].endswith("done")
    assert res["structured"] == {"got": {"login": {"action": "accept", "content": {"name": "octocat", "notify": False}}},
                                 "state": "s1"}
    # Two independent tools/call requests: new id, same arguments, state echoed only on the retry.
    assert len(calls) == 2 and "requestState" not in calls[0] and calls[1]["requestState"] == "s1"
    ids = [b["id"] for m, _h, b in srv.requests if m == "tools/call"]
    assert len(set(ids)) == 2
    for m, _h, b in srv.requests:
        caps = b["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"]
        assert caps == {"elicitation": {"form": {}, "url": {}},
                        "extensions": {"io.modelcontextprotocol/tasks": {}}}, m
    # What the UI was asked.
    assert len(prompts) == 1 and prompts[0].connection == "t" and prompts[0].tool == "echo" and prompts[0].round == 1
    pub = prompts[0].public()
    assert pub["requests"][0]["key"] == "login" and pub["requests"][0]["mode"] == "form"
    assert pub["requests"][0]["requestedSchema"]["required"] == ["name"]


@pytest.mark.asyncio
async def test_no_requeststate_means_none_is_sent_back():
    srv, calls = _elicit_server({"login": FORM}, state=None)

    async def elicit(prompt):
        return {"login": {"action": "accept", "content": {"name": "mona"}}}

    c = make_client()
    with serve(srv):
        res = await c.call_tool("echo", {}, elicit=elicit)
    assert not res["is_error"] and "requestState" not in calls[1]


@pytest.mark.asyncio
async def test_decline_and_cancel_are_forwarded_and_summarised():
    srv, calls = _elicit_server({"login": FORM, "link": URL})

    async def elicit(prompt):
        assert [r.mode for r in prompt.requests] == ["form", "url"]
        assert prompt.requests[1].public()["host"] == "connect.example.com"
        return {"login": {"action": "decline", "content": {"name": "ignored"}}, "link": {"action": "cancel"}}

    c = make_client()
    with serve(srv):
        res = await c.call_tool("echo", {}, elicit=elicit)
    assert calls[1]["inputResponses"] == {"login": {"action": "decline", "content": {"name": "ignored"}},
                                          "link": {"action": "cancel"}}
    assert "declined to fill in the server's form" in res["text"]
    assert "dismissed the request to open the link at connect.example.com" in res["text"]


@pytest.mark.asyncio
async def test_url_accept_then_server_asks_again_until_done():
    """A URL flow: the server keeps answering input_required while the
    out-of-band step is pending; each round is put to the user again."""
    srv = FakeServer("modern")
    rounds = {"n": 0}

    def tool(args, body):
        rounds["n"] += 1
        if rounds["n"] < 3:
            return {"resultType": "input_required", "requestState": f"r{rounds['n']}", "inputRequests": {"link": URL}}
        return {"resultType": "complete", "content": [{"type": "text", "text": "connected"}]}

    srv.handlers["echo"] = tool
    seen = []

    async def elicit(prompt):
        seen.append((prompt.round, prompt.requests[0].url))
        return {"link": {"action": "accept"}}

    c = make_client()
    with serve(srv):
        res = await c.call_tool("echo", {}, elicit=elicit)
    assert res["text"].endswith("connected") and seen == [(1, URL["params"]["url"]), (2, URL["params"]["url"])]
    assert "opened the link at connect.example.com; then opened the link at connect.example.com" in res["text"]
    states = [b["params"].get("requestState") for m, _h, b in srv.requests if m == "tools/call"]
    assert states == [None, "r1", "r2"]


@pytest.mark.asyncio
async def test_round_cap_stops_a_server_that_never_finishes():
    srv = FakeServer("modern")
    srv.handlers["echo"] = lambda a, b: {"resultType": "input_required", "requestState": "x", "inputRequests": {"link": URL}}
    asked = {"n": 0}

    async def elicit(prompt):
        asked["n"] += 1
        return {"link": {"action": "accept"}}

    c = make_client()
    with serve(srv):
        res = await c.call_tool("echo", {}, elicit=elicit)
    assert res["is_error"] and "kept asking" in res["text"] and asked["n"] == el.MAX_INPUT_ROUNDS


@pytest.mark.asyncio
async def test_elicitor_returning_none_cancels_every_request():
    srv, calls = _elicit_server({"login": FORM, "link": URL})

    async def elicit(prompt):
        return None   # timed out / user gone

    c = make_client()
    with serve(srv):
        res = await c.call_tool("echo", {}, elicit=elicit)
    assert calls[1]["inputResponses"] == {"login": {"action": "cancel"}, "link": {"action": "cancel"}}
    assert "dismissed the server's form" in res["text"]


@pytest.mark.asyncio
async def test_without_elicitor_capability_is_omitted_and_request_reported():
    srv, calls = _elicit_server({"login": FORM})
    c = make_client()
    with serve(srv):
        res = await c.call_tool("echo", {})
    assert res["is_error"] and len(calls) == 1
    assert "needs user input" in res["text"] and "fill in name, notify" in res["text"] and "web chat" in res["text"]
    call = [b for m, _h, b in srv.requests if m == "tools/call"][0]
    assert call["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"] == {
        "extensions": {"io.modelcontextprotocol/tasks": {}}}


@pytest.mark.asyncio
async def test_undeclared_request_kinds_are_refused_not_answered():
    sampling = {"method": "sampling/createMessage", "params": {"messages": [], "maxTokens": 10}}
    srv, calls = _elicit_server({"q": sampling})
    elicit = AsyncMock()
    c = make_client()
    with serve(srv):
        res = await c.call_tool("echo", {}, elicit=elicit)
    assert res["is_error"] and "sampling/createMessage" in res["text"] and len(calls) == 1
    elicit.assert_not_called()


@pytest.mark.asyncio
async def test_state_only_input_required_is_retried_once_then_given_up():
    srv = FakeServer("modern")
    srv.handlers["echo"] = lambda a, b: {"resultType": "input_required", "requestState": "again"}
    c = make_client()
    with serve(srv):
        res = await c.call_tool("echo", {}, elicit=AsyncMock())
    assert res["is_error"] and "asked for nothing" in res["text"]
    assert sum(1 for m, _h, _b in srv.requests if m == "tools/call") == 2


# ── parse_requests / validation ──────────────────────────────────────────────

def test_parse_requests_rejects_bad_urls_and_nested_schemas():
    with pytest.raises(el.UnsupportedInputRequest):
        el.parse_requests({"x": {"method": "elicitation/create", "params": {"mode": "url", "url": "javascript:alert(1)"}}})
    with pytest.raises(el.UnsupportedInputRequest):
        el.parse_requests({"x": {"method": "elicitation/create", "params": {"mode": "url", "url": "file:///etc/passwd"}}})
    with pytest.raises(el.UnsupportedInputRequest):
        el.parse_requests({"x": {"method": "elicitation/create", "params": {"requestedSchema": {"type": "array"}}}})
    with pytest.raises(el.UnsupportedInputRequest):
        el.parse_requests({})
    # mode omitted = form; no schema = empty form.
    reqs = el.parse_requests({"x": {"method": "elicitation/create", "params": {"message": "ok?"}}})
    assert reqs[0].mode == "form" and reqs[0].schema == {"type": "object", "properties": {}}


SCHEMA = {"type": "object", "required": ["name", "age"], "properties": {
    "name": {"type": "string", "minLength": 2, "maxLength": 5},
    "email": {"type": "string", "format": "email"},
    "age": {"type": "integer", "minimum": 18, "maximum": 120},
    "score": {"type": "number", "maximum": 1},
    "ok": {"type": "boolean"},
    "color": {"type": "string", "oneOf": [{"const": "#f00", "title": "Red"}, {"const": "#0f0", "title": "Green"}]},
    "size": {"type": "string", "enum": ["S", "M"]},
    "tags": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "string", "enum": ["a", "b", "c"]}},
    "when": {"type": "string", "format": "date"},
    "odd": {"type": "object"},   # outside the spec subset: passed through
}}


def test_validate_content_accepts_a_good_answer_and_drops_unknown_fields():
    clean, errors = el.validate_content(SCHEMA, {
        "name": "mona", "email": "m@example.com", "age": 30, "score": 0.5, "ok": True, "color": "#0f0",
        "size": "M", "tags": ["a", "c"], "when": "2026-10-25", "odd": {"k": 1}, "extra": "dropped"})
    assert errors == {} and "extra" not in clean and clean["tags"] == ["a", "c"] and clean["odd"] == {"k": 1}


def test_validate_content_reports_every_bad_field():
    clean, errors = el.validate_content(SCHEMA, {
        "name": "x", "email": "nope", "age": 17.5, "score": 2, "ok": "yes", "color": "#00f",
        "size": "L", "tags": ["a", "b", "c"], "when": "yesterday"})
    assert set(errors) == {"name", "email", "age", "score", "ok", "color", "size", "tags", "when"}
    assert errors["age"] == "must be a whole number" and errors["tags"] == "pick at most 2"
    assert clean == {}
    # Missing required / empty string.
    _clean, errors = el.validate_content(SCHEMA, {"name": "", "age": 20})
    assert errors == {"name": "is required"}


def test_validate_response_actions():
    req = el.ElicitRequest(key="k", mode="form", message="", schema=SCHEMA)
    assert el.validate_response(req, {"action": "decline", "content": {"name": "x"}}) == ({"action": "decline"}, {})
    assert el.validate_response(req, {"action": "nope"})[1] == {"": "action must be one of accept, decline, cancel"}
    assert el.validate_response(req, None)[1] == {"": "missing answer"}
    url = el.ElicitRequest(key="u", mode="url", message="", url="https://x.example/")
    assert el.validate_response(url, {"action": "accept", "content": {"leak": 1}}) == ({"action": "accept"}, {})
    prompt = el.ElicitationPrompt(connection="c", tool="t", requests=[req, url])
    clean, errors = el.validate_responses(prompt, {"k": {"action": "accept", "content": {"name": "mona", "age": 30}}})
    assert clean == {"k": {"action": "accept", "content": {"name": "mona", "age": 30}}}
    assert errors == {"u": {"": "missing answer"}}


# ── Broker ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_broker_asks_rejects_invalid_then_accepts():
    sent = []

    async def send(ev):
        sent.append(ev)

    broker = el.ElicitationBroker(send, timeout=5)
    prompt = el.ElicitationPrompt(connection="c", tool="t",
                                  requests=[el.ElicitRequest(key="k", mode="form", message="m", schema=SCHEMA)])
    task = asyncio.ensure_future(broker.ask(prompt))
    await asyncio.sleep(0)
    assert sent[0]["type"] == "elicitation_request" and sent[0]["elicitation_id"] == prompt.id
    assert sent[0]["connection"] == "c" and sent[0]["requests"][0]["requestedSchema"] == SCHEMA and sent[0]["timeout_s"] == 5
    assert not await broker.answer("bogus", {})
    assert sent[-1]["type"] == "warning"
    assert not await broker.answer(prompt.id, {"k": {"action": "accept", "content": {"name": "x", "age": 1}}})
    assert sent[-1]["type"] == "elicitation_invalid" and set(sent[-1]["errors"]["k"]) == {"name", "age"}
    assert broker.pending == 1
    assert await broker.answer(prompt.id, {"k": {"action": "accept", "content": {"name": "mona", "age": 30}}})
    assert await task == {"k": {"action": "accept", "content": {"name": "mona", "age": 30}}}
    assert broker.pending == 0


@pytest.mark.asyncio
async def test_broker_timeout_and_close_return_none():
    sent = []

    async def send(ev):
        sent.append(ev)

    broker = el.ElicitationBroker(send, timeout=0.05)
    prompt = el.ElicitationPrompt(connection="c", tool="t",
                                  requests=[el.ElicitRequest(key="u", mode="url", message="", url="https://x.example/")])
    assert await broker.ask(prompt) is None
    assert sent[-1] == {"type": "elicitation_closed", "elicitation_id": prompt.id, "reason": "timeout"}
    broker = el.ElicitationBroker(send, timeout=60)
    task = asyncio.ensure_future(broker.ask(prompt))
    await asyncio.sleep(0)
    broker.close()
    assert await task is None and broker.pending == 0


# ── The agent drops the elicitor outside the web chat ───────────────────────

def test_agent_core_keeps_elicitor_only_for_interactive_web_turns():
    from agent.core import AgentCore
    e = object()
    assert AgentCore(provider=MagicMock(), interactive=True, channel="web", elicit=e).elicit is e
    assert AgentCore(provider=MagicMock(), interactive=True, channel="telegram", elicit=e).elicit is None
    assert AgentCore(provider=MagicMock(), interactive=False, channel=None, elicit=e).elicit is None


# ── WebSocket wiring ─────────────────────────────────────────────────────────

def _ws_app():
    from fastapi import FastAPI
    from api import chat as chat_mod
    app = FastAPI()
    app.include_router(chat_mod.router)
    app.add_api_websocket_route("/ws/chat", chat_mod.websocket_chat)
    return app


class _FakeAgent:
    """Stands in for AgentCore: calls the elicitor mid-turn like an MCP tool would."""

    def __init__(self, elicit):
        self.elicit = elicit
        self.working_memory = []

    async def chat(self, message, stream=True):
        yield {"type": "tool_call", "name": "mcp_srv_echo", "args": {}, "id": "tc1"}
        prompt = el.ElicitationPrompt(connection="srv", tool="echo", requests=[
            el.ElicitRequest(key="login", mode="form", message="Who?", schema=FORM["params"]["requestedSchema"])])
        answer = await self.elicit(prompt)
        yield {"type": "tool_result", "name": "mcp_srv_echo", "tool_id": "tc1", "is_error": False,
               "result": json.dumps(answer)}
        yield {"type": "text_delta", "content": "ok"}
        yield {"type": "done", "full_response": "ok"}


def test_websocket_turn_streams_a_form_and_takes_the_answer():
    from starlette.testclient import TestClient
    from api import chat as chat_mod

    async def fake_build_agent(*, elicit=None, **kw):
        return _FakeAgent(elicit)

    memory = MagicMock()
    memory.episodic.save_message = AsyncMock()
    with patch.object(chat_mod, "_build_agent", fake_build_agent), \
         patch.object(chat_mod, "get_provider_for", return_value=MagicMock()), \
         patch.object(chat_mod, "create_memory_manager", return_value=memory), \
         patch.object(chat_mod, "_route_turn", AsyncMock(return_value=None)), \
         patch.object(chat_mod, "_finish_route", return_value=None), \
         patch("api.auth.authorize_websocket", AsyncMock(return_value=True)), \
         patch("utils.chat_settings.skill_discovery", return_value="off"):
        client = TestClient(_ws_app())
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_text(json.dumps({"message": "go", "session_id": "s1", "project_id": "default"}))
            events = []
            while True:
                ev = ws.receive_json()
                events.append(ev)
                if ev["type"] == "elicitation_request":
                    break
            assert [e["type"] for e in events] == ["session_start", "tool_call", "elicitation_request"]
            ask = events[-1]
            assert ask["connection"] == "srv" and ask["tool"] == "echo" and ask["requests"][0]["key"] == "login"
            # A ping is still answered while the turn waits on the form.
            ws.send_text(json.dumps({"type": "ping"}))
            assert ws.receive_json() == {"type": "pong"}
            # A second message is refused rather than run concurrently (a
            # warning, not an error: the UI keeps the running turn).
            ws.send_text(json.dumps({"message": "another"}))
            assert ws.receive_json()["type"] == "warning"
            # A bad answer is bounced; the form stays open.
            ws.send_text(json.dumps({"type": "elicitation_response", "elicitation_id": ask["elicitation_id"],
                                     "responses": {"login": {"action": "accept", "content": {"name": "x"}}}}))
            bad = ws.receive_json()
            assert bad["type"] == "elicitation_invalid" and "name" in bad["errors"]["login"]
            ws.send_text(json.dumps({"type": "elicitation_response", "elicitation_id": ask["elicitation_id"],
                                     "responses": {"login": {"action": "accept", "content": {"name": "mona"}}}}))
            result = ws.receive_json()
            assert result["type"] == "tool_result"
            assert json.loads(result["result"]) == {"login": {"action": "accept", "content": {"name": "mona"}}}
            assert ws.receive_json()["type"] == "text_delta"
            assert ws.receive_json()["type"] == "done"
    saved = [c.kwargs.get("role") for c in memory.episodic.save_message.call_args_list]
    assert saved == ["user", "assistant"]

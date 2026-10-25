"""MCP elicitation (spec 2026-07-28, client side).

A modern server that needs something from the user answers ``tools/call``
with ``resultType: "input_required"`` whose ``inputRequests`` map holds
``elicitation/create`` requests (the multi round-trip pattern). The client
shows them, collects the answers and retries the call with ``inputResponses``
(and the echoed ``requestState``). Two modes:

* **form** - a flat object of primitive fields (string / number / integer /
  boolean / enum / multi-select enum) the user fills in. The answers go to
  the server only; the model is told which fields were answered, never the
  values.
* **url** - a link the user opens out of band (credentials, OAuth, payment).
  The client shows the full URL and host, never fetches it, and opens it
  only on an explicit click; ``accept`` means "I consented and opened it".

This module is transport-agnostic: ``parse_requests`` / ``validate_response``
are pure, ``ElicitationBroker`` turns a ``send(event)`` coroutine plus
``answer()`` calls into the awaitable the MCP client needs. ``api/chat.py``
wires a broker to each chat WebSocket; channels without a UI (bots, jobs,
REST) pass no elicitor, so the client omits the capability and the server
must do without.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# What this client declares in `_meta.io.modelcontextprotocol/clientCapabilities`
# on a request whose caller can show the user a form or a link.
ELICITATION_CAPABILITY: dict[str, Any] = {"form": {}, "url": {}}

ACTIONS = ("accept", "decline", "cancel")
# A server MAY answer the retry with another input_required (a URL flow
# that is still pending, a field it still wants). Past this many rounds in
# one tool call the client stops and tells the model.
MAX_INPUT_ROUNDS = 8

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SAFE_SCHEMES = ("http", "https")


class UnsupportedInputRequest(Exception):
    """An inputRequests entry this client did not declare (sampling, roots)
    or an elicitation it cannot render."""


@dataclass
class ElicitRequest:
    """One parsed ``elicitation/create`` request."""
    key: str
    mode: str                      # "form" | "url"
    message: str
    schema: dict[str, Any] | None = None   # form: requestedSchema
    url: str | None = None                 # url: the link to open

    def public(self) -> dict[str, Any]:
        """What the UI gets."""
        out: dict[str, Any] = {"key": self.key, "mode": self.mode, "message": self.message}
        if self.mode == "url":
            out["url"] = self.url
            out["host"] = url_host(self.url or "")
        else:
            out["requestedSchema"] = self.schema or {"type": "object", "properties": {}}
        return out


@dataclass
class ElicitationPrompt:
    """Everything the UI needs to ask the user, plus bookkeeping."""
    connection: str
    tool: str
    requests: list[ElicitRequest]
    round: int = 1
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def public(self) -> dict[str, Any]:
        return {
            "elicitation_id": self.id,
            "connection": self.connection,
            "tool": self.tool,
            "round": self.round,
            "requests": [r.public() for r in self.requests],
        }


@dataclass
class ConfirmPrompt:
    """Approval the chat asks for before a tool that changes data on an MCP
    server runs (tool annotations: destructiveHint / readOnlyHint)."""
    connection: str
    tool: str
    arguments: dict[str, Any]
    title: str = ""
    description: str = ""
    annotations: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def public(self) -> dict[str, Any]:
        return {"confirm_id": self.id, "connection": self.connection, "tool": self.tool,
                "arguments": self.arguments, "title": self.title, "description": self.description[:500],
                "annotations": {k: v for k, v in self.annotations.items()
                                if k in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")}}


# The awaitable the manager calls before a tool that needs approval: True runs it.
Confirmer = Callable[[ConfirmPrompt], Awaitable[bool]]


# The awaitable the MCP client calls: returns {key: {"action", "content"?}}
# for every request, or None when the user is gone / out of time (-> cancel).
Elicitor = Callable[[ElicitationPrompt], Awaitable[dict[str, dict[str, Any]] | None]]


# ── Parsing what the server asked ──────────────────────────────────────────

def url_host(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def parse_requests(input_requests: Any) -> list[ElicitRequest]:
    """Turn an ``inputRequests`` map into ElicitRequests, in key order.

    Raises UnsupportedInputRequest for anything that is not an elicitation
    we can render (we declare neither sampling nor roots), for a URL request
    without an http(s) URL, or for a form whose schema is not an object.
    """
    if not isinstance(input_requests, dict) or not input_requests:
        raise UnsupportedInputRequest("inputRequests is empty")
    out: list[ElicitRequest] = []
    for key, req in input_requests.items():
        if not isinstance(req, dict):
            raise UnsupportedInputRequest(f"inputRequests[{key!r}] is not a request")
        method = req.get("method")
        if method != "elicitation/create":
            raise UnsupportedInputRequest(f"server asked for {method or 'an unknown request'} ({key})")
        params = req.get("params") or {}
        if not isinstance(params, dict):
            raise UnsupportedInputRequest(f"elicitation {key!r} has no params")
        mode = params.get("mode") or "form"
        message = str(params.get("message") or "").strip()
        if mode == "url":
            url = params.get("url")
            if not isinstance(url, str) or urlsplit(url).scheme.lower() not in _SAFE_SCHEMES or not url_host(url):
                raise UnsupportedInputRequest(f"elicitation {key!r} has no http(s) URL")
            out.append(ElicitRequest(key=str(key), mode="url", message=message, url=url))
        elif mode == "form":
            schema = params.get("requestedSchema")
            if schema is None:
                schema = {"type": "object", "properties": {}}
            if not isinstance(schema, dict) or schema.get("type", "object") != "object" \
                    or not isinstance(schema.get("properties", {}), dict):
                raise UnsupportedInputRequest(f"elicitation {key!r} schema is not a flat object")
            out.append(ElicitRequest(key=str(key), mode="form", message=message, schema=schema))
        else:
            raise UnsupportedInputRequest(f"elicitation {key!r} uses unknown mode {mode!r}")
    return out


def describe_requests(requests: list[ElicitRequest]) -> str:
    """Short prose naming what the server asked (for the model / logs)."""
    asks: list[str] = []
    for r in requests:
        if r.mode == "url":
            asks.append(f"open {r.url}" + (f" ({r.message})" if r.message else ""))
        else:
            fields = list((r.schema or {}).get("properties", {}).keys())
            asks.append(f"fill in {', '.join(fields) or 'a form'}" + (f" ({r.message})" if r.message else ""))
    return "; ".join(asks) if asks else "more input"


# ── Validating what the user answered ──────────────────────────────────────

def _enum_values(prop: dict[str, Any]) -> list[Any] | None:
    if isinstance(prop.get("enum"), list):
        return list(prop["enum"])
    one = prop.get("oneOf")
    if isinstance(one, list) and one and all(isinstance(o, dict) and "const" in o for o in one):
        return [o["const"] for o in one]
    return None


def _item_enum_values(prop: dict[str, Any]) -> list[Any] | None:
    items = prop.get("items")
    if not isinstance(items, dict):
        return None
    if isinstance(items.get("enum"), list):
        return list(items["enum"])
    any_of = items.get("anyOf")
    if isinstance(any_of, list) and any_of and all(isinstance(o, dict) and "const" in o for o in any_of):
        return [o["const"] for o in any_of]
    return None


def _check_string(prop: dict[str, Any], value: Any) -> str | None:
    if not isinstance(value, str):
        return "must be text"
    lo, hi = prop.get("minLength"), prop.get("maxLength")
    if isinstance(lo, int) and len(value) < lo:
        return f"must be at least {lo} characters"
    if isinstance(hi, int) and len(value) > hi:
        return f"must be at most {hi} characters"
    fmt = prop.get("format")
    if fmt == "email" and not _EMAIL_RE.match(value):
        return "must be an e-mail address"
    if fmt == "uri":
        parts = urlsplit(value)
        if not parts.scheme or not (parts.netloc or parts.path):
            return "must be a URL"
    if fmt == "date":
        try:
            date.fromisoformat(value)
        except ValueError:
            return "must be a date (YYYY-MM-DD)"
    if fmt == "date-time":
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return "must be a date and time (ISO 8601)"
    return None


def _check_number(prop: dict[str, Any], value: Any, integer: bool) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "must be a number"
    if integer and int(value) != value:
        return "must be a whole number"
    lo, hi = prop.get("minimum"), prop.get("maximum")
    if isinstance(lo, (int, float)) and value < lo:
        return f"must be at least {lo}"
    if isinstance(hi, (int, float)) and value > hi:
        return f"must be at most {hi}"
    return None


def validate_content(schema: dict[str, Any], content: Any) -> tuple[dict[str, Any], dict[str, str]]:
    """Check a form answer against the (restricted) requestedSchema.

    Returns (clean content, errors by field). Unknown fields are dropped,
    missing optional fields stay absent, and a field whose schema uses a
    type outside the spec's primitive subset is passed through unchecked.
    """
    props = schema.get("properties") or {}
    required = [r for r in (schema.get("required") or []) if isinstance(r, str)]
    content = content if isinstance(content, dict) else {}
    clean: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for name, prop in props.items():
        if not isinstance(prop, dict):
            continue
        value = content.get(name)
        if value is None or value == "" or value == []:
            if name in required:
                errors[name] = "is required"
            continue
        enum_values = _enum_values(prop)
        item_values = _item_enum_values(prop) if prop.get("type") == "array" else None
        ptype = prop.get("type")
        err: str | None = None
        if enum_values is not None:
            if value not in enum_values:
                err = "must be one of the offered choices"
        elif item_values is not None:
            if not isinstance(value, list) or any(v not in item_values for v in value):
                err = "must be a selection of the offered choices"
            else:
                lo, hi = prop.get("minItems"), prop.get("maxItems")
                if isinstance(lo, int) and len(value) < lo:
                    err = f"pick at least {lo}"
                elif isinstance(hi, int) and len(value) > hi:
                    err = f"pick at most {hi}"
        elif ptype == "string":
            err = _check_string(prop, value)
        elif ptype in ("number", "integer"):
            err = _check_number(prop, value, integer=(ptype == "integer"))
        elif ptype == "boolean":
            if not isinstance(value, bool):
                err = "must be yes or no"
        if err:
            errors[name] = err
        else:
            clean[name] = value
    return clean, errors


def validate_response(request: ElicitRequest, response: Any) -> tuple[dict[str, Any], dict[str, str]]:
    """Normalise one ElicitResult from the UI.

    Returns (result to send, field errors). ``action`` must be one of
    ACTIONS; content is kept only for an accepted form, validated against
    its schema; an accepted URL request has no content.
    """
    if not isinstance(response, dict):
        return {}, {"": "missing answer"}
    action = response.get("action")
    if action not in ACTIONS:
        return {}, {"": f"action must be one of {', '.join(ACTIONS)}"}
    if action != "accept":
        return {"action": action}, {}
    if request.mode == "url":
        return {"action": "accept"}, {}
    clean, errors = validate_content(request.schema or {}, response.get("content"))
    if errors:
        return {}, errors
    return {"action": "accept", "content": clean}, {}


def validate_responses(prompt: ElicitationPrompt, responses: Any) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, str]]]:
    """Validate the whole answer map; every request must be answered."""
    responses = responses if isinstance(responses, dict) else {}
    clean: dict[str, dict[str, Any]] = {}
    errors: dict[str, dict[str, str]] = {}
    for req in prompt.requests:
        result, errs = validate_response(req, responses.get(req.key))
        if errs:
            errors[req.key] = errs
        else:
            clean[req.key] = result
    return clean, errors


def cancel_all(prompt: ElicitationPrompt) -> dict[str, dict[str, Any]]:
    """The answer when nobody is there to answer: the user "dismissed" it."""
    return {req.key: {"action": "cancel"} for req in prompt.requests}


def summarize_answers(prompt: ElicitationPrompt, responses: dict[str, dict[str, Any]]) -> str:
    """What the model may know about the user's answer: the action and the
    field names, never the values (they are the server's business)."""
    parts: list[str] = []
    for req in prompt.requests:
        res = responses.get(req.key) or {}
        action = res.get("action", "cancel")
        if req.mode == "url":
            what = f"the link at {url_host(req.url or '')}"
            parts.append({"accept": f"opened {what}", "decline": f"declined to open {what}"}.get(
                action, f"dismissed the request to open {what}"))
        else:
            fields = ", ".join((res.get("content") or {}).keys()) or "no fields"
            parts.append({"accept": f"filled in the server's form ({fields})",
                          "decline": "declined to fill in the server's form"}.get(
                action, "dismissed the server's form"))
    return "; ".join(parts)


# ── Broker: one per chat connection ────────────────────────────────────────

class ElicitationBroker:
    """Turns a ``send(event)`` coroutine into an Elicitor.

    ``ask(prompt)`` emits an ``elicitation_request`` event and waits for
    ``answer(elicitation_id, responses)``; invalid answers get an
    ``elicitation_invalid`` event and the wait continues. Times out (->
    None, the client sends cancel) after ``timeout`` seconds and tells the
    UI with ``elicitation_closed``. ``close()`` cancels every wait (the
    socket went away).
    """

    def __init__(self, send: Callable[[dict[str, Any]], Awaitable[None]], *, timeout: float = 600.0) -> None:
        self._send = send
        self.timeout = timeout
        self._pending: dict[str, tuple[ElicitationPrompt, asyncio.Future]] = {}
        self._confirms: dict[str, tuple[ConfirmPrompt, asyncio.Future]] = {}
        # Tasks extension (mcp_client.tasks): one Event per tool call that
        # is waiting on a background task, keyed by the chat's tool_id;
        # AgentCore fills it in, cancel_tool() sets it.
        self.cancel_events: dict[str, asyncio.Event] = {}

    @property
    def pending(self) -> int:
        return len(self._pending)

    async def ask(self, prompt: ElicitationPrompt) -> dict[str, dict[str, Any]] | None:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[prompt.id] = (prompt, fut)
        await self._send({"type": "elicitation_request", **prompt.public(),
                          "timeout_s": int(self.timeout)})
        try:
            return await asyncio.wait_for(fut, timeout=self.timeout)
        except asyncio.TimeoutError:
            logger.info("MCP elicitation %s for '%s' timed out after %.0fs", prompt.id, prompt.connection, self.timeout)
            await self._send({"type": "elicitation_closed", "elicitation_id": prompt.id, "reason": "timeout"})
            return None
        except asyncio.CancelledError:
            return None
        finally:
            self._pending.pop(prompt.id, None)

    async def answer(self, elicitation_id: Any, responses: Any) -> bool:
        """Deliver the user's answer. False when nothing waits under that id."""
        entry = self._pending.get(str(elicitation_id or ""))
        if entry is None:
            # Not an `error`: that ends the turn in the UI, and one may be running.
            await self._send({"type": "warning", "message": "That request for input has expired."})
            return False
        prompt, fut = entry
        clean, errors = validate_responses(prompt, responses)
        if errors:
            await self._send({"type": "elicitation_invalid", "elicitation_id": prompt.id, "errors": errors})
            return False
        if not fut.done():
            fut.set_result(clean)
        return True

    async def confirm(self, prompt: ConfirmPrompt) -> bool:
        """Ask the person to approve a tool run; False on deny, timeout or close."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._confirms[prompt.id] = (prompt, fut)
        await self._send({"type": "tool_confirm", **prompt.public(), "timeout_s": int(self.timeout)})
        try:
            return bool(await asyncio.wait_for(fut, timeout=self.timeout))
        except asyncio.TimeoutError:
            await self._send({"type": "tool_confirm_closed", "confirm_id": prompt.id, "reason": "timeout"})
            return False
        except asyncio.CancelledError:
            return False
        finally:
            self._confirms.pop(prompt.id, None)

    async def answer_confirm(self, confirm_id: Any, approve: Any) -> bool:
        entry = self._confirms.get(str(confirm_id or ""))
        if entry is None:
            await self._send({"type": "warning", "message": "That approval request has expired."})
            return False
        _prompt, fut = entry
        if not fut.done():
            fut.set_result(bool(approve))
        return True

    async def tool_status(self, tool_id: str, tool_name: str, status: dict[str, Any]) -> None:
        """Progress of an MCP background task behind one tool call -> UI."""
        await self._send({"type": "tool_status", "tool_id": tool_id, "name": tool_name, **status})

    async def cancel_tool(self, tool_id: Any) -> bool:
        """The user asked to cancel the task a tool call is waiting on."""
        ev = self.cancel_events.get(str(tool_id or ""))
        if ev is None:
            await self._send({"type": "warning", "message": "That tool call is no longer running."})
            return False
        ev.set()
        return True

    def close(self) -> None:
        for _prompt, fut in list(self._pending.values()):
            if not fut.done():
                fut.cancel()
        self._pending.clear()
        for ev in self.cancel_events.values():
            ev.set()   # nobody is watching: let a waiting task be cancelled
        self.cancel_events.clear()
        for _p, fut in list(self._confirms.values()):
            if not fut.done():
                fut.cancel()
        self._confirms.clear()

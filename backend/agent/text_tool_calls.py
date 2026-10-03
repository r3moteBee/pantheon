"""Recover tool calls a model wrote as text instead of structured tool_calls.

Smaller/local models (or servers without a tool-call parser for the
model's chat template) often reply with the call as plain text:

    {"action": "generate_image", "action_input": {"prompt": "..."}}   (ReAct)
    {"name": "recall", "arguments": {"query": "..."}}                 (OpenAI-ish)
    <tool_call>{"name": "...", "arguments": {...}}</tool_call>          (Hermes/Qwen)
    <tool_call><function=name><parameter=k>v</parameter></function>   (Qwen3 XML)
    ```json {...} ```                                                   (fenced)
    ```tool_code\nrecall(query="nvidia")\n```                          (Gemma)

Left alone, the user sees the JSON and nothing runs. ``recover`` turns such
a reply into tool calls — but only when the WHOLE reply is the call and
every name is a tool the agent actually has, so prose that merely contains
JSON is never executed.
"""
from __future__ import annotations

import ast
import json
import re
import uuid
from typing import Any

MAX_LEN = 50_000

# First characters of a reply that might be a textual tool call. The agent
# loop holds streamed text back while a reply starts like this.
HOLD_PREFIXES = ("{", "[", "```", "<tool_call")

_FENCE_RE = re.compile(r"^```[\w-]*\s*\n?(.*?)\n?```$", re.S)
_TAG_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)


def might_be_tool_call(text: str) -> bool | None:
    """True/False once the start of a reply decides it; None while too
    short to tell (e.g. "`" could still become "```")."""
    s = text.lstrip()
    if not s:
        return None
    for p in HOLD_PREFIXES:
        if s.startswith(p):
            return True
        if p.startswith(s):
            return None
    return False


def _loads(s: str) -> Any:
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None


def _args(v: Any) -> dict | None:
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        parsed = _loads(v)
        return parsed if isinstance(parsed, dict) else None
    return None


def _from_obj(obj: Any) -> list[tuple[str, dict]] | None:
    if isinstance(obj, list):
        out: list[tuple[str, dict]] = []
        for item in obj:
            got = _from_obj(item)
            if not got:
                return None
            out.extend(got)
        return out or None
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("tool_calls"), list):
        return _from_obj(obj["tool_calls"])
    if isinstance(obj.get("function"), dict):
        return _from_obj(obj["function"])
    for name_key, arg_keys in (("action", ("action_input", "input", "args", "arguments")),
                               ("name", ("arguments", "parameters", "args", "input")),
                               ("tool", ("tool_input", "input", "args", "arguments", "parameters")),
                               ("tool_name", ("arguments", "parameters", "args", "input"))):
        name = obj.get(name_key)
        if isinstance(name, str) and name:
            raw = next((obj[k] for k in arg_keys if k in obj), None)
            args = _args(raw)
            if args is None:
                return None
            return [(name, args)]
    return None


def _lenient_action(text: str) -> list[tuple[str, dict]] | None:
    """ReAct JSON whose action_input is a *string* with unescaped quotes
    (invalid JSON overall): take the name, then the largest balanced
    {...} after action_input that parses."""
    m = re.search(r'"(?:action|name|tool)"\s*:\s*"([\w.\-]+)"', text)
    k = re.search(r'"(?:action_input|arguments|parameters|args|input)"\s*:', text)
    if not m or not k:
        return None
    start = text.find("{", k.end())
    if start < 0:
        return None
    for end in range(len(text) - 1, start, -1):
        if text[end] == "}":
            args = _loads(text[start:end + 1])
            if isinstance(args, dict):
                return [(m.group(1), args)]
    return None


def _from_python_call(text: str) -> list[tuple[str, dict]] | None:
    """Gemma tool_code: ``name(a="x", n=1)`` (keyword literals only)."""
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError:
        return None
    call = tree.body
    # print(fn(...)) wrapper some templates use
    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "print" \
            and len(call.args) == 1 and isinstance(call.args[0], ast.Call):
        call = call.args[0]
    if not (isinstance(call, ast.Call) and isinstance(call.func, (ast.Name, ast.Attribute))) or call.args:
        return None
    name = call.func.id if isinstance(call.func, ast.Name) else call.func.attr
    args: dict[str, Any] = {}
    for kw in call.keywords:
        if kw.arg is None:
            return None
        try:
            args[kw.arg] = ast.literal_eval(kw.value)
        except (ValueError, SyntaxError):
            return None
    return [(name, args)]


_XML_FN_RE = re.compile(r"<function=([\w.\-]+)>\s*(.*?)\s*</function>", re.S)
_XML_PARAM_RE = re.compile(r"<parameter=([\w.\-]+)>\n?(.*?)\n?</parameter>", re.S)


def _from_xml(body: str) -> list[tuple[str, dict]] | None:
    """Qwen3's XML call format: <function=save_to_artifact><parameter=path>a.md</parameter>...</function>.
    A model left a whole save_to_artifact call like this as its reply (2026-10-03)."""
    fns = _XML_FN_RE.findall(body)
    if not fns or _XML_FN_RE.sub("", body).strip():
        return None
    out = []
    for name, inner in fns:
        args: dict[str, Any] = {}
        for k, v in _XML_PARAM_RE.findall(inner):
            parsed = _loads(v.strip())
            args[k] = parsed if isinstance(parsed, (dict, list, int, float, bool)) else v
        if _XML_PARAM_RE.sub("", inner).strip():
            return None
        out.append((name, args))
    return out


def recover(text: str, known_tools: set[str]) -> list[dict] | None:
    """Tool calls in the agent's event shape ({type, name, args, id}), or
    None when ``text`` isn't (entirely) a call to known tools."""
    if not text or len(text) > MAX_LEN:
        return None
    s = text.strip()
    if s.startswith("<tool_call>") and "</tool_call>" not in s:
        s += "</tool_call>"          # the model stopped before closing the tag
    calls: list[tuple[str, dict]] | None = None

    tags = _TAG_RE.findall(s)
    if tags and not _TAG_RE.sub("", s).strip():
        found: list[tuple[str, dict]] = []
        for body in tags:
            got = _from_obj(_loads(body)) or _lenient_action(body) or _from_xml(body)
            if not got:
                return None
            found.extend(got)
        calls = found
    else:
        fence = _FENCE_RE.match(s)
        body = fence.group(1).strip() if fence else s
        calls = _from_obj(_loads(body))
        if calls is None and body.startswith("{"):
            calls = _lenient_action(body)
        if calls is None and fence:
            calls = _from_python_call(body)

    if not calls or any(name not in known_tools for name, _ in calls):
        return None
    return [{"type": "tool_call", "name": name, "args": args,
             "id": f"call_{uuid.uuid4().hex[:12]}", "recovered": True}
            for name, args in calls]

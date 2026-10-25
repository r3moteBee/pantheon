"""MCP prompts as user-chosen commands.

The spec makes prompts *user-controlled*: the person picks one, the server
fills it in, the result steers the model. So a prompt is invoked from the
composer as ``/<connection>:<prompt> key=value ... free text`` (the picker
lists them next to skills), never by the model. ``resolve_prompt_command``
parses that line; ``render_prompt`` fetches it through the connection and
returns the text the agent receives as the user's message.
"""
from __future__ import annotations

import re
import shlex
from typing import Any

_CMD_RE = re.compile(r"^/([A-Za-z0-9_-]+):([A-Za-z0-9_.-]+)(?:\s+(.*))?$", re.DOTALL)


def parse_prompt_args(rest: str, arguments: list[dict[str, Any]]) -> dict[str, str]:
    """``key=value`` / ``key="quoted value"`` tokens become arguments; the
    leftover words go to the first argument that is still empty (required
    ones first)."""
    rest = (rest or "").strip()
    if not rest:
        return {}
    names = [a.get("name") for a in arguments if isinstance(a, dict) and a.get("name")]
    try:
        tokens = shlex.split(rest)
    except ValueError:
        tokens = rest.split()
    out: dict[str, str] = {}
    free: list[str] = []
    for tok in tokens:
        key, eq, value = tok.partition("=")
        if eq and key in names and key not in out:
            out[key] = value
        else:
            free.append(tok)
    if free:
        ordered = [a.get("name") for a in sorted(
            (a for a in arguments if isinstance(a, dict) and a.get("name")),
            key=lambda a: 0 if a.get("required") else 1)]
        target = next((n for n in ordered if n not in out), None)
        if target:
            out[target] = " ".join(free)
    return out


def resolve_prompt_command(message: str, manager: Any) -> dict[str, Any] | None:
    """``/connection:prompt ...`` -> {client, prompt, arguments, rest} or None."""
    m = _CMD_RE.match((message or "").strip())
    if not m:
        return None
    conn, pname, rest = m.group(1), m.group(2), m.group(3) or ""
    clients = getattr(manager, "_clients", {}) or {}
    client = next((c for n, c in clients.items() if n.lower() == conn.lower()), None)
    if client is None:
        return None
    prompt = next((p for p in getattr(client, "prompts", []) or [] if p.get("name") == pname), None)
    if prompt is None:
        return None
    return {"client": client, "prompt": prompt,
            "arguments": parse_prompt_args(rest, prompt.get("arguments") or []), "rest": rest}


def missing_arguments(prompt: dict[str, Any], arguments: dict[str, str]) -> list[str]:
    return [a["name"] for a in prompt.get("arguments") or []
            if isinstance(a, dict) and a.get("required") and not arguments.get(a.get("name", ""))]


async def render_prompt(resolved: dict[str, Any], *, elicit: Any = None) -> str:
    """Fetch the prompt and return what the agent gets as the user's message."""
    client, prompt = resolved["client"], resolved["prompt"]
    res = await client.call_synthetic("get_prompt", {"name": prompt["name"], "arguments": resolved["arguments"]},
                                      elicit=elicit)
    body = res.get("text") or ""
    if res.get("is_error"):
        raise RuntimeError(body or "the server did not return the prompt")
    label = prompt.get("title") or prompt["name"]
    return (f"[The user chose the prompt \"{label}\" from MCP server '{client.name}'; follow it as their request.]\n\n"
            f"{body}")


def list_prompt_commands(manager: Any) -> list[dict[str, Any]]:
    """What the composer's picker shows."""
    out: list[dict[str, Any]] = []
    for name, client in (getattr(manager, "_clients", {}) or {}).items():
        for p in getattr(client, "prompts", []) or []:
            if not isinstance(p, dict) or not p.get("name"):
                continue
            out.append({
                "connection": name, "name": p["name"], "command": f"{name}:{p['name']}",
                "title": p.get("title") or p["name"], "description": p.get("description") or "",
                "arguments": [{"name": a.get("name"), "description": a.get("description") or "",
                               "required": bool(a.get("required"))}
                              for a in (p.get("arguments") or []) if isinstance(a, dict) and a.get("name")],
            })
    return out

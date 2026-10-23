"""Tool-schema helpers for the MCP client.

Two concerns live here so ``client.py`` stays about the wire protocol:

* ``x-mcp-header`` (spec 2026-07-28, Streamable HTTP "Custom Headers from
  Tool Parameters"): a server may mark primitive tool parameters to be
  mirrored into ``Mcp-Param-<Name>`` request headers. Clients MUST do the
  mirroring and MUST drop tool definitions whose annotations break the
  rules. ``header_params`` validates one tool and returns the mapping;
  ``encode_header_value`` applies the Base64 sentinel encoding the spec
  requires for values that are not plain header-safe ASCII.
* ``simplify_for_llm``: the inputSchema the LLM sees. 2026-07-28 loosened
  ``inputSchema`` to any JSON Schema 2020-12 (``$ref``/``$defs`` included).
  Local refs are inlined, network refs are never fetched (spec: MUST NOT
  dereference automatically) and become a permissive ``{}``, and schema
  bookkeeping keys the model gains nothing from are dropped.
"""
from __future__ import annotations

import base64
import copy
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

HEADER_ANNOTATION = "x-mcp-header"
HEADER_PREFIX = "Mcp-Param-"
# RFC 9110 token: 1*tchar
_TCHAR_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_HEADER_TYPES = ("string", "integer", "boolean")
_SAFE_INT_MAX = 2 ** 53 - 1
_SENTINEL_OPEN, _SENTINEL_CLOSE = "=?base64?", "?="
_MAX_REF_DEPTH = 8


class InvalidToolDefinition(ValueError):
    """A tool definition the client must refuse (spec: exclude it from tools/list)."""


# ── x-mcp-header ─────────────────────────────────────────────────────────────

def header_params(tool: dict[str, Any]) -> list[tuple[str, tuple[str, ...]]]:
    """Return ``[(header_name_part, property_path), ...]`` for a tool's
    ``x-mcp-header`` annotations, or raise ``InvalidToolDefinition``.

    The property path is the chain of ``properties`` keys from the schema
    root; the spec only allows annotations that are *statically reachable*
    that way (no ``items``, composition, conditionals or ``$ref`` on the way).
    """
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict):
        return []
    found: list[tuple[str, tuple[str, ...]]] = []
    seen_lower: set[str] = set()

    def walk(node: Any, path: tuple[str, ...], reachable: bool) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, path, False)
            return
        if not isinstance(node, dict):
            return
        if HEADER_ANNOTATION in node:
            name = node[HEADER_ANNOTATION]
            if not reachable:
                raise InvalidToolDefinition(
                    f"{HEADER_ANNOTATION} {name!r} is not statically reachable via 'properties'")
            if not isinstance(name, str) or not name or not _TCHAR_RE.match(name):
                raise InvalidToolDefinition(f"{HEADER_ANNOTATION} value {name!r} is not an HTTP token")
            if name.lower() in seen_lower:
                raise InvalidToolDefinition(f"{HEADER_ANNOTATION} {name!r} is not unique (case-insensitive)")
            if node.get("type") not in _HEADER_TYPES:
                raise InvalidToolDefinition(
                    f"{HEADER_ANNOTATION} {name!r} is on a {node.get('type')!r} parameter; "
                    "only string, integer and boolean are allowed")
            seen_lower.add(name.lower())
            found.append((name, path))
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict) and reachable:
                for prop, sub in value.items():
                    walk(sub, path + (prop,), True)
            elif key == "properties":
                walk(value, path, False)
            elif key == HEADER_ANNOTATION:
                continue
            else:
                walk(value, path, False)

    walk(schema, (), True)
    return found


def value_at(arguments: Any, path: tuple[str, ...]) -> Any:
    """The argument value at a ``properties`` path, or None when absent."""
    node = arguments
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def encode_header_value(value: Any) -> str:
    """Render a parameter value (or tool name) as an HTTP header value.

    Strings stay as-is when every character is visible ASCII, space or tab,
    there is no leading/trailing whitespace, and the text does not itself
    look like the sentinel. Everything else is ``=?base64?<utf-8 b64>?=``.
    """
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, int):
        text = str(value)
    elif isinstance(value, float) and value.is_integer():
        text = str(int(value))
    elif isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    plain = (
        text != ""
        and all(0x21 <= ord(c) <= 0x7E or c in " \t" for c in text)
        and text == text.strip(" \t")
        and not (text.startswith(_SENTINEL_OPEN) and text.endswith(_SENTINEL_CLOSE))
    )
    if plain:
        return text
    return f"{_SENTINEL_OPEN}{base64.b64encode(text.encode('utf-8')).decode('ascii')}{_SENTINEL_CLOSE}"


def mirrored_headers(params: list[tuple[str, tuple[str, ...]]], arguments: Any) -> dict[str, str]:
    """The ``Mcp-Param-*`` headers for one call: a header per annotated
    parameter that has a non-null value in ``arguments``."""
    headers: dict[str, str] = {}
    for name, path in params:
        value = value_at(arguments, path)
        if value is None:
            continue
        headers[f"{HEADER_PREFIX}{name}"] = encode_header_value(value)
    return headers


# ── LLM-facing schema ────────────────────────────────────────────────────────

_DROP_KEYS = frozenset({"$schema", "$id", "$defs", "definitions", "$comment", HEADER_ANNOTATION})


def simplify_for_llm(schema: Any) -> Any:
    """A copy of ``inputSchema`` with local ``$ref`` inlined and bookkeeping
    keys removed, for the function-calling ``parameters`` field.

    Network refs are replaced by ``{}`` (never fetched). Cyclic or very deep
    refs also collapse to ``{}`` so a hostile schema cannot blow up here.
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    root = schema

    def resolve(ref: str) -> Any:
        if not ref.startswith("#"):
            logger.warning("MCP tool schema: ignoring non-local $ref %r", ref)
            return None
        node: Any = root
        for raw in ref[1:].split("/"):
            if raw == "":
                continue
            key = raw.replace("~1", "/").replace("~0", "~")
            if isinstance(node, list):
                try:
                    node = node[int(key)]
                except (ValueError, IndexError):
                    return None
            elif isinstance(node, dict) and key in node:
                node = node[key]
            else:
                return None
        return node

    def walk(node: Any, stack: tuple[str, ...]) -> Any:
        if isinstance(node, list):
            return [walk(x, stack) for x in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node and isinstance(node["$ref"], str):
            ref = node["$ref"]
            if ref in stack or len(stack) >= _MAX_REF_DEPTH:
                return {}
            target = resolve(ref)
            if not isinstance(target, dict):
                return {}
            merged = dict(target)
            # Sibling keywords next to $ref apply too (2020-12).
            merged.update({k: v for k, v in node.items() if k != "$ref"})
            return walk(merged, stack + (ref,))
        return {k: walk(v, stack) for k, v in node.items() if k not in _DROP_KEYS}

    out = walk(copy.deepcopy(schema), ())
    if not isinstance(out, dict) or not out:
        return {"type": "object", "properties": {}}
    return out

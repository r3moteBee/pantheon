"""What the agent loop does with a tool's result string.

- ``is_error`` — the one "did this tool call fail?" signal. The agent loop
  puts it on every ``tool_result`` event; routing tuning counts it.
- ``cap`` — shortens a result before it goes into the model's context.
  The UI event keeps the full text; the model gets head + tail and a note
  saying how much was left out, so one huge MCP reply or file read can't
  push the conversation past the context window.
"""
from __future__ import annotations

import os
import re

# Tool results are prose, so errors are recognised by how they start:
# "Error: …", "Search failed: …", "MCP tool error: …", "Tool 'x' is disabled".
_ERROR_RE = re.compile(
    r"\s*(error\b|refus|access denied|unknown tool|invalid\b"
    r"|tool '[^']+' is disabled"
    r"|.{0,60}?\berror:"
    r"|.{0,60}?\b(failed|refused|denied|not found|not allowed|escapes)\b)",
    re.I,
)
_MCP_ERROR_MARK = "[server reported isError=true on this tool result]"

DEFAULT_MAX_CHARS = 24_000


def is_error(result: object) -> bool:
    text = str(result or "")
    return bool(_ERROR_RE.match(text[:200])) or text.rstrip().endswith(_MCP_ERROR_MARK)


def max_chars() -> int:
    try:
        n = int(os.getenv("TOOL_RESULT_MAX_CHARS", DEFAULT_MAX_CHARS))
    except ValueError:
        return DEFAULT_MAX_CHARS
    return n if n > 0 else DEFAULT_MAX_CHARS


def cap(result: object, limit: int | None = None) -> str:
    text = result if isinstance(result, str) else str(result)
    limit = limit or max_chars()
    if len(text) <= limit:
        return text
    head = int(limit * 0.75)
    tail = limit - head
    omitted = len(text) - head - tail
    return (
        f"{text[:head]}\n\n[… {omitted:,} of {len(text):,} characters omitted to fit the "
        "context window. Narrow the request (a smaller query, a path prefix, a section, "
        f"a max_chars/limit argument) to see the rest …]\n\n{text[-tail:]}"
    )

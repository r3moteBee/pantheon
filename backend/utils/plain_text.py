"""Markdown -> readable plain text, for chat apps that show the raw symbols.

The messaging adapters send text the app does not render as Markdown
(Telegram without parse_mode, Matrix without formatted_body, Slack mrkdwn).
Asking the model for plain text was not enough: with the reply style in both
the system prompt and the turn note, the 9B agent model still answered a
comparison question with headings and a table 4 times out of 4. So bot
replies are converted after the fact (AgentCore.run_autonomous).
"""
from __future__ import annotations

import re

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_BULLET = re.compile(r"^(\s*)[*+]\s+")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_ITALIC_STAR = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
_ITALIC_UNDER = re.compile(r"(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_CODE = re.compile(r"`([^`\n]+)`")


def _inline(text: str) -> str:
    # Inline code first, so its contents are not treated as emphasis.
    codes: list[str] = []

    def keep(m: re.Match) -> str:
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    text = _CODE.sub(keep, text)
    text = _IMAGE.sub(lambda m: f"{m.group(1) or 'image'} ({m.group(2)})", text)
    text = _LINK.sub(lambda m: m.group(1) if m.group(1) == m.group(2) else f"{m.group(1)} ({m.group(2)})", text)
    text = _BOLD.sub(r"\2", text)
    text = _ITALIC_STAR.sub(r"\1", text)
    text = _ITALIC_UNDER.sub(r"\1", text)
    text = _STRIKE.sub(r"\1", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)


def _cells(row: str) -> list[str]:
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [_inline(c.strip()) for c in row.split("|")]


def _table(rows: list[str]) -> list[str]:
    """A Markdown table as one line per row: "- Header: cell; Header: cell"."""
    if len(rows) >= 2 and _TABLE_SEP.match(rows[1]):
        header, body = _cells(rows[0]), rows[2:]
    else:
        header, body = [], rows
    out = []
    for r in body:
        cells = _cells(r)
        if header and len(header) == len(cells):
            first, rest = cells[0], list(zip(header[1:], cells[1:]))
            parts = [f"{h}: {c}" for h, c in rest if c]
            out.append(f"- {first}" + (f" — {'; '.join(parts)}" if parts else ""))
        else:
            out.append("- " + " | ".join(c for c in cells if c))
    return out


def markdown_to_plain(text: str) -> str:
    """Readable plain text: no #, **, tables or [link](url) syntax.

    Code blocks keep their contents (fences dropped); lists keep "- " / "1."
    markers, which read fine as plain text."""
    if not text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    in_code = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if _FENCE.match(line):
            in_code = not in_code
            i += 1
            continue
        if in_code:
            out.append(line)
            i += 1
            continue
        if line.lstrip().startswith("|") and line.count("|") >= 2:
            block = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                block.append(lines[i])
                i += 1
            out.extend(_table(block))
            continue
        m = _HEADING.match(line)
        if m:
            heading = _inline(m.group(1)).rstrip(":")
            if out and out[-1].strip():
                out.append("")
            out.append(f"{heading}:" if heading else "")
            i += 1
            continue
        if _RULE.match(line):
            out.append("")
            i += 1
            continue
        line = _BULLET.sub(r"\1- ", line)
        line = re.sub(r"^\s*>\s?", "", line)
        out.append(_inline(line))
        i += 1
    # Collapse runs of blank lines left by removed rules/fences.
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()

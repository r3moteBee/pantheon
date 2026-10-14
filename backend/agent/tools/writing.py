"""Writing voices: the user's saved writing styles (Settings → About you)."""
from __future__ import annotations

from typing import Any

from agent.tools.registry import ToolContext, tool


SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "writing_voice",
            "description": (
                "Read one of the user's saved writing voices (their own style: notes plus samples of their "
                "writing) before drafting anything that goes out under their name - a post, email, letter, "
                "article, bio or speech. action='get' returns the voice's notes, excerpts of its newest samples "
                "and how to imitate it (no name = the default voice); 'list' lists the voices; 'sample' returns "
                "one whole sample."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["get", "list", "sample"],
                        "description": "get (default): the voice to write in. list: all voices. sample: one full sample.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Voice name, when the user named one. Omit to use the default voice.",
                    },
                    "sample": {
                        "type": "string",
                        "description": "For action='sample': the sample's title (omit for the newest).",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
]


@tool("writing_voice")
async def _writing_voice(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> str:
    from utils import user_profile as up
    action = (tool_args.get("action") or "get").strip().lower()
    if action == "list":
        voices = up.list_voices()
        if not voices:
            return ("The user has no writing voices yet. They can add one, with samples of their writing, "
                    "in Settings → About you.")
        lines = ["The user's writing voices:"]
        for v in voices:
            notes = " ".join(v["notes"].split())
            notes = notes if len(notes) <= 200 else notes[:197] + "…"
            lines.append(f"- \"{v['name']}\"{' (default)' if v['is_default'] else ''}: "
                         f"{v['sample_count']} samples, {v['total_words']} words"
                         + (f" — {notes}" if notes else ""))
        return "\n".join(lines)
    if action not in ("get", "sample"):
        return f"Error: unknown action '{action}' (use get, list or sample)."
    voice, problem = up.resolve_voice(tool_args.get("name"))
    if not voice:
        return problem
    if action == "get":
        return up.render_voice_for_agent(voice)
    sample, problem = up.find_sample(voice, tool_args.get("sample"))
    if not sample:
        return problem
    text = sample["content"]
    if len(text) > up.SAMPLE_FULL_MAX_CHARS:
        text = text[:up.SAMPLE_FULL_MAX_CHARS].rstrip() + "\n[… rest of the sample omitted]"
    return (f"# \"{sample['title']}\" — a sample of the voice \"{voice['name']}\" ({sample['word_count']} words)\n"
            "Imitate the style, never the content or phrases.\n\n" + text)

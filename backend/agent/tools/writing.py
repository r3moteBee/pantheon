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
                "Read one of the user's saved writing voices before drafting anything that goes out under their "
                "name (post, email, letter, article, bio, speech). action='get': the voice's notes, sample "
                "excerpts and how to imitate it (no name = default voice); 'list'; 'sample': one whole sample."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["get", "list", "sample"],
                    },
                    "name": {
                        "type": "string",
                        "description": "The voice the user named (default voice if omitted).",
                    },
                    "sample": {
                        "type": "string",
                        "description": "action=sample: the sample's title (default: newest).",
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

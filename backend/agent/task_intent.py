"""What a chat message asks for in time, and when a task it asks for needs review.

Future timing. Asked "In 15 minutes, check the releases page", "Remind me
tomorrow at 9am ..." or "... and next week compare it", the agent did the
check at once, asked three questions, or did only the part for today - it did
not reach for create_task once in 9 runs (homely follow-through eval,
2026-10-08). future_timing() spots the wording; the turn then gets
future_time_note(), which says what to queue and how to write the schedule.

Review. create_task proposals wait for approval in the Tasks tab. That review
exists for the agent's own judgement - a task it decided on, a chain of steps
that can delete, overwrite or push - not for work the user just asked for: an
image the user requested once sat as a paused proposal while the chat said
nothing about approving it. explicit_task_request() says whether the user asked
for scheduled or background work in this message; review_reason() says why a
task still needs review anyway (a plan step that can lose or corrupt data).
"""
from __future__ import annotations

import re

_UNITS = r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?)"
_DAYS = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_FUTURE_RE = re.compile(
    rf"\b(?:in|within|after) (?:\d+|a|an|a few|a couple of|half an?|one|two|three|five|ten|fifteen|thirty) {_UNITS}\b"
    rf"|\b(?:tomorrow|tonight|overnight|later (?:today|tonight|this (?:morning|afternoon|evening|week)|on)"
    rf"|this (?:evening|afternoon|weekend)|next (?:week|month|year|{_DAYS})|on {_DAYS}|end of (?:the )?(?:day|week|month))\b"
    rf"|\bat \d{{1,2}}(?::\d\d)?\s*(?:am|pm)\b"
    rf"|\b(?:every|each) (?:\d+ {_UNITS}|day|morning|evening|night|weekday|week|month|hour|{_DAYS})\b"
    rf"|\b(?:daily|weekly|monthly|hourly|nightly)\b"
    rf"|\bremind me\b|\bset (?:up )?a reminder\b"
    rf"|\b(?:once|when|as soon as|after) (?:it|that|this|the [\w-]+(?: [\w-]+)?) (?:is|'s|has|have|finishes|finished|completes|completed|lands|arrives|is done|is in|comes in)\b",
    re.I,
)
_TASK_ASK_RE = re.compile(
    r"\b(?:create|set up|setup|schedule|start|queue|make|add|run)\s+(?:me\s+)?(?:a|an|the)?\s*"
    r"(?:new\s+)?(?:background|scheduled|recurring|daily|weekly|research)?\s*(?:task|job|reminder|digest|batch)\b"
    r"|\bin the background\b",
    re.I,
)

# Plan steps that can lose or corrupt information, or act outside Pantheon on the user's behalf.
_RISKY_TOOLS = ("delete_", "forget", "consolidate_memory", "merge_topics", "approve_merge", "force_merge",
                "reject_merge", "git_commit", "git_merge", "git_push", "github_write", "github_merge",
                "github_delete", "github_create_pr", "update_artifact", "write_file", "run_command",
                "code_execute", "batch_convert_documents")
_RISKY_WORDS_RE = re.compile(r"\b(?:delete|remove|overwrite|replace|wipe|purge|drop|truncate|force[- ]push|"
                             r"merge|rename|move)\b", re.I)


def future_timing(text: str | None) -> str | None:
    """The phrase that puts part of the request in the future, or None."""
    m = _FUTURE_RE.search(text or "")
    return m.group(0) if m else None


def explicit_task_request(text: str | None) -> bool:
    """True when the message itself asks for scheduled, delayed or background work."""
    return bool(future_timing(text) or _TASK_ASK_RE.search(text or ""))


def review_reason(tool_args: dict) -> str | None:
    """Why a task the user asked for still waits for approval: a step that can delete, overwrite,
    merge or push (named by tool or in words), or an iteration loop that commits to main."""
    text = " ".join(str(tool_args.get(k) or "") for k in ("plan", "description", "execute_instruction"))
    low = text.lower()
    for t in _RISKY_TOOLS:
        if t in low:
            return f"its plan uses {t.rstrip('_')}"
    m = _RISKY_WORDS_RE.search(text)
    if m:
        return f"its plan says \"{m.group(0)}\""
    if tool_args.get("job_type") == "iteration_loop" and tool_args.get("branch_strategy") == "main":
        return "it commits to main"
    return None


def future_time_note(phrase: str) -> str:
    return (f"[Pantheon note] Part of this request happens later (\"{phrase}\"). Do the parts that can happen "
            "now, now. Queue the later part with create_task in this turn - the user asked for it, so do not ask "
            "whether to set it up. schedule: 'delay:N' = N minutes from now, a cron expression for a time of day "
            "or a repeat ('0 9 * * *' = 9:00 every day; work out tomorrow's date from the current time above), "
            "'interval:N' = every N minutes. Put everything the later run needs into description and plan - it "
            "sees nothing else. If it waits for something the user will do (an upload), schedule it for the time "
            "they gave and have it check for that first. Only after create_task has returned, say in one line what was "
            "queued and when it runs - never say a task is queued without calling create_task.\n\n")

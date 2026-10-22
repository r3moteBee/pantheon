"""What a chat message asks for in time, or as a picture.

Future timing. Asked "In 15 minutes, check the releases page", "Remind me
tomorrow at 9am ..." or "... and next week compare it", the agent did the
check at once, asked three questions, or did only the part for today - the
later part never became a task (create_task 0 times in 9 runs, homely
follow-through eval, 2026-10-08). future_timing() spots the wording; the turn
then gets future_time_note(): do the part for now, now, and make the later
part a create_task - under the usual approval rule (prompts.py "Scheduled task
approval flow"): the plan is shown for approval, nothing is scheduled on
wording alone.

asks_to_repeat() backs create_task's refusal of repeating schedules nobody
asked for; wants_image() adds IMAGE_NOTE to picture requests.
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
_REPEAT_RE = re.compile(
    rf"\b(?:every|each) (?:\d+ {_UNITS}|day|morning|evening|night|weekday|week|month|hour|{_DAYS})\b"
    r"|\b(?:daily|weekly|monthly|hourly|nightly|recurring|regularly|repeatedly|periodically)\b"
    r"|\bkeep (?:checking|watching|an eye on)\b|\bmonitor\b|\bwatch for\b|\bon a schedule\b",
    re.I,
)
# A request to MAKE a picture: a making verb before the noun. A bare "image" also matched "what's in this
# image?" and "describe the attached picture", which are vision questions, not generate_image calls.
_IMAGE_RE = re.compile(
    r"\b(?:make|create|generate|draw|design|render|produce|paint|sketch|illustrate|mock up|turn (?:this|that|it) into"
    r"|give me|i (?:want|need|'d like))\b[^.?!\n]{0,40}?"
    r"\b(?:infographic|image|picture|illustration|poster|drawing|wallpaper|logo|graphic|visual|icon|banner)s?\b"
    r"|\bdraw (?:me )?an?\b",
    re.I)


def asks_to_repeat(text: str | None) -> bool:
    """True when the message asks for something that recurs (a schedule that repeats)."""
    return bool(_REPEAT_RE.search(text or ""))


_NOT_PICTURE_RE = re.compile(r"\b(?:docker|container|podman|oci|disk|iso|vm|system|base|os|cloud|boot|"
                             r"firmware|backup|kernel|qcow2?|raw)[- ]image|\bimage (?:tag|registry|digest|layer|pull|build)",
                             re.I)


def wants_image(text: str | None) -> bool:
    """True when the message asks for a picture, not a container or disk image."""
    t = _NOT_PICTURE_RE.sub(" ", text or "")
    return bool(_IMAGE_RE.search(t))


IMAGE_NOTE = ("[Pantheon note] This asks for an image. Call generate_image with a prompt that describes it - for an "
              "infographic or poster, the layout plus the exact title and each short line of text in quotes (the "
              "image model renders quoted text). Keep it to a title and at most five short facts, taken from this "
              "conversation. Do not write plotting code or describe the image instead of generating it.\n\n")


def future_timing(text: str | None) -> str | None:
    """The phrase that puts part of the request in the future, or None."""
    m = _FUTURE_RE.search(text or "")
    return m.group(0) if m else None


def future_time_note(phrase: str) -> str:
    return (f"[Pantheon note] Part of this request happens later (\"{phrase}\"). Do the parts that can happen "
            "now, now. The later part is a scheduled task (create_task) - do not do it now and do not just "
            "describe it. Follow the approval flow: end your reply with its numbered plan (the tool for each step, "
            "and everything the later run needs - it sees nothing else) and the exact schedule, and ask the user to "
            "approve; if they said not to review, call create_task with skip_review now. schedule: once, later -> "
            "'delay:N' = N minutes from now (for 'tomorrow at 9' work out the minutes from the current time "
            "above); repeating, only when the user asked for a repeat -> cron ('0 9 * * *' = 9:00 every day) or "
            "'interval:N'. If it waits for something the user will do (an upload), schedule it for the time they "
            "gave and have it check for that first. Never say a task is queued or set up unless create_task "
            "returned 'Task scheduled'.\n\n")

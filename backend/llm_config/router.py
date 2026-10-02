"""Per-turn chat router (phase 2 of model routing).

Picks the task class — and so the model — for ONE interactive chat turn.
Background work doesn't come through here: call sites there already know
their class (extract, summarize, …).

Rules first (no model call), in priority order:

  1. override   — the user pinned a class for this session (/model <class>)
  2. vision     — images attached and the agent model can't see them
  3. long       — history + message won't fit the agent model's window
  4. skill      — the active skill's manifest asks for a class
  5. code       — code fences / tracebacks / diffs in the message
  6. sticky     — the conversation was recently on code; stay there
  7. fresh      — time-sensitive question, or a follow-up in a conversation about
                  current facts (agent/freshness.py) - never quick
  8. quick      — short conversational message with no tool intent
  9. classifier — optional small-model call, only when rules can't decide
 10. default    — agent

A rule only fires when its target class has its OWN route (not inherited)
and that route's primary model can run the tool loop — otherwise the turn
stays on agent and the decision says why. The class is chosen once per
turn, before the agent loop, so the model never changes mid tool loop.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Classes a chat turn may be routed to.
CHAT_CLASSES = ("agent", "quick", "code", "long_context", "vision")

CONFIG_KEY = "llm_router"
DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,          # rules only fire for classes you've configured
    "classifier": False,      # small-model tie-breaker for ambiguous turns
    "quick_max_chars": 240,   # "quick" only below this message length
    "sticky_turns": 3,        # stay on code for N turns after a code signal
}

# Fixed prompt overhead (system prompt + ~100 tool schemas) in tokens, used
# when estimating whether a turn fits the agent model's context window.
PROMPT_OVERHEAD_TOKENS = 12_000
LONG_CONTEXT_THRESHOLD = 0.75

_IMAGE_RE = re.compile(r"\[image:[^\]]*\(artifact:[\w\-]+\)\]|data:image/[a-z]+;base64,", re.I)
_CODE_RE = re.compile(
    r"```|Traceback \(most recent call last\)|^\s*(diff --git|@@ -\d+,\d+ \+\d+,\d+ @@)"
    r"|^\s*at [\w.$]+\([\w.]+:\d+\)|\b(SyntaxError|TypeError|NullPointerException|segmentation fault)\b",
    re.M,
)
# Words that mean the turn will probably need tools/memory — never "quick".
_TOOL_INTENT_RE = re.compile(
    r"\b(search|find|look ?up|ingest|schedul\w*|create|save|research|analy[sz]\w*|fetch|download"
    r"|run|remember|recall|artifact|file|project|task|job|graph|summari[sz]e|compare|report"
    r"|generate|image|draw|write|draft|update|delete|list|show|open|read|browse|check)\b"
    r"|https?://|/\w",
    re.I,
)
# Short messages that still need the agent. The quick class is fine for small
# talk, trivia, arithmetic and questions about the user's own memory (recall
# puts the facts in the prompt) - measured on homely's 9B with thinking off:
# arithmetic 24/27 on quick vs 22/27 on agent, memory probes 15/15. What it
# does badly is anything time-sensitive: it answered "I don't have access to
# real-time information" instead of searching 2 times in 18 (agent: 18/18).
# Those, actions, and multi-step requests go to the agent.
_NEEDS_AGENT_RE = re.compile(
    # time-sensitive / current facts -> needs a search
    r"\b(latest|newest|current(ly)?|today|tonight|tomorrow|yesterday|this (week|month|year)|last (night|week)"
    r"|right now|news|price[sd]?|how much (is|are|does|do)|stock|exchange rate|weather|forecast|release[sd]?|out yet"
    r"|launch\w*|announce\w*|won|winning|leading|trending|outages?|open for)\b"
    # actions
    # ("book a table", not "that book"; "remind me to ...", not "remind me what we decided")
    r"|\b(e-?mail|send|text|message|book (a|an|me|us)|remind (me|us) (to|at|in|on|tomorrow|tonight)"
    r"|set an? (alarm|timer|reminder)|reminder)\b"
    # multi-step work
    r"|\b(plan|itinerary|compare|debug)\b",
    re.I,
)


@dataclass
class RouteDecision:
    task_class: str
    rule: str
    reason: str
    endpoint: str = ""
    model: str = ""
    est_tokens: int = 0
    notes: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("notes", None)
        if self.notes:
            d["reason"] = self.reason + " (" + "; ".join(self.notes) + ")"
        return d


# ── Config ──────────────────────────────────────────────────────────────

def get_config() -> dict[str, Any]:
    from secrets.vault import get_vault
    try:
        stored = json.loads(get_vault().get_secret(CONFIG_KEY) or "{}")
    except (json.JSONDecodeError, TypeError):
        stored = {}
    return {**DEFAULT_CONFIG, **{k: v for k, v in stored.items() if k in DEFAULT_CONFIG}}


def set_config(patch: dict[str, Any]) -> dict[str, Any]:
    from secrets.vault import get_vault
    cfg = get_config()
    for k, v in patch.items():
        if k not in DEFAULT_CONFIG:
            raise ValueError(f"unknown router setting {k!r}")
        if isinstance(DEFAULT_CONFIG[k], bool):
            v = bool(v)
        else:
            v = int(v)
            if v < 0:
                raise ValueError(f"{k} must be >= 0")
        cfg[k] = v
    get_vault().set_secret(CONFIG_KEY, json.dumps(cfg))
    return cfg


# ── Per-session state (single process; resets on restart by design) ───

_MAX_SESSIONS = 500
_sessions: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


def _state(session_id: str) -> dict[str, Any]:
    st = _sessions.get(session_id)
    if st is None:
        st = {"override": None, "sticky": None, "sticky_left": 0, "last_decision": None}
        _sessions[session_id] = st
        while len(_sessions) > _MAX_SESSIONS:
            _sessions.popitem(last=False)
    else:
        _sessions.move_to_end(session_id)
    return st


def set_override(session_id: str, task_class: str | None) -> None:
    """Pin a session to a class (None / "auto" clears the pin).

    Re-pinning away from the class the router just used counts as a
    correction of that turn (a tuning signal)."""
    if task_class in (None, "", "auto"):
        task_class = None
    elif task_class not in CHAT_CLASSES:
        raise ValueError(f"unknown chat class {task_class!r} — use one of {', '.join(CHAT_CLASSES)} or auto")
    st = _state(session_id)
    if task_class != st["override"]:
        last = st.get("last_decision")
        if task_class and last and last[1] != task_class:
            from llm_config.usage import mark_corrected
            mark_corrected(last[0])
    st["override"] = task_class


# The user pushing back on the previous answer — a tuning signal.
_CORRECTION_RE = re.compile(
    r"^\W*(no\b|nope\b|wrong\b|incorrect\b|that'?s (not|wrong|incorrect)\b|not what i\b"
    r"|try again\b|you (didn'?t|missed|forgot|ignored)\b|that didn'?t work\b|still (wrong|broken|not)\b)",
    re.I,
)


def looks_like_correction(message: str) -> bool:
    return bool(_CORRECTION_RE.match(message or ""))


def note_user_message(session_id: str, message: str) -> None:
    """Before routing a new turn: flag the previous turn if this message
    pushes back on it."""
    st = _sessions.get(session_id)
    last = st.get("last_decision") if st else None
    if last and looks_like_correction(message):
        from llm_config.usage import mark_corrected
        mark_corrected(last[0])
        st["last_decision"] = None  # count each turn once


def remember_decision(session_id: str, decision_id: str, task_class: str) -> None:
    _state(session_id)["last_decision"] = (decision_id, task_class)


def get_override(session_id: str) -> str | None:
    st = _sessions.get(session_id)
    return st["override"] if st else None


_MODEL_CMD_RE = re.compile(r"^/model\b(?:\s+(\S+))?\s*(.*)$", re.S)


def parse_model_command(message: str) -> tuple[str | None, str] | None:
    """"/model code fix this" -> ("code", "fix this"); "/model" -> (None, "").
    Returns None when the message isn't a /model command."""
    m = _MODEL_CMD_RE.match((message or "").strip())
    if not m:
        return None
    return (m.group(1) or None), (m.group(2) or "").strip()


# ── Capability lookups ─────────────────────────────────────────────────

def _own_primary(task_class: str) -> dict[str, str] | None:
    """The class's OWN primary entry (None when it only inherits)."""
    from llm_config.store import get_routes
    entries = get_routes().get(task_class) or []
    return entries[0] if entries else None


def _agent_primary() -> dict[str, str] | None:
    from llm_config.store import resolve_route
    chain = resolve_route("agent")
    return {"endpoint": chain[0].endpoint_name, "model": chain[0].model} if chain else None


def _profile(entry: dict[str, str] | None):
    from llm_config.store import get_profile
    from llm_config.models import ModelProfile
    if not entry:
        return ModelProfile()
    return get_profile(entry["endpoint"], entry["model"])


def _usable(task_class: str, *, need_vision: bool = False) -> tuple[bool, str]:
    """Can this class run a chat turn? (configured + tool-capable [+ vision])."""
    entry = _own_primary(task_class)
    if not entry:
        return False, f"no {task_class} model configured"
    prof = _profile(entry)
    if not prof.tools:
        return False, f"{entry['model']} isn't marked tool-capable"
    if need_vision and not prof.vision:
        return False, f"{entry['model']} isn't marked vision-capable"
    return True, ""


# ── Signals ─────────────────────────────────────────────────────────────

def estimate_tokens(text_chars: int) -> int:
    return PROMPT_OVERHEAD_TOKENS + text_chars // 4


def has_images(message: str) -> bool:
    return bool(_IMAGE_RE.search(message or ""))


def looks_like_code(message: str) -> bool:
    return bool(_CODE_RE.search(message or ""))


def looks_quick(message: str, max_chars: int) -> bool:
    msg = (message or "").strip()
    if not msg or len(msg) > max_chars or "\n\n" in msg:
        return False
    return not (_TOOL_INTENT_RE.search(msg) or _NEEDS_AGENT_RE.search(msg))


def _skill_class(skill: Any) -> str | None:
    if skill is None:
        return None
    try:
        cls = skill.manifest.pantheon.model_class
    except AttributeError:
        return None
    return cls if cls in CHAT_CLASSES else None


# ── Decision ────────────────────────────────────────────────────────────

async def decide(
    message: str,
    *,
    session_id: str,
    history_chars: int = 0,
    history: list[dict] | None = None,
    skill: Any = None,
    config: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> RouteDecision:
    """``state`` lets a what-if replay use private per-session state
    instead of the live sessions' pins/stickiness."""
    cfg = config or get_config()
    st = state if state is not None else _state(session_id)
    est = estimate_tokens(history_chars + len(message or ""))
    notes: list[str] = []

    def done(cls: str, rule: str, reason: str) -> RouteDecision:
        entry = _own_primary(cls) if cls != "agent" else _agent_primary()
        return RouteDecision(cls, rule, reason, endpoint=(entry or {}).get("endpoint", ""),
                             model=(entry or {}).get("model", ""), est_tokens=est, notes=notes)

    # 1. Explicit pin always wins (even with the router disabled).
    if st["override"]:
        cls = st["override"]
        if cls == "agent":
            return done("agent", "override", "pinned with /model")
        ok, why = _usable(cls)
        if ok:
            return done(cls, "override", "pinned with /model")
        notes.append(f"pinned {cls} unusable: {why}")

    if not cfg.get("enabled", True):
        return done("agent", "disabled", "router off")

    agent_prof = _profile(_agent_primary())

    # 2. Images the agent model can't see.
    if has_images(message) and not agent_prof.vision:
        for cls in ("vision", "long_context", "code", "quick"):
            ok, why = _usable(cls, need_vision=True)
            if ok:
                return done(cls, "vision", "image attached; agent model has no vision")
        notes.append("image attached but no tool-capable vision model — staying on agent")

    # 3. Won't fit the agent model's window.
    window = agent_prof.context_window
    if window and est > window * LONG_CONTEXT_THRESHOLD:
        ok, why = _usable("long_context")
        lp = _profile(_own_primary("long_context"))
        if ok and (lp.context_window or 0) > window:
            return done("long_context", "long", f"~{est // 1000}k tokens vs {window // 1000}k agent window")
        notes.append(f"~{est // 1000}k tokens near agent window; " + (why or "long_context window not larger"))

    # 4. Skill preference.
    scls = _skill_class(skill)
    if scls and scls != "agent":
        ok, why = _usable(scls)
        if ok:
            return done(scls, "skill", f"skill asks for {scls}")
        notes.append(f"skill wants {scls}: {why}")

    # 5/6. Code, with a short stickiness window.
    if looks_like_code(message):
        ok, why = _usable("code")
        if ok:
            st["sticky"], st["sticky_left"] = "code", int(cfg.get("sticky_turns", 3))
            return done("code", "code", "code/traceback in message")
    if st["sticky"] and st["sticky_left"] > 0:
        st["sticky_left"] -= 1
        ok, _ = _usable(st["sticky"])
        if ok:
            return done(st["sticky"], "sticky", f"recent {st['sticky']} turns in this conversation")
    else:
        st["sticky"] = None

    # 7. Needs current facts - the quick class answered those from memory.
    # Follow-ups count too: "can you do a similar breakdown for the house?" after
    # two Senate-race lookups went to quick, unsearched (2026-10-02).
    from agent.freshness import followup_needs_fresh, needs_fresh_facts
    if needs_fresh_facts(message):
        return done("agent", "fresh", "time-sensitive question")
    if followup_needs_fresh(message, history or []):
        return done("agent", "fresh", "follow-up in a conversation about current facts")

    # 8. Short conversational turn — but never a push-back on the last
    # answer ("no, that's wrong"): that deserves the stronger model.
    if looks_quick(message, int(cfg.get("quick_max_chars", 240))) and not looks_like_correction(message):
        ok, _ = _usable("quick")
        if ok:
            return done("quick", "quick", "short message, no tool intent")

    # 9. Optional classifier for the ambiguous middle.
    if cfg.get("classifier"):
        cls = await _classify(message)
        if cls and cls != "agent":
            ok, _ = _usable(cls)
            if ok:
                return done(cls, "classifier", f"classifier picked {cls}")

    return done("agent", "default", "default")


# ── Optional LLM classifier ──────────────────────────────────────────────

_CLASSIFY_TIMEOUT = 3.0
_classify_cache: "OrderedDict[str, str | None]" = OrderedDict()
_CLASSIFY_PROMPT = (
    "Classify the user's request for routing. Reply with ONE word:\n"
    "quick — small talk or a simple question answerable without tools or research\n"
    "code — writing, reviewing or debugging code\n"
    "agent — anything else (research, multi-step work, tools, memory)\n\n"
    "Request:\n"
)


async def _classify(message: str) -> str | None:
    """One cheap call on the extract class; any failure/timeout → None."""
    usable = [c for c in ("quick", "code") if _usable(c)[0]]
    if not usable:
        return None
    key = hashlib.sha1((message or "").encode()).hexdigest()
    if key in _classify_cache:
        return _classify_cache[key]
    from models.provider import get_provider_for
    prov = get_provider_for("extract")
    result: str | None = None
    try:
        resp = await asyncio.wait_for(
            prov.chat_complete([{"role": "user", "content": _CLASSIFY_PROMPT + (message or "")[:2000]}]),
            timeout=_CLASSIFY_TIMEOUT,
        )
        word = re.findall(r"[a-z_]+", (resp.get("content") or "").lower())
        result = word[0] if word and word[0] in ("quick", "code", "agent") else None
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.info("router classifier skipped: %s", e)
    _classify_cache[key] = result
    while len(_classify_cache) > 256:
        _classify_cache.popitem(last=False)
    return result

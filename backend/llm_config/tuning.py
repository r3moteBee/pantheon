"""Routing tuning from your own history (phase 3).

Three pieces, none of which change anything on their own:

  stats()            — per class / rule quality of routed chat turns:
                       problem rate, tool use, tool errors, corrections,
                       thumbs, latency, fallbacks.
  recommendations()  — concrete suggestions with evidence, each with an
                       optional action the user applies explicitly.
  simulate()         — what-if replay: re-run the router's rules over past
                       chat messages with a candidate config and report
                       which turns would move. Deterministic; no LLM calls.

A turn is a "problem" when it errored, hit the tool-iteration cap, was
corrected by the user (push-back message or re-pin with /model) or got a
thumbs-down. Tool errors are reported separately — they're often the
tool's fault, not the model's.
"""
from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

MIN_TURNS = 15          # per group before any quality-based recommendation
MIN_CALLS = 20          # per model before an error-rate recommendation


# ── Stats ───────────────────────────────────────────────────────────────

def _has_outcome(r: dict) -> bool:
    return r.get("latency_ms") is not None


def _is_problem(r: dict) -> bool:
    return bool(r.get("stream_error") or r.get("truncated") or r.get("corrected")
                or (r.get("rating") or 0) < 0)


def _group_stats(rows: list[dict]) -> dict[str, Any]:
    scored = [r for r in rows if _has_outcome(r)]
    n = len(scored)
    lats = sorted(r["latency_ms"] for r in scored)
    tool_users = [r for r in scored if (r.get("tool_calls") or 0) > 0]
    return {
        "turns": len(rows),
        "scored": n,
        "problem_rate": (sum(_is_problem(r) for r in scored) / n) if n else None,
        "tool_use_rate": (len(tool_users) / n) if n else None,
        "tool_error_rate": (sum((r.get("tool_errors") or 0) > 0 for r in tool_users) / len(tool_users))
                           if tool_users else None,
        "corrected": sum(bool(r.get("corrected")) for r in rows),
        "thumbs_up": sum((r.get("rating") or 0) > 0 for r in rows),
        "thumbs_down": sum((r.get("rating") or 0) < 0 for r in rows),
        "truncated": sum(bool(r.get("truncated")) for r in rows),
        "errors": sum(bool(r.get("stream_error")) for r in rows),
        "p50_ms": int(statistics.median(lats)) if lats else None,
        "p95_ms": lats[max(0, int(len(lats) * 0.95) - 1)] if lats else None,
        "fallback_rate": (sum(bool(r.get("served_model")) and r.get("served_model") != r.get("model")
                              for r in rows) / len(rows)) if rows else None,
    }


def stats(hours: float = 24 * 7) -> dict[str, Any]:
    from llm_config.usage import decision_rows
    rows = decision_rows(hours)
    by_class: dict[str, list] = {}
    by_rule: dict[tuple[str, str], list] = {}
    for r in rows:
        by_class.setdefault(r["task_class"], []).append(r)
        by_rule.setdefault((r["task_class"], r["rule"]), []).append(r)
    return {
        "hours": hours,
        "total_turns": len(rows),
        "classes": [{"task_class": c, **_group_stats(rs)} for c, rs in sorted(by_class.items())],
        "rules": [{"task_class": c, "rule": rule, **_group_stats(rs)}
                  for (c, rule), rs in sorted(by_rule.items(), key=lambda kv: -len(kv[1]))],
    }


# ── Recommendations ─────────────────────────────────────────────────────

def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def recommendations(hours: float = 24 * 7) -> list[dict[str, Any]]:
    """Suggestions with evidence. ``action`` (optional) is applied only via
    apply_recommendation(), never automatically."""
    from llm_config.router import get_config
    from llm_config.store import get_routes
    from llm_config.usage import summary

    cfg = get_config()
    st = stats(hours)
    cls = {c["task_class"]: c for c in st["classes"]}
    recs: list[dict[str, Any]] = []

    def add(id_: str, severity: str, title: str, detail: str, action: dict | None = None):
        recs.append({"id": id_, "severity": severity, "title": title, "detail": detail, "action": action})

    agent = cls.get("agent")
    quick = cls.get("quick")
    qmax = int(cfg.get("quick_max_chars", 240))

    if st["total_turns"] < MIN_TURNS:
        add("more-data", "info", "Not enough routed turns yet",
            f"{st['total_turns']} routed chat turns in this window; recommendations need about "
            f"{MIN_TURNS} per class. Keep using chat (and the 👍/👎 on replies) and check back.")

    # Quick chat — misfires, quality, and room to grow.
    if quick and (quick["scored"] or 0) >= MIN_TURNS:
        if (quick["tool_use_rate"] or 0) > 0.3 and qmax > 60:
            new = max(60, int(qmax * 0.6))
            add("quick-misfire", "warn", "Quick chat is catching tool work",
                f"{_pct(quick['tool_use_rate'])} of quick turns called tools — those messages "
                f"weren't just chat. Lowering the quick limit from {qmax} to {new} chars keeps "
                "longer requests on Agent.",
                {"kind": "router_config", "patch": {"quick_max_chars": new}})
        if agent and (agent["scored"] or 0) >= MIN_TURNS and quick["problem_rate"] is not None \
                and agent["problem_rate"] is not None:
            gap = quick["problem_rate"] - agent["problem_rate"]
            if gap > 0.15:
                add("quick-quality", "warn", "Quick chat answers go wrong more often",
                    f"Problem rate {_pct(quick['problem_rate'])} on quick vs {_pct(agent['problem_rate'])} "
                    "on agent (errors, corrections, thumbs-down). Turning quick off sends those "
                    "turns back to Agent; or pick a stronger quick model in Model routing.",
                    {"kind": "router_config", "patch": {"quick_max_chars": 0}})
            elif gap <= 0.03 and quick["p50_ms"] and agent["p50_ms"] \
                    and quick["p50_ms"] < agent["p50_ms"] * 0.7 and 0 < qmax < 600:
                new = min(600, int(qmax * 1.5))
                add("quick-expand", "info", "Quick chat is holding up — give it more",
                    f"Quick matches agent quality ({_pct(quick['problem_rate'])} vs "
                    f"{_pct(agent['problem_rate'])} problems) at {quick['p50_ms']}ms vs "
                    f"{agent['p50_ms']}ms median. Raising the limit to {new} chars routes more "
                    "short turns there. Preview shows how many.",
                    {"kind": "router_config", "patch": {"quick_max_chars": new}})

    # Code class vs agent.
    code = cls.get("code")
    if code and agent and (code["scored"] or 0) >= MIN_TURNS and (agent["scored"] or 0) >= MIN_TURNS \
            and code["problem_rate"] is not None and agent["problem_rate"] is not None \
            and code["problem_rate"] - agent["problem_rate"] > 0.15:
        add("code-quality", "warn", "Coding turns go wrong more often than Agent turns",
            f"Problem rate {_pct(code['problem_rate'])} on code vs {_pct(agent['problem_rate'])} on "
            "agent. Consider a different Coding model, or clear the Coding route so code turns "
            "stay on Agent.")

    # Sticky code turns that are mostly corrected → shorten stickiness.
    sticky = next((r for r in st["rules"] if r["rule"] == "sticky"), None)
    if sticky and (sticky["scored"] or 0) >= MIN_TURNS and (sticky["problem_rate"] or 0) > 0.3 \
            and int(cfg.get("sticky_turns", 3)) > 1:
        new = int(cfg.get("sticky_turns", 3)) - 1
        add("sticky-shorter", "info", "Conversations stay on Coding too long",
            f"{_pct(sticky['problem_rate'])} of 'stay on code' turns had problems — the "
            f"conversation had usually moved on. Shorten stickiness to {new} turn(s).",
            {"kind": "router_config", "patch": {"sticky_turns": new}})

    # Classifier.
    clf = next((r for r in st["rules"] if r["rule"] == "classifier"), None)
    if cfg.get("classifier") and clf and (clf["scored"] or 0) >= MIN_TURNS and agent \
            and clf["problem_rate"] is not None and agent["problem_rate"] is not None \
            and clf["problem_rate"] - agent["problem_rate"] > 0.15:
        add("classifier-off", "warn", "The LLM classifier is choosing badly",
            f"Classifier-routed turns: {_pct(clf['problem_rate'])} problems vs "
            f"{_pct(agent['problem_rate'])} on agent. Turn it off.",
            {"kind": "router_config", "patch": {"classifier": False}})

    # Fallback / model health, from the per-attempt call log.
    routes = get_routes()
    usage = summary(hours)["rows"]
    health = {(u["task_class"], u["endpoint"], u["model"]): u for u in usage}
    for task_class, entries in routes.items():
        if not entries:
            continue
        p = entries[0]
        pu = health.get((task_class, p["endpoint"], p["model"]))
        if not pu or pu["calls"] < MIN_CALLS:
            continue
        err = pu["errors"] / pu["calls"]
        if err < 0.2:
            continue
        better = None
        for i, e in enumerate(entries[1:], start=1):
            fu = health.get((task_class, e["endpoint"], e["model"]))
            if fu and fu["calls"] >= 5 and fu["errors"] / fu["calls"] < 0.05:
                better = (i, e, fu)
                break
        if better:
            i, e, fu = better
            add(f"swap-{task_class}", "warn", f"{task_class}: fallback is more reliable than the primary",
                f"{p['model']} failed {_pct(err)} of {pu['calls']} calls; fallback {e['model']} "
                f"failed {_pct(fu['errors'] / fu['calls'])} of {fu['calls']}. Make it the primary. "
                f"Last primary error: {pu.get('last_error') or '?'}",
                {"kind": "swap_primary", "task_class": task_class, "index": i})
        else:
            add(f"unreliable-{task_class}", "warn", f"{task_class}: primary model is failing often",
                f"{p['model']} failed {_pct(err)} of {pu['calls']} calls"
                + ("" if len(entries) > 1 else " and has no fallback — add one in Model routing")
                + f". Last error: {pu.get('last_error') or '?'}")

    order = {"warn": 0, "info": 1}
    recs.sort(key=lambda r: order.get(r["severity"], 2))
    return recs


def apply_recommendation(rec_id: str, hours: float = 24 * 7) -> dict[str, Any]:
    """Re-derive the recommendation server-side and apply its action."""
    rec = next((r for r in recommendations(hours) if r["id"] == rec_id), None)
    if rec is None:
        raise ValueError("recommendation no longer applies — refresh")
    action = rec.get("action")
    if not action:
        raise ValueError("this recommendation has no automatic action")
    if action["kind"] == "router_config":
        from llm_config.router import set_config
        set_config(action["patch"])
    elif action["kind"] == "swap_primary":
        from llm_config.models import RouteEntry
        from llm_config.store import get_routes, set_routes
        routes = get_routes()
        entries = routes[action["task_class"]]
        i = action["index"]
        entries.insert(0, entries.pop(i))
        set_routes({c: [RouteEntry(**e) for e in es] for c, es in routes.items()})
        from models.provider import reset_provider
        reset_provider()
    else:
        raise ValueError(f"unknown action {action['kind']!r}")
    return rec


# ── What-if replay ──────────────────────────────────────────────────────

def _recent_chat_messages(days: float, limit: int) -> list[dict[str, Any]]:
    """Interactive chat user messages (oldest first) with the size of the
    conversation before each. Job sessions (prompts carry job_id) and
    /model commands are skipped."""
    from memory.episodic import EpisodicMemory
    ep = EpisodicMemory()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with ep._connect() as conn:
        rows = conn.execute(
            """SELECT session_id, role, content, timestamp, metadata FROM messages
               WHERE timestamp >= ? ORDER BY session_id, timestamp""",
            (since,),
        ).fetchall()
    job_sessions: set[str] = set()
    for r in rows:
        try:
            if "job_id" in json.loads(r["metadata"] or "{}"):
                job_sessions.add(r["session_id"])
        except (json.JSONDecodeError, TypeError):
            pass
    out: list[dict[str, Any]] = []
    history: dict[str, int] = {}
    for r in rows:
        sid = r["session_id"]
        if sid in job_sessions:
            continue
        content = r["content"] or ""
        if r["role"] == "user" and not content.lstrip().startswith("/model"):
            out.append({"session_id": sid, "content": content,
                        "history_chars": history.get(sid, 0), "timestamp": r["timestamp"]})
        history[sid] = history.get(sid, 0) + len(content)
    out.sort(key=lambda m: m["timestamp"])
    return out[-limit:]


async def simulate(patch: dict[str, Any], days: float = 14, limit: int = 400) -> dict[str, Any]:
    """Replay past chat messages through the router with the current config
    and with ``patch`` applied; report which turns would move.

    The classifier is skipped in both runs (it would cost a model call per
    message), and session pins aren't replayed (they aren't stored)."""
    from llm_config import router as chat_router
    unknown = set(patch) - set(chat_router.DEFAULT_CONFIG)
    if unknown:
        raise ValueError(f"unknown router setting(s): {', '.join(sorted(unknown))}")
    base = {**chat_router.get_config(), "classifier": False}
    cand = {**base, **patch, "classifier": False}
    msgs = _recent_chat_messages(days, limit)
    states: dict[tuple[str, str], dict] = {}

    def state(run: str, sid: str) -> dict:
        return states.setdefault((run, sid), {"override": None, "sticky": None,
                                              "sticky_left": 0, "last_decision": None})

    cur_counts: dict[str, int] = {}
    new_counts: dict[str, int] = {}
    moves: dict[str, int] = {}
    examples: list[dict[str, Any]] = []
    for m in sorted(msgs, key=lambda m: (m["session_id"], m["timestamp"])):
        kw = dict(session_id=m["session_id"], history_chars=m["history_chars"])
        d0 = await chat_router.decide(m["content"], config=base, state=state("cur", m["session_id"]), **kw)
        d1 = await chat_router.decide(m["content"], config=cand, state=state("new", m["session_id"]), **kw)
        cur_counts[d0.task_class] = cur_counts.get(d0.task_class, 0) + 1
        new_counts[d1.task_class] = new_counts.get(d1.task_class, 0) + 1
        if d0.task_class != d1.task_class:
            key = f"{d0.task_class}→{d1.task_class}"
            moves[key] = moves.get(key, 0) + 1
            if len(examples) < 10:
                text = " ".join(m["content"].split())
                examples.append({"from": d0.task_class, "to": d1.task_class, "rule": d1.rule,
                                 "text": text[:120] + ("…" if len(text) > 120 else "")})
    return {
        "messages": len(msgs), "days": days, "patch": patch,
        "current": cur_counts, "candidate": new_counts,
        "changed": sum(moves.values()), "moves": moves, "examples": examples,
        "notes": ["classifier and /model pins are not replayed"],
    }

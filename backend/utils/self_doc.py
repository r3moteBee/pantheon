"""get_self_documentation: what Pantheon can do and how THIS instance is set up.

Written for the agent answering "can you ...", "where does ... go", "what happens if
...", "which model ..." - and for the agent itself mid-task. Two parts:

1. How Pantheon works: a short capability guide with this instance's numbers filled in
   (limits, toolchains, sandbox mode), so answers match the code.
2. This instance right now: the tools offered in the CALLING context (web chat vs.
   background task / messaging app) and why others are hidden, model routing (which
   endpoints are on the local network), connections, skills, a few settings.

Secrets never appear, not even masked: settings are shown only from SETTINGS_SHOWN
(an allowlist of harmless keys) and URLs are reduced to their host. An earlier version
masked by substring and printed the first/last 4 characters of vault_master_key,
secret_key and llm_api_key into the model's context.

generate_self_doc() is synchronous (SQLite, vault, registries); the tool runs it in a
thread so it never blocks the event loop.
"""
from __future__ import annotations

import ipaddress
import logging
import os
from typing import Any
from urllib.parse import urlsplit

from config import get_settings

logger = logging.getLogger(__name__)

# Settings shown verbatim. Allowlist, never a denylist: a new setting holding a
# credential must not show up here by default.
SETTINGS_SHOWN = (
    "agent_host_exec", "agent_web_budget", "agent_force_search", "agent_thinking",
    "llm_max_tokens", "history_token_budget", "pantheon_builder", "builder_timeout_seconds",
    "builder_allow_network", "auto_index_uploads", "personality_weight", "context_focus",
    "embedding_model", "allow_private_fetch", "auth_session_days", "app_env",
)


def _host(url: str | None) -> str:
    """Host[:port] of a URL - no credentials, path or query."""
    try:
        parts = urlsplit(url or "")
        host = parts.hostname or ""
        return f"{host}:{parts.port}" if parts.port else host
    except Exception:
        return ""


def _is_local(host: str) -> bool:
    h = host.split(":")[0].lower()
    if not h or h == "localhost" or h.endswith((".local", ".lan", ".internal", ".svc", ".cluster.local", ".ts.net")):
        return True
    if "." not in h:
        return True          # a bare service name
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_private or ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False


def _limits() -> dict[str, Any]:
    s = get_settings()
    out: dict[str, Any] = {
        "max_rounds": int(os.getenv("MAX_TOOL_ITERATIONS", "100") or 100),
        "web_budget": s.agent_web_budget,
        "reply_tokens": s.llm_max_tokens,
        "result_chars": 24_000,
    }
    try:
        from agent import tool_results
        out["result_chars"] = tool_results.max_chars()
    except Exception:
        pass
    return out


def _sandbox_line() -> str:
    mode = (os.getenv("PANTHEON_SANDBOX") or "subprocess").strip().lower()
    if mode == "firecracker":
        return "code_execute runs snippets (Python, Node, Bash) in a Firecracker microVM: isolated from the server."
    return ("code_execute runs snippets (Python, Node, Bash) as a subprocess inside Pantheon's own server/container, "
            "30 s timeout, 256 MB memory, WITHOUT filesystem isolation (Firecracker isolation is not enabled).")


def _toolchains() -> str:
    try:
        from builder.backend import toolchains
        return "; ".join(f"{t.name} ({t.about.split(':')[0].split(',')[0]})" for t in toolchains().values())
    except Exception:
        return "unknown"


def _guide(lim: dict[str, Any], builder_on: bool) -> str:
    build = (f"run_build builds and tests a folder of artifacts in a throwaway container with no network and returns "
             f"PASSED/FAILED plus a zip download link. Toolchains: {_toolchains()}. Swift is Swift on Linux: packages, "
             "Foundation, XCTest - no SwiftUI/UIKit/AppKit, so iOS apps can be written and their logic tested but not "
             "built or run as apps."
             if builder_on else "run_build is not configured on this instance (no PANTHEON_BUILDER): code can be written "
             "and saved, but not compiled in a build container.")
    return f"""## How Pantheon works

**Memory (per project).** Every conversation is stored (episodic). Saved artifacts and notes are indexed for meaning
(semantic, vector search) and entities go into a knowledge graph. At the start of each turn Pantheon recalls relevant
past messages, notes and graph entries from the SAME project and adds them to the message; `recall` searches more.
`remember` stores a fact on purpose (episodic / semantic / graph). Projects don't share memory.

**Artifacts vs workspace.** Artifacts are the durable store: notes, reports, transcripts, generated images, code and
project files the user asked for, and chat uploads (saved under `chat-attachments/<date>/`). They are versioned,
indexed for recall, shown on the Artifacts page, downloadable (a folder as a zip), buildable with run_build and
pushable to GitHub. The workspace is the project's scratch folder on disk (files for code_execute / run_command,
downloads, git checkouts): not on the Artifacts page, indexed only when you call `index(target='workspace')`.
Deliverables go to artifacts with save_to_artifact, one folder per project (`MyApp/main.py`, `MyApp/README.md`).

**Tasks and jobs.** `create_task` schedules a background run: one-shot (`now`, `delay:N` minutes) or recurring
(`interval:N` minutes, or cron such as `0 9 * * 1`). Job types: autonomous_task (run a plan once per firing),
iteration_loop (execute/review turns, each saved as artifact `iteration/<job id, 8 chars>/turn-01.md`...),
research_batch (one sourced note per item for surveys of many items, then a summary), coding_task (edits the project's
bound GitHub repo and opens a PR; web chat only, starts at once). Approval: for a schedule, show the user a numbered
plan first and call create_task with skip_review=true only after they approve it in this chat; any task created without
that approval (and every task created outside the web chat) waits PAUSED as a proposal until the user approves it in
the chat's Tasks tab. When a run finishes, a completion notice is posted into the chat that scheduled it and the run
shows in the Tasks tab; `list_recent_jobs` / `get_job_status` read the real status.

**Skills** are reusable recipes the user runs with `/skill-name` (create_skill makes one); they are not schedules.

**Limits.** Up to {lim['max_rounds']} tool rounds per turn (MAX_TOOL_ITERATIONS; background tasks default to the same
and can raise it with max_iterations, up to 1000). Web chat: at most {lim['web_budget']} web lookups (web_search /
web_fetch) per turn (AGENT_WEB_BUDGET) - bigger surveys go to a research_batch task. Each tool result is cut to
{lim['result_chars']:,} characters (TOOL_RESULT_MAX_CHARS). Each model call writes at most {lim['reply_tokens']:,}
tokens (LLM_MAX_TOKENS).

**Running and building code.** {_sandbox_line()} run_command runs shell commands in the project's repo checkout or
workspace. Both, and the git_* tools, only exist in the web chat (AGENT_HOST_EXEC). {build}

**Models.** Every LLM call has a task class (agent, quick, code, long_context, vision, image_gen, summarize, extract,
embed, rerank) routed to a configured endpoint/model with fallbacks. In web chat a router picks the class per turn
(quick for short no-tool messages, code for coding, vision for images the agent model can't see, else agent); the
model that answered is shown under each reply. The user pins one with `/model <agent|quick|code|long_context|vision|auto>`
or the model picker next to the chat input; routes are set in Settings -> LLMs.

**GitHub.** Needs a personal access token (Connections page -> GitHub) and a repo bound to the project (the chat's
Repository tab). Then the `github` tool reads the repo, and `github(action="push_artifacts")` commits an artifact folder
to a new branch and opens a pull request (web chat only); the Artifacts page has a Push to GitHub button too.

**Reaching the user.** Replies go to the chat the message came from. Task results are posted into the scheduling chat.
Messaging apps (Telegram, Slack, Discord, Matrix, Mattermost) are set up in Settings -> Channels; send_telegram only
delivers when Telegram is configured. There is no email or phone push tool; email, calendar, drive and similar services
need an MCP connector (Connections page -> MCP servers)."""


def _context_section(project_id: str | None, interactive: bool, host_exec: bool) -> tuple[str, bool]:
    """The calling context: tools offered now, and why others aren't. Returns (text, builder_on)."""
    lines = ["## This conversation"]
    lines.append("- Context: " + ("web chat driven by the user (interactive)." if interactive else
                                  "a background task or a messaging-app chat (not interactive): tasks you create land as "
                                  "proposals the user must approve in the web UI, and coding tasks / push_artifacts are refused."))
    builder_on = False
    names: list[str] = []
    try:
        from agent.tools import get_all_tool_schemas, HOST_EXEC_TOOLS
        from builder import builder_enabled
        builder_on = builder_enabled()
        names = [t["function"]["name"] for t in get_all_tool_schemas()
                 if host_exec or t["function"]["name"] not in HOST_EXEC_TOOLS]
    except Exception as e:
        logger.debug("tool list unavailable: %s", e)
    off = []
    if not host_exec:
        off.append("code_execute, run_command, git_* and start_autoresearch (host execution is off here; "
                   "AGENT_HOST_EXEC allows it only in web chat)")
    if not builder_on:
        off.append("run_build (no builder configured)")
    try:
        from agent.browser_tools import browser_enabled
        if not browser_enabled():
            off.append("browser tools (BROWSER_ENABLED is off: pages can be read as text with web_fetch, not clicked "
                       "or logged into)")
    except Exception:
        pass
    builtin = [n for n in names if not n.startswith("mcp_")]
    mcp = [n for n in names if n.startswith("mcp_")]
    if builtin:
        lines.append(f"- Tools: the {len(builtin)} built-in tools in your tool list are all usable here.")
    lines.append(f"- MCP tools: {len(mcp)}" + (f" ({', '.join(mcp[:40])})" if mcp else
                                                 " - no email, calendar, Slack, drive or other external-service tools.") )
    if off:
        lines.append("- Not available here: " + "; ".join(off) + ".")
    repo = None
    if project_id:
        try:
            from api.connections import get_project_repo_for_tools
            repo = get_project_repo_for_tools(project_id)
        except Exception:
            repo = None
    if repo:
        lines.append(f"- GitHub: this project is bound to `{repo['owner']}/{repo['repo']}` (default branch "
                     f"`{repo['default_branch']}`).")
    else:
        n_conn = 0
        try:
            from api.connections import _connect
            with _connect() as cn:
                n_conn = cn.execute("SELECT COUNT(*) FROM github_connections").fetchone()[0]
        except Exception:
            pass
        lines.append("- GitHub: no repo is bound to this project" + (
            f" ({n_conn} GitHub connection(s) exist; bind one in the chat's Repository tab)." if n_conn else
            " and no GitHub connection is configured, so nothing can be pushed until the user adds a token "
            "(Connections page -> GitHub) and binds a repo."))
    return "\n".join(lines), builder_on


def _instance_section() -> str:
    s = get_settings()
    lines = ["## This instance"]
    try:
        from utils.version import app_version
        lines.append(f"- Pantheon version: {app_version()}")
    except Exception:
        pass
    # Model routing
    try:
        from llm_config.models import TASK_CLASSES
        from llm_config.store import get_routes, list_endpoints, route_warnings
        eps = {ep.name: _host(ep.base_url) for ep in list_endpoints()}
        routes = get_routes()
        rows = []
        for cls, meta in TASK_CLASSES.items():
            entries = routes.get(cls) or []
            if not entries:
                parent = meta.get("inherits")
                rows.append(f"{cls}: " + (f"uses {parent}" if parent else "not set"))
                continue
            chain = " -> ".join(f"{e['model']} @ {e['endpoint']}" for e in entries)
            rows.append(f"{cls}: {chain}")
        lines.append("- Model routing (class: model @ endpoint, fallbacks after ->): " + "; ".join(rows) + ".")
        if eps:
            lines.append("- Endpoints: " + "; ".join(
                f"{n} = {h or '?'} ({'local network' if _is_local(h) else 'external service'})" for n, h in eps.items()) + ".")
        for w in route_warnings(routes) or []:
            lines.append(f"- Routing warning: {w}")
    except Exception as e:
        logger.debug("routing unavailable: %s", e)
    try:
        from llm_config import router as _r
        rc = _r.get_config()
        lines.append(f"- Chat router: {'on' if rc['enabled'] else 'off'} (quick <= {rc['quick_max_chars']} chars).")
    except Exception:
        pass
    try:
        from llm_config.usage import summary as _usage
        # counts only: provider error texts can echo a key ("Incorrect API key provided: sk-...")
        errs = [f"{r['task_class']} {r['model']}@{r['endpoint']}: {r['errors']}"
                for r in _usage(24).get("rows", []) if r.get("errors")]
        if errs:
            lines.append("- LLM errors in the last 24h: " + "; ".join(errs[:5]) + ".")
    except Exception:
        pass
    try:
        from llm_config import tuning as _tuning
        recs = [r for r in _tuning.recommendations(24 * 7) if r["id"] != "more-data"]
        if recs:
            lines.append("- Routing tuning suggestions (the user applies them in Settings -> LLMs): "
                         + "; ".join(f"{r['title']}" for r in recs[:4]) + ".")
    except Exception:
        pass
    # Search
    try:
        from agent.search_providers import get_search_manager
        provs = get_search_manager().get_usage().get("providers", [])
        lines.append("- Web search chain (in order): " + ", ".join(
            f"{p['name']}" + ("" if p.get("enabled") else " (disabled)")
            + (" (no API key)" if p.get("type") in ("brave", "tavily", "serper", "exa") and not p.get("api_key_set") else "")
            for p in provs) + ".")
    except Exception:
        pass
    # MCP
    try:
        from mcp_client.manager import get_mcp_manager
        conns = get_mcp_manager().list_connections()
        lines.append("- MCP connectors: " + (", ".join(
            f"{c['name']} ({'connected' if c.get('connected') else 'not connected'}, {c.get('tools_count', 0)} tools)"
            for c in conns) if conns else "none configured") + ".")
    except Exception:
        pass
    # Messaging
    try:
        from messaging.gateway import get_messaging_gateway
        gw = get_messaging_gateway()
        if not getattr(gw, "_adapters", None):
            gw._auto_register()
        conf = [n for n, a in gw._adapters.items() if a.is_configured()]
        lines.append("- Messaging apps configured: " + (", ".join(conf) if conf else
                                                       "none (no Telegram/Slack/Discord/Matrix/Mattermost)") + ".")
    except Exception:
        pass
    # Skills
    try:
        from skills.registry import get_skill_registry
        reg = get_skill_registry()
        reg.ensure_loaded()
        sk = [f"/{x.manifest.name}" for x in reg.list_all()]
        lines.append("- Installed skills (run with /name): " + (", ".join(sk) if sk else "none") + ".")
    except Exception:
        pass
    lines.append(f"- Data directory: {s.data_dir} (SQLite stores, artifacts, ChromaDB "
                 f"{'server' if s.chroma_host else 'on disk'}).")
    shown = []
    for key in SETTINGS_SHOWN:
        if hasattr(s, key):
            shown.append(f"{key}={getattr(s, key)}")
    lines.append("- Settings: " + ", ".join(shown) + ".")
    return "\n".join(lines)


def generate_self_doc(project_id: str | None = None, interactive: bool = True, host_exec: bool = True) -> str:
    """The self-documentation for a caller in the given context (sync - run it in a thread)."""
    lim = _limits()
    ctx, builder_on = _context_section(project_id, interactive, host_exec)
    parts = ["# Pantheon self-documentation",
             "Pantheon is a self-hosted, single-user agent harness: chat, per-project memory, artifacts, web research, "
             "scheduled background tasks and skills. Answer questions about yourself from this page.",
             _guide(lim, builder_on), ctx, _instance_section()]
    return "\n\n".join(parts)

"""Agent tools: schemas the model sees and the dispatcher that runs them.

Each domain module (memory, files, artifacts, web, sources, tasks, skills,
images, code, github, git, finance) holds its tools' ``SCHEMAS`` next to
their handlers, registered with ``@tool`` (see ``registry``). To add a
tool: put its schema in the module's ``SCHEMAS``, write an
``@tool("name")`` handler ``(ctx, tool_name, tool_args)``, add the name to
``_ORDER`` below, then regenerate docs/tools.md.
"""
from __future__ import annotations

import logging
from typing import Any

from agent.tools.registry import ToolContext, resolve
from agent.tools import (  # noqa: F401  (imported to register their tools)
    artifacts, code, files, finance, git, github, images, memory, skills,
    sources, tasks, web, workspace,
)
# Re-exported for callers that import helpers from agent.tools.
from agent.tools.web import _web_fetch, _web_search, _ddg_search  # noqa: F401
from agent.tools.images import _generate_image_tool  # noqa: F401
from agent.tools.workspace import (  # noqa: F401
    _get_workspace_base, _safe_workspace_path, _PRECOMMIT_MARKER, _install_precommit_hook,
    _repo_checkout_dir, _resolve_repo_checkout, _git_auth_env, _run_git_cmd,
)

logger = logging.getLogger(__name__)

# Tools that execute arbitrary code/commands on the host. Gated per-context
# by AgentCore(host_exec=...) — see config.agent_host_exec.
HOST_EXEC_TOOLS = frozenset({
    "code_execute", "run_command",
    "git_sync_repo", "git_status", "git_create_branch",
    "git_merge", "git_commit", "git_push_pr",
})

# The order the model sees the tools in (kept stable across refactors).
_ORDER = ['remember', 'recall', 'create_graph_node', 'link_concepts', 'read_file', 'write_file', 'list_workspace_files', 'web_search', 'web_fetch', 'create_task', 'send_telegram', 'index_workspace', 'link_topic_similarity', 'list_merge_proposals', 'approve_merge', 'reject_merge', 'force_merge', 'rerun_job', 'get_self_documentation', 'create_skill', 'index_artifact', 'save_transcript_artifact', 'list_source_adapters', 'ingest_source', 'batch_ingest_sources', 'extract_topics', 'save_last_response', 'show_file', 'download_file', 'generate_image', 'get_job_status', 'list_recent_jobs', 'consolidate_memory', 'code_execute', 'run_command', 'github_list_connections', 'github_read_file', 'github_list_directory', 'github_list_branches', 'github_list_pulls', 'github_create_branch', 'github_delete_branch', 'github_write_files', 'github_create_pr', 'github_merge_pr', 'start_coding_task', 'save_to_artifact', 'update_artifact', 'read_artifact', 'list_artifacts', 'convert_document', 'batch_convert_documents', 'git_sync_repo', 'git_status', 'git_create_branch', 'git_merge', 'git_commit', 'git_push_pr', 'analyze_company_financials', 'compare_company_strategy_and_risks', 'analyze_earnings_call']

_BY_NAME = {s["function"]["name"]: s for m in (
    memory, files, artifacts, web, sources, tasks, skills, images, code, github, git, finance,
) for s in getattr(m, "SCHEMAS", [])}
TOOL_SCHEMAS: list[dict[str, Any]] = [_BY_NAME[n] for n in _ORDER] + [
    s for n, s in _BY_NAME.items() if n not in _ORDER]


def host_exec_allowed(context: str) -> bool:
    """Whether host-exec tools are offered in ``context``.

    context: "interactive" (web UI chat, coding_task) or "background"
    (autonomous/scheduled jobs, iteration loops, messaging bots).
    """
    from config import get_settings
    mode = (get_settings().agent_host_exec or "interactive").strip().lower()
    if mode == "never":
        return False
    if mode == "always":
        return True
    return context == "interactive"


def get_all_tool_schemas(project_id: str | None = None) -> list[dict[str, Any]]:
    """Return built-in tools + browser tools (if enabled) + any MCP-provided tools.

    project_id is accepted for back-compat but ignored — MCP servers are
    enabled globally now (per-project enablement was removed).
    """
    schemas = list(TOOL_SCHEMAS)
    try:
        from agent.browser_tools import browser_enabled, BROWSER_TOOL_SCHEMAS
        if browser_enabled():
            schemas.extend(BROWSER_TOOL_SCHEMAS)
    except Exception as e:
        logger.debug("Browser tools unavailable: %s", e)
    try:
        from mcp_client.manager import get_mcp_manager
        mgr = get_mcp_manager()
        mcp_schemas = mgr.get_all_tool_schemas() or []
        if mcp_schemas:
            schemas.extend(mcp_schemas)
            logger.debug("Added %d MCP tools to agent schema", len(mcp_schemas))
    except Exception as e:
        logger.debug("No MCP tools available: %s", e)
    return schemas


async def execute_tool(
    tool_name: str,
    tool_args: dict[str, Any],
    memory_manager: Any,
    project_id: str | None = None,
    session_id: str | None = None,
    last_assistant_text: str = "",
    interactive: bool = False,
    host_exec: bool = False,
) -> str:
    """Execute a tool call and return the result as a string.

    ``interactive`` is True only for turns driven by a person in the web UI;
    it gates actions that assume the user just approved something.
    ``host_exec`` must be the caller's AgentCore.host_exec: host-execution
    tools are refused here too, not only hidden from the schema.
    """
    if tool_name in HOST_EXEC_TOOLS and not host_exec:
        return (
            f"Tool '{tool_name}' is disabled in this context "
            "(host command execution is only available in "
            "interactive chat; see AGENT_HOST_EXEC)."
        )
    try:
        # Route MCP tool calls through the MCP manager
        if tool_name.startswith("mcp_"):
            try:
                from mcp_client.manager import get_mcp_manager
                mgr = get_mcp_manager()
                return await mgr.execute_tool(tool_name, tool_args)
            except Exception as e:
                logger.error("MCP tool dispatch failed for '%s': %s", tool_name, e)
                return f"MCP tool error: {e}"

        handler = resolve(tool_name)
        if handler is None:
            return f"Unknown tool: {tool_name}"
        ctx = ToolContext(
            memory_manager=memory_manager,
            project_id=project_id,
            session_id=session_id,
            last_assistant_text=last_assistant_text,
            interactive=interactive,
            host_exec=host_exec,
        )
        return await handler(ctx, tool_name, tool_args)
    except Exception as e:
        logger.error(f"Tool '{tool_name}' error: {e}", exc_info=True)
        return f"Error executing {tool_name}: {str(e)}"

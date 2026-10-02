"""autoresearch handler - the evolutionary optimisation loop (utils/autoresearch.py) as a job.

The autoresearch skill used to run ``python utils/autoresearch.py`` through
code_execute, which runs a snippet in a temp dir with a 300 s limit: the
relative path didn't resolve there and a 10-round loop couldn't finish. As a
job it runs in-process against the project's workspace, reports progress per
round, can be cancelled between rounds, and saves its report as an artifact.

The benchmark command is a shell command on the host, so the job runs only
when host exec is allowed for interactive use (AGENT_HOST_EXEC), and the
start_autoresearch tool queues it only from the web chat.

Payload shape:
    {
      "target_file": str,     # path relative to the project workspace
      "eval_cmd": str,        # shell command run in the workspace after each mutation
      "metric": str,          # metric name printed by eval_cmd ("time: 1.23")
      "direction": "min"|"max",
      "iterations": int,      # default 10, clamped to [1, 50]
      "instructions": str,    # optimisation guidance for the mutation model
    }
"""
from __future__ import annotations

import logging
from typing import Any

from jobs.context import JobContext
from jobs.handlers import register

logger = logging.getLogger(__name__)


@register("autoresearch", default_timeout_seconds=7200,
          description="Evolutionary optimisation of one workspace file against a benchmark command.")
async def handle_autoresearch(ctx: JobContext) -> dict[str, Any]:
    from agent.tools import host_exec_allowed
    from agent.tools.workspace import _get_workspace_base
    from utils.autoresearch import AutoresearchRunner

    if not host_exec_allowed("interactive"):
        return {"status": "failed", "error": "AGENT_HOST_EXEC=never: the benchmark command can't run on this host"}
    pl = ctx.payload or {}
    workspace = _get_workspace_base(ctx.project_id)
    target = (workspace / (pl.get("target_file") or "")).resolve()
    if not target.is_relative_to(workspace) or not target.is_file():
        return {"status": "failed", "error": f"target_file not found in the project workspace: {pl.get('target_file')!r}"}
    eval_cmd = (pl.get("eval_cmd") or "").strip()
    metric = (pl.get("metric") or "").strip()
    if not eval_cmd or not metric:
        return {"status": "failed", "error": "eval_cmd and metric are required"}
    direction = "max" if str(pl.get("direction") or "min").lower() == "max" else "min"
    iterations = max(1, min(int(pl.get("iterations") or 10), 50))

    async def on_iteration(i: int, entry: dict[str, Any]) -> None:
        await ctx.heartbeat(progress=f"Round {i}/{iterations}: {entry.get('status')} "
                                     f"({metric}={entry.get('metric')})")

    runner = AutoresearchRunner(
        target_path=target, eval_cmd=eval_cmd, metric_name=metric, direction=direction,
        max_iterations=iterations, instructions=pl.get("instructions") or "Optimize performance and keep it correct.",
        workspace_dir=workspace, on_iteration=on_iteration, should_stop=ctx.cancel_requested,
    )
    await ctx.heartbeat(progress=f"Baseline: running {eval_cmd!r}")
    await runner.execute_loop()

    report_path = workspace / "autoresearch_report.md"
    artifact_id = None
    if report_path.is_file():
        try:
            from artifacts.store import get_store
            art = get_store().create(
                project_id=ctx.project_id, path=f"autoresearch/report-{ctx.job_id[:8]}.md",
                content_type="text/markdown", content=report_path.read_text(encoding="utf-8"),
                title=f"Autoresearch: {pl.get('target_file')}", source={"kind": "job", "job_id": ctx.job_id})
            artifact_id = art["id"]
        except Exception as e:
            logger.warning("autoresearch report not saved as an artifact: %s", e)
    kept = [h["iteration"] for h in runner.history if h.get("status") == "SUCCESS"]
    best = runner.best_metric if runner.best_metric not in (float("inf"), float("-inf")) else None
    return {"status": "completed", "target_file": pl.get("target_file"), "metric": metric, "direction": direction,
            "initial_metric": runner.initial_metric, "best_metric": best, "rounds": len(runner.history),
            "kept_rounds": kept, "report_artifact_id": artifact_id}

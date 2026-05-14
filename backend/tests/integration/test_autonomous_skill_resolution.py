"""End-to-end smoke test for the autonomous-task skill-resolution chain.

Validates structurally that the key invariants hold:
  1. create_task accepts skill_name in its schema.
  2. schedule_agent_task signature accepts skill_name.
  3. _enqueue_autonomous_job carries skill_name into the job payload.
  4. The autonomous handler resolves payload.skill_name (with
     underscore<->hyphen tolerance), validates requires_mcp against
     the live MCP manager, and aborts cleanly on missing connectors.

These tests intentionally do NOT spin up APScheduler or hit the LLM
provider — they verify the wiring at the Python-AST and direct
function-call level, which is what the user actually needs assurance
of after the fix lands.

Run: pytest backend/tests/integration/test_autonomous_skill_resolution.py -v
"""
from __future__ import annotations

import inspect
import os
import re
import textwrap
from pathlib import Path

import pytest

# Anchor data dir into a tmp location so import-time settings.ensure_dirs()
# doesn't try to create /app/data.
os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")
os.makedirs("/tmp/pantheon-tests-data/db", exist_ok=True)


_ROOT = Path(__file__).resolve().parents[2]


def _read(rel_path: str) -> str:
    return (_ROOT / rel_path).read_text(encoding="utf-8")


def test_create_task_schema_has_skill_name():
    """The agent-facing create_task schema exposes skill_name as an
    optional string property with a description that points at
    /skill-name resolution."""
    src = _read("agent/tools.py")
    # Find the create_task schema's properties block.
    m = re.search(
        r'"name": "create_task",.*?"properties": \{(.*?)\},\s*"required":',
        src, re.DOTALL,
    )
    assert m, "create_task schema not found"
    props = m.group(1)
    assert '"skill_name"' in props, "skill_name property missing from create_task schema"
    assert "registered" in props.lower() or "slug" in props.lower(), \
        "skill_name description should mention registration/slug"


def test_scheduler_signature_accepts_skill_name():
    pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed in test env")
    """schedule_agent_task and _enqueue_autonomous_job both accept
    a skill_name kwarg."""
    from tasks import scheduler
    sig = inspect.signature(scheduler.schedule_agent_task)
    assert "skill_name" in sig.parameters, \
        "schedule_agent_task missing skill_name param"
    sig2 = inspect.signature(scheduler._enqueue_autonomous_job)
    assert "skill_name" in sig2.parameters, \
        "_enqueue_autonomous_job missing skill_name param"


async def test_enqueue_payload_carries_skill_name(monkeypatch):
    """The payload dict that _enqueue_autonomous_job passes to
    JobStore.create() includes skill_name. Runtime check — resilient
    to refactors of how the payload literal is constructed."""
    pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed in test env")
    from tasks import scheduler
    from jobs import store as jobs_store

    captured: dict = {}

    class FakeStore:
        def create(self, **kwargs):
            captured.update(kwargs)
            return "fake-job-id"

    monkeypatch.setattr(jobs_store, "get_store", lambda: FakeStore())

    await scheduler._enqueue_autonomous_job(
        task_id="t1", task_name="smoke", description="d",
        skill_name="my-skill",
    )

    assert "payload" in captured, "no payload kwarg passed to store.create"
    assert captured["payload"].get("skill_name") == "my-skill", \
        f"skill_name not forwarded into payload; got {captured['payload']}"


def test_handler_resolves_skill_with_underscore_tolerance():
    """The handler's skill resolution loop should handle slugs with
    either separator. We verify the loop structure exists; full
    behavior is tested via test_handler_aborts_when_required_mcp_missing."""
    src = _read("jobs/handlers/autonomous_task.py")
    assert 'pl.get("skill_name")' in src, \
        "handler not reading skill_name from payload"
    # Tolerance loop checks for both _-> and -> _ variants.
    assert 'replace("_", "-")' in src and 'replace("-", "_")' in src, \
        "handler missing underscore<->hyphen tolerance"
    # Resolution feeds into AgentCore via skill_context + active_skill_name.
    assert "skill_context=skill_context" in src, \
        "handler not passing skill_context to AgentCore"
    assert "active_skill_name=active_skill_name" in src, \
        "handler not passing active_skill_name to AgentCore"
    # project_name plumbing.
    assert "project_name=project_name" in src, \
        "handler not passing project_name to AgentCore"


def test_handler_validates_requires_mcp():
    """The handler walks skill.manifest.requires_mcp against the
    current MCP tool registry and aborts on missing tools."""
    src = _read("jobs/handlers/autonomous_task.py")
    assert "requires_mcp" in src, "handler missing requires_mcp validation"
    # Should consult the MCP manager.
    assert "get_mcp_manager" in src, \
        "handler not consulting MCP manager for precondition check"
    # Should fail-fast with a 'failed' status when missing.
    assert "missing_mcp_tools" in src, \
        "handler doesn't surface missing tools in result"


@pytest.mark.asyncio
async def test_handler_aborts_when_required_mcp_missing(monkeypatch):
    # Skip cleanly if the test environment lacks deps needed to import
    # the handler (apscheduler/sqlalchemy chain).
    pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed in test env")

    """End-to-end: a fake skill with an impossible MCP requirement
    causes the handler to return status='failed' before running the
    agent loop."""
    # Register a fake skill with an impossible MCP requirement.
    from skills.models import (
        SkillManifest, LoadedSkill,
        PantheonExtensions, MemoryConfig,
    )
    from skills.registry import get_skill_registry

    manifest_kwargs = dict(
        name="needs-fake-mcp",
        description="test skill",
        version="1.0.0",
        triggers=["needs fake mcp"],
        tags=["test"],
        pantheon=PantheonExtensions(memory=MemoryConfig(), project_aware=False),
    )
    # requires_mcp may not be a model field on every SkillManifest version;
    # add it via setattr after construction so the test stays compatible.
    manifest = SkillManifest(**manifest_kwargs)
    object.__setattr__(manifest, "requires_mcp", ("mcp_FakeServer_doThing",))

    sk = LoadedSkill(
        manifest=manifest, instructions="# test", skill_dir="/tmp/nofake",
        is_bundled=False, disabled_projects=[],
    )
    registry = get_skill_registry()
    registry._skills[sk.name] = sk

    # Stub the MCP manager so it returns no tools.
    from mcp_client import manager as _mcp_mod
    class FakeMgr:
        def get_all_tool_schemas(self): return []
    monkeypatch.setattr(_mcp_mod, "_manager", FakeMgr())

    # Build a fake JobContext.
    from jobs.handlers.autonomous_task import handle_autonomous_task

    class FakeCtx:
        def __init__(self):
            self.job_id = "test-job-id-12345678"
            self.title = "smoke"
            self.description = "smoke task"
            self.project_id = "test-project"
            self.payload = {
                "task_name": "smoke", "description": "smoke task",
                "schedule": "now", "skill_name": "needs-fake-mcp",
            }
            self._results: list[dict] = []
        def cancel_requested(self): return False
        async def heartbeat(self, **kwargs): pass
        def update_result(self, d): self._results.append(d)

    ctx = FakeCtx()
    result = await handle_autonomous_task(ctx)
    assert result.get("status") == "failed", \
        f"expected failed status, got {result}"
    assert "mcp_FakeServer_doThing" in result.get("error", ""), \
        f"expected missing-tool error, got {result.get('error')}"

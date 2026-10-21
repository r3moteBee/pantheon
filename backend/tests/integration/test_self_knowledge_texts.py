"""Model-facing texts about Pantheon itself stay consistent with the code
(agent.md refresh on updates, prompt + tool descriptions, honest tool errors)."""
from __future__ import annotations

import asyncio
import hashlib
import os

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")
os.makedirs("/tmp/pantheon-tests-data/db", exist_ok=True)

from pathlib import Path

from agent import personality


# ── agent.md: follow the bundled default unless the user edited it ──────────

def _bundled(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "bundled" / "agent.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_agent_md_copied_when_missing(tmp_path):
    b = _bundled(tmp_path, "# Guide v2\n")
    d = tmp_path / "data"
    assert personality.refresh_default_agent_md(d, b) == "copied"
    assert (d / "agent.md").read_text() == "# Guide v2\n"
    assert personality.refresh_default_agent_md(d, b) == "current"


def test_agent_md_refreshed_when_unedited_since_last_copy(tmp_path):
    d = tmp_path / "data"
    personality.refresh_default_agent_md(d, _bundled(tmp_path, "# Guide v1\n"))
    assert personality.refresh_default_agent_md(d, _bundled(tmp_path, "# Guide v2\n")) == "refreshed"
    assert (d / "agent.md").read_text() == "# Guide v2\n"


def test_agent_md_old_shipped_copy_refreshed_without_marker(tmp_path):
    """Installs from before the marker: a byte-identical old default is replaced."""
    d = tmp_path / "data"
    d.mkdir()
    old = "stale default\n"
    (d / "agent.md").write_text(old)
    orig = personality.SHIPPED_AGENT_MD_SHA256
    try:
        personality.SHIPPED_AGENT_MD_SHA256 = orig | {hashlib.sha256(old.encode()).hexdigest()}
        assert personality.refresh_default_agent_md(d, _bundled(tmp_path, "# new\n")) == "refreshed"
    finally:
        personality.SHIPPED_AGENT_MD_SHA256 = orig
    assert (d / "agent.md").read_text() == "# new\n"


def test_agent_md_user_edits_kept(tmp_path):
    d = tmp_path / "data"
    personality.refresh_default_agent_md(d, _bundled(tmp_path, "# Guide v1\n"))
    (d / "agent.md").write_text("# my own rules\n")
    assert personality.refresh_default_agent_md(d, _bundled(tmp_path, "# Guide v2\n")) == "kept-edited"
    assert (d / "agent.md").read_text() == "# my own rules\n"


def test_stale_tracked_agent_md_is_not_in_the_repo():
    repo = Path(__file__).resolve().parents[3]
    import subprocess
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "data/personality"], capture_output=True, text=True)
    if out.returncode == 0:
        assert "data/personality/agent.md" not in out.stdout.split()


# ── prompt + tool texts ──────────────────────────────────────────────────────

def _prompt(**kw):
    from agent.prompts import build_system_prompt
    return build_system_prompt(project_id="default", **kw)


def test_prompt_has_no_dead_or_user_specific_references():
    from agent.prompts import MEMORY_GUIDANCE
    p = _prompt()
    assert "[archive]" not in MEMORY_GUIDANCE         # recall never returns archival entries
    assert "NBJ" not in p
    assert "turn-N.md" not in p
    from agent.tools import tasks
    schema = next(x for x in tasks.SCHEMAS if x["function"]["name"] == "create_task")
    assert "iteration/<job id, 8 chars>/turn-01.md" in str(schema)
    assert "send_telegram` step is obvious" not in p


def test_prompt_mentions_run_build_only_with_a_builder(monkeypatch):
    import builder
    monkeypatch.setattr(builder, "builder_enabled", lambda: False)
    assert "run_build" not in _prompt()
    monkeypatch.setattr(builder, "builder_enabled", lambda: True)
    assert "run_build" in _prompt()


def test_skip_review_rules_agree():
    from agent.tools import tasks
    p = _prompt()
    schema = next(s for s in tasks.SCHEMAS if s["function"]["name"] == "create_task")["function"]
    skip = schema["parameters"]["properties"]["skip_review"]["description"]
    assert "approved this exact plan" in skip
    assert "skip_review: true" in p and "PAUSED PROPOSAL" in p
    assert "Ignored outside the web chat" in skip


def test_code_execute_description_matches_sandbox_mode():
    from agent.tools import code
    d = next(s for s in code.SCHEMAS if s["function"]["name"] == "code_execute")["function"]["description"]
    assert "isolated sandbox" not in d
    assert ("NO filesystem isolation" in d) or ("Firecracker" in d)


def test_errors_do_not_point_at_hidden_tools_or_wrong_pages():
    import inspect
    from agent.tools import artifacts, github, git, memory
    for mod in (artifacts, github, git, memory):
        src = inspect.getsource(mod)
        assert "Settings → Sources" not in src and "Settings → Connections" not in src
    assert "call write_file directly" not in inspect.getsource(artifacts)
    msrc = inspect.getsource(memory)
    assert "use `index_artifact(" not in msrc and "prefer `index_artifact`" not in msrc


def test_send_telegram_reports_when_not_configured(monkeypatch):
    from agent import tools
    from messaging.adapters import telegram
    monkeypatch.setattr(telegram, "_application", None)
    out = asyncio.run(tools.execute_tool("send_telegram", {"message": "hi"}, None))
    assert out.startswith("Telegram send failed") and "not set up" in out


# ── questions about Pantheon read the self-doc first ─────────────────────────

def test_capability_questions_read_the_self_doc():
    from agent.freshness import wants_self_description
    for q in ("Can you compile Swift code?", "Where do files I upload in the chat go?",
              "What's the most tool steps you can take in one turn?", "Which model is answering me right now?",
              "Can you message me when a background job finishes?", "How long can a single reply of yours be?",
              "If I ask you to schedule a daily digest, what happens?"):
        assert wants_self_description(q), q


def test_ordinary_requests_and_world_questions_do_not():
    from agent.freshness import wants_self_description, needs_fresh_facts
    for q in ("Can you write me a poem about cats?", "Can you run this script for me?",
              "Where do penguins go in winter?", "What happens if I mix bleach and vinegar?",
              "How many moons does Jupiter have?",
              "Can you summarize this article: " + "x" * 300):
        assert not wants_self_description(q), q
    # the web-search guards are unchanged by it
    assert needs_fresh_facts("Can you search the web for the latest Rust release?")

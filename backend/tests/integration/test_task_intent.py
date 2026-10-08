"""Future-time requests go to create_task; tasks the user asked for skip review (agent/task_intent.py)."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from agent.task_intent import explicit_task_request, future_timing, review_reason  # noqa: E402

LATER = [
    "In 15 minutes, check the llama.cpp releases page.",
    "Remind me tomorrow at 9am to renew the domain.",
    "Fetch the blog now, and next week compare it with what is there then.",
    "Every morning at 8, send me a summary of new releases.",
    "I'm uploading a PDF tonight. Once it's in, summarise it.",
    "Give me a weekly digest of Home Assistant news.",
    "When the backup finishes, check its log.",
]
NOW = [
    "What is the latest Node.js LTS version?",
    "Summarise this page: https://example.com/a",
    "Generate an image of a red bicycle.",
    "What happened in the news this week?",
    "Compare Immich and PhotoPrism.",
]


@pytest.mark.parametrize("text", LATER)
def test_future_timing_is_spotted(text):
    assert future_timing(text) and explicit_task_request(text)


@pytest.mark.parametrize("text", NOW)
def test_present_requests_are_not_timed(text):
    assert future_timing(text) is None and not explicit_task_request(text)


def test_explicit_task_wording_counts_without_a_time():
    assert explicit_task_request("Set up a research task on self-hosted photo apps.")
    assert explicit_task_request("Run this in the background and save the result.")


def test_review_reason_holds_plans_that_can_lose_data():
    assert review_reason({"plan": "1. Search with web_search. 2. Save with save_to_artifact."}) is None
    assert review_reason({"plan": "1. List artifacts. 2. delete_artifact each old one."})
    assert review_reason({"plan": "1. Read the notes. 2. Overwrite the summary artifact."})
    assert review_reason({"plan": "1. Commit.", "job_type": "iteration_loop", "branch_strategy": "main"})


# ---- create_task -----------------------------------------------------------------------------

async def _create(user_requested: bool, interactive: bool = True, plan: str = "1. Search with web_search."):
    from agent.tools import execute_tool
    sched = AsyncMock(return_value="sched-1")
    with patch("tasks.scheduler.schedule_agent_task", sched):
        result = await execute_tool("create_task", {"name": "Release check", "description": "check releases",
                                                    "schedule": "delay:15", "plan": plan},
                                    memory_manager=None, project_id="p", session_id="s",
                                    interactive=interactive, user_requested_task=user_requested)
    return result, sched.call_args.kwargs["plan_status"]


@pytest.mark.asyncio
async def test_task_the_user_asked_for_is_scheduled_right_away():
    result, status = await _create(user_requested=True)
    assert status == "approved" and result.startswith("Task scheduled (the user asked")


@pytest.mark.asyncio
async def test_task_the_agent_decided_on_still_waits_for_review():
    result, status = await _create(user_requested=False)
    assert status == "proposed" and result.startswith("Task PROPOSED")


@pytest.mark.asyncio
async def test_requested_task_that_can_lose_data_waits_for_review():
    result, status = await _create(user_requested=True, plan="1. delete_artifact for each draft.")
    assert status == "proposed"


@pytest.mark.asyncio
async def test_background_runs_never_skip_review():
    result, status = await _create(user_requested=True, interactive=False)
    assert status == "proposed"


# ---- the agent loop --------------------------------------------------------------------------

TOOLS = [{"type": "function", "function": {"name": "create_task", "parameters": {}}}]


class _Prov:
    """Round 1 calls create_task, round 2 answers with `reply`; records what it was sent."""
    model, task_class = "m", "agent"

    def __init__(self, reply):
        self.reply, self.calls, self.seen = reply, 0, []

    async def chat(self, messages, tools=None, stream=True, **kw):
        self.calls += 1
        self.seen.append([dict(m) for m in messages])
        if self.calls == 1:
            yield {"type": "tool_call", "id": "t1", "name": "create_task",
                   "args": {"name": "Release check", "description": "d", "schedule": "delay:15", "plan": "1. web_search"}}
        else:
            yield {"type": "text_delta", "content": self.reply}
        yield {"type": "done"}


async def _run(message, reply, tool_result):
    from agent.core import AgentCore
    from config import get_settings
    agent = AgentCore(provider=_Prov(reply), memory_manager=None, project_id="p", session_id="s", interactive=True)
    seen_flags = []

    async def fake_tool(tool_name, tool_args, **kw):
        seen_flags.append(kw.get("user_requested_task"))
        return tool_result
    with patch.object(get_settings(), "agent_force_search", False), \
         patch.object(get_settings(), "agent_thinking", False), \
         patch("agent.core.get_all_tool_schemas", return_value=TOOLS), \
         patch("agent.core.build_system_prompt", return_value="sys"), \
         patch("agent.core.execute_tool", side_effect=fake_tool), \
         patch("agent.context_fit.context_window", return_value=None):
        events = [e async for e in agent.chat(message)]
    text = "".join(e.get("content", "") for e in events if e["type"] == "text_delta")
    return agent.provider, seen_flags, text


def _user_text(msgs):
    c = [m for m in msgs if m["role"] == "user"][-1]["content"]
    return c if isinstance(c, str) else "".join(p.get("text", "") for p in c)


@pytest.mark.asyncio
async def test_future_request_gets_the_note_and_the_flag():
    prov, flags, _ = await _run("In 15 minutes, check the llama.cpp releases.", "Queued for 15 minutes from now.",
                                "Task scheduled (the user asked for it in this chat ...)")
    assert "Part of this request happens later" in _user_text(prov.seen[0])
    assert flags == [True]


@pytest.mark.asyncio
async def test_present_request_gets_neither():
    prov, flags, _ = await _run("What is the latest llama.cpp release?", "It is b11476.", "Task PROPOSED - paused")
    assert "happens later" not in _user_text(prov.seen[0])
    assert flags == [False]


@pytest.mark.asyncio
async def test_a_proposal_the_reply_does_not_mention_gets_a_note():
    _, _, text = await _run("What is the latest llama.cpp release?", "I set that up for you.",
                            "Task PROPOSED — paused, awaiting your approval.")
    assert "Waiting for your approval" in text and "Release check" in text


@pytest.mark.asyncio
async def test_no_note_when_the_reply_already_asks_for_approval():
    _, _, text = await _run("What is the latest llama.cpp release?", "Please approve it in the Tasks tab.",
                            "Task PROPOSED — paused, awaiting your approval.")
    assert "Waiting for your approval" not in text


# ---- repeats nobody asked for; image requests (2026-10-08 research episode) ------------------

from agent.task_intent import asks_to_repeat, wants_image  # noqa: E402


@pytest.mark.parametrize("text", ["Every morning at 8, summarise new releases.", "Give me a weekly digest.",
                                  "Keep checking the page and tell me when it changes.", "Monitor the releases."])
def test_repeat_requests(text):
    assert asks_to_repeat(text)


@pytest.mark.parametrize("text", ["Run this research in the background.", "Remind me tomorrow at 9am.",
                                  "In 15 minutes, check the releases page.", "Track what changed in ROCm this month."])
def test_one_off_requests(text):
    assert not asks_to_repeat(text)


@pytest.mark.parametrize("text", ["Make an infographic of the summary.", "Generate an image of a red bicycle.",
                                  "Create a poster for the event.", "Draw a lighthouse at dusk."])
def test_picture_requests(text):
    assert wants_image(text)


@pytest.mark.parametrize("text", ["Update the Docker image for Jellyfin.", "Which container image tag is newest?",
                                  "Rebuild the podman image and push it.", "Flash the firmware image to the board."])
def test_container_and_disk_images_are_not_pictures(text):
    assert not wants_image(text)


async def _create_sched(message, schedule):
    from agent.tools import execute_tool
    sched = AsyncMock(return_value="sched-1")
    with patch("tasks.scheduler.schedule_agent_task", sched):
        result = await execute_tool("create_task", {"name": "Research", "description": "d", "schedule": schedule,
                                                    "plan": "1. web_search"},
                                    memory_manager=None, project_id="p", session_id="s", interactive=True,
                                    user_requested_task=True, user_message=message)
    return result, sched.called


@pytest.mark.asyncio
async def test_unrequested_repeat_is_rejected():
    result, scheduled = await _create_sched("Research ROCm changes in the background.", "interval:60")
    assert not scheduled and "does not" not in result and result.startswith("create_task rejected")
    assert "'now'" in result


@pytest.mark.asyncio
async def test_requested_repeat_is_scheduled():
    result, scheduled = await _create_sched("Every morning at 8, summarise new llama.cpp releases.", "0 8 * * *")
    assert scheduled


@pytest.mark.asyncio
async def test_one_off_later_is_scheduled():
    result, scheduled = await _create_sched("Remind me tomorrow at 9am to renew the domain.", "delay:600")
    assert scheduled


@pytest.mark.asyncio
async def test_image_request_gets_the_image_note():
    prov, _, _ = await _run("Make an infographic of the summary.", "Here it is.", "ok")
    assert "This asks for an image" in _user_text(prov.seen[0])


@pytest.mark.asyncio
async def test_a_job_type_given_as_skill_becomes_the_job_type():
    from agent.tools import execute_tool
    sched = AsyncMock(return_value="sched-1")
    with patch("tasks.scheduler.schedule_agent_task", sched):
        result = await execute_tool("create_task", {"name": "Research", "description": "d", "schedule": "now",
                                                    "plan": "1. web_search", "skill_name": "research_batch",
                                                    "items": ["a", "b"], "item_question": "What about {item}?"},
                                    memory_manager=None, project_id="p", session_id="s", interactive=True,
                                    user_requested_task=True, user_message="Research these in the background.")
    assert sched.called and sched.call_args.kwargs["job_type"] == "research_batch"
    assert sched.call_args.kwargs["skill_name"] is None

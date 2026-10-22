"""Future-time requests become create_task plans; repeats and pictures (agent/task_intent.py)."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from agent.task_intent import future_time_note, future_timing  # noqa: E402

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
    assert future_timing(text)


@pytest.mark.parametrize("text", NOW)
def test_present_requests_are_not_timed(text):
    assert future_timing(text) is None


def test_future_note_keeps_the_approval_flow():
    # One approval rule (prompts.py): wording alone never schedules a task.
    note = future_time_note("in 15 minutes")
    assert "approve" in note and "skip_review" in note and "delay:N" in note


# ---- create_task -----------------------------------------------------------------------------

async def _create(interactive: bool = True, skip_review: bool = False, channel: str | None = "web",
                  message: str = "In 15 minutes, check the releases."):
    from agent.tools import execute_tool
    sched = AsyncMock(return_value="sched-1")
    with patch("tasks.scheduler.schedule_agent_task", sched):
        result = await execute_tool("create_task", {"name": "Release check", "description": "check releases",
                                                    "schedule": "delay:15", "plan": "1. Search with web_search.",
                                                    "skip_review": skip_review},
                                    memory_manager=None, project_id="p", session_id="s",
                                    interactive=interactive, channel=channel, user_message=message)
    return result, sched.call_args.kwargs["plan_status"]


@pytest.mark.asyncio
async def test_a_requested_task_still_waits_for_approval():
    # #45 once scheduled tasks approved from the wording of the message; main's single approval rule wins.
    result, status = await _create()
    assert status == "proposed" and result.startswith("Task PROPOSED")


@pytest.mark.asyncio
async def test_background_and_bot_runs_never_skip_review():
    assert (await _create(interactive=False, skip_review=True, channel=None))[1] == "proposed"
    assert (await _create(interactive=False, skip_review=True, channel="telegram"))[1] == "proposed"


@pytest.mark.asyncio
async def test_bots_get_no_user_message():
    from agent.tools import execute_tool
    seen = {}

    async def handler(ctx, name, args):
        seen["msg"] = ctx.user_message
        return "ok"
    with patch("agent.tools.resolve", return_value=handler):
        await execute_tool("x", {}, memory_manager=None, interactive=True, channel="telegram", user_message="hi")
        bot = seen["msg"]
        await execute_tool("x", {}, memory_manager=None, interactive=True, channel="web", user_message="hi")
    assert bot == "" and seen["msg"] == "hi"


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
        seen_flags.append(kw.get("user_message"))
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
async def test_future_request_gets_the_note_and_tools_see_the_message():
    prov, flags, _ = await _run("In 15 minutes, check the llama.cpp releases.", "Please approve the plan.",
                                "Task PROPOSED - paused")
    assert "Part of this request happens later" in _user_text(prov.seen[0])
    assert flags == ["In 15 minutes, check the llama.cpp releases."]


@pytest.mark.asyncio
async def test_present_request_gets_neither():
    prov, flags, _ = await _run("What is the latest llama.cpp release?", "It is b11476.", "Task PROPOSED - paused")
    assert "happens later" not in _user_text(prov.seen[0])


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


@pytest.mark.parametrize("text", ["What is in this image?", "Describe the attached picture.",
                                  "Update the Docker image for Jellyfin.", "Which container image tag is newest?",
                                  "Rebuild the podman image and push it.", "Flash the firmware image to the board."])
def test_vision_questions_and_container_images_are_not_picture_requests(text):
    assert not wants_image(text)


async def _create_sched(message, schedule):
    from agent.tools import execute_tool
    sched = AsyncMock(return_value="sched-1")
    with patch("tasks.scheduler.schedule_agent_task", sched):
        result = await execute_tool("create_task", {"name": "Research", "description": "d", "schedule": schedule,
                                                    "plan": "1. web_search"},
                                    memory_manager=None, project_id="p", session_id="s", interactive=True,
                                    channel="web", user_message=message)
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
async def test_the_approval_turn_still_sees_the_repeat_request():
    # create_task runs on "yes, go ahead"; the request with "every morning" came a turn earlier.
    result, scheduled = await _create_sched("Every morning at 8, summarise new llama.cpp releases.\nyes, go ahead",
                                            "0 8 * * *")
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
                                    channel="web", user_message="Research these in the background.")
    assert sched.called and sched.call_args.kwargs["job_type"] == "research_batch"
    assert sched.call_args.kwargs["skill_name"] is None


@pytest.mark.asyncio
async def test_tools_see_the_recent_user_messages_on_the_approval_turn():
    from agent.core import AgentCore
    from config import get_settings
    agent = AgentCore(provider=_Prov("Done."), memory_manager=None, project_id="p", session_id="s", interactive=True)
    agent.working_memory = [{"role": "user", "content": "Every morning at 8, summarise new releases."},
                            {"role": "assistant", "content": "Plan: 1. web_search. Approve?"}]
    seen = []

    async def fake_tool(tool_name, tool_args, **kw):
        seen.append(kw.get("user_message"))
        return "Task scheduled"
    with patch.object(get_settings(), "agent_force_search", False), \
         patch.object(get_settings(), "agent_thinking", False), \
         patch("agent.core.get_all_tool_schemas", return_value=TOOLS), \
         patch("agent.core.build_system_prompt", return_value="sys"), \
         patch("agent.core.execute_tool", side_effect=fake_tool), \
         patch("agent.context_fit.context_window", return_value=None):
        [e async for e in agent.chat("yes, go ahead")]
    assert "Every morning at 8" in seen[0] and seen[0].endswith("yes, go ahead")

"""Autonomous task execution — runs the agent without human interaction."""
from __future__ import annotations
import asyncio
import logging
import uuid

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


async def run_autonomous_task(
    task_id: str,
    task_name: str,
    description: str,
    project_id: str = "default",
    schedule: str = "now",
    **kwargs,
) -> None:
    """Execute an autonomous agent task.

    This function runs the full agent loop with the given description as the
    initial prompt, with no human in the loop. Results are saved to episodic
    memory and optionally sent via Telegram.
    """
    logger.info(f"Starting autonomous task: {task_name} (id={task_id})")
    session_id = f"autonomous-{task_id}-{uuid.uuid4().hex[:8]}"

    from memory.episodic import EpisodicMemory
    episodic = EpisodicMemory()

    # Log task start
    await episodic.log_task_event(
        task_id=task_id,
        event="started",
        project_id=project_id,
        task_name=task_name,
        details=f"Task description: {description}",
    )

    try:
        from agent.core import AgentCore
        from memory.manager import create_memory_manager
        from models.provider import get_provider

        provider = get_provider()
        memory = create_memory_manager(
            project_id=project_id,
            session_id=session_id,
            provider=provider,
        )

        agent = AgentCore(
            provider=provider,
            memory_manager=memory,
            project_id=project_id,
            session_id=session_id,
        )

        system_context = (
            f"You are running an autonomous task: '{task_name}'\n"
            f"Task ID: {task_id}\n"
            f"Complete the task fully and save important results to memory. "
            f"Notify via Telegram when done."
        )

        full_task_prompt = f"{system_context}\n\nTask:\n{description}"
        result = await agent.run_autonomous(full_task_prompt)

        # Log completion
        await episodic.log_task_event(
            task_id=task_id,
            event="completed",
            project_id=project_id,
            task_name=task_name,
            details=f"Result: {result[:500] if result else 'No response'}",
        )
        logger.info(f"Autonomous task completed: {task_name}")

        # Notify via Telegram
        try:
            from telegram_bot.bot import send_message_to_all
            msg = f"Task '{task_name}' completed.\n\n{result[:300] if result else 'Done.'}"
            await send_message_to_all(msg)
        except Exception as tg_err:
            logger.debug(f"Telegram notification skipped: {tg_err}")

    except Exception as e:
        logger.error(f"Autonomous task failed: {task_name}: {e}", exc_info=True)
        await episodic.log_task_event(
            task_id=task_id,
            event="failed",
            project_id=project_id,
            task_name=task_name,
            details=str(e),
        )
        try:
            from telegram_bot.bot import send_message_to_all
            await send_message_to_all(f"Task '{task_name}' failed: {str(e)[:200]}")
        except Exception:
            pass

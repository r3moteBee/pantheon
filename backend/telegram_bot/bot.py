"""Telegram bot integration using python-telegram-bot."""
from __future__ import annotations
import asyncio
import logging
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_application: Any = None

# Per-chat active project — maps telegram chat_id (int) -> project_id (str)
_chat_projects: dict[int, str] = {}


def _get_token() -> str:
    """Return the Telegram bot token, preferring vault over .env."""
    try:
        from secrets.vault import get_vault
        vault = get_vault()
        token = vault.get_secret("telegram_bot_token")
        if token:
            return token
    except Exception:
        pass
    return settings.telegram_bot_token or ""


def _get_allowed_ids() -> list[int]:
    """Return allowed chat IDs, preferring vault over .env."""
    try:
        from secrets.vault import get_vault
        vault = get_vault()
        raw = vault.get_secret("telegram_allowed_chat_ids")
        if raw:
            return [int(x.strip()) for x in raw.split(",") if x.strip()]
    except Exception:
        pass
    return settings.telegram_allowed_ids


def _active_project(chat_id: int) -> str:
    """Return the active project for a chat, defaulting to 'default'."""
    return _chat_projects.get(chat_id, "default")


async def start_telegram_bot(*, raise_on_error: bool = False) -> None:
    """Initialize and start the Telegram bot.

    Args:
        raise_on_error: If True, propagate exceptions instead of logging them.
                        Used by restart_telegram_bot() to surface errors to the UI.
    """
    global _application
    token = _get_token()
    if not token:
        logger.info("Telegram bot token not configured, skipping.")
        if raise_on_error:
            raise RuntimeError("No Telegram bot token configured")
        return

    try:
        from telegram import Update
        from telegram.ext import (
            Application,
            CommandHandler,
            MessageHandler,
            filters,
            ContextTypes,
        )

        app = Application.builder().token(token).build()

        # ── /start ──────────────────────────────────────────────────────
        async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not _is_allowed(update):
                return
            project = _active_project(update.effective_chat.id)
            await update.message.reply_text(
                "Hello! I'm your Pantheon AI assistant.\n\n"
                "Commands:\n"
                "/project <name> — Switch active project\n"
                "/projects — List available projects\n"
                "/status — Get agent status\n"
                "/files — List workspace files\n"
                "/task <description> — Create an autonomous task\n"
                "/memory <query> — Search memories\n"
                "/note [text] — Save text/photo/file as a note in the project (attach media with /note caption)\n\n"
                "Or just send a message to chat with the agent.\n\n"
                f"Active project: {project}"
            )

        # ── /project ────────────────────────────────────────────────────
        async def project_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not _is_allowed(update):
                return
            chat_id = update.effective_chat.id
            name = " ".join(context.args).strip() if context.args else ""
            if not name:
                current = _active_project(chat_id)
                await update.message.reply_text(
                    f"Active project: {current}\n\n"
                    "Usage: /project <name>  — switch project\n"
                    "       /projects        — list all projects"
                )
                return
            # Verify project exists
            from api.projects import _load_projects
            existing = list(_load_projects().values())
            match = None
            for p in existing:
                if p["id"] == name or p["name"].lower() == name.lower():
                    match = p
                    break
            if not match:
                names = ", ".join(p["id"] for p in existing) if existing else "(none)"
                await update.message.reply_text(
                    f"Project '{name}' not found.\nAvailable: {names}"
                )
                return
            _chat_projects[chat_id] = match["id"]
            await update.message.reply_text(f"Switched to project: {match['name']} ({match['id']})")

        # ── /projects ───────────────────────────────────────────────────
        async def projects_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not _is_allowed(update):
                return
            from api.projects import _load_projects
            existing = list(_load_projects().values())
            current = _active_project(update.effective_chat.id)
            if not existing:
                await update.message.reply_text("No projects configured.")
                return
            lines = []
            for p in existing:
                marker = " ← active" if p["id"] == current else ""
                lines.append(f"• {p['name']} ({p['id']}){marker}")
            await update.message.reply_text("Projects:\n" + "\n".join(lines))

        # ── /status ─────────────────────────────────────────────────────
        async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not _is_allowed(update):
                return
            from tasks.scheduler import list_jobs
            project = _active_project(update.effective_chat.id)
            jobs = list_jobs()
            status = f"Pantheon Online\nProject: {project}\nScheduled tasks: {len(jobs)}"
            if jobs:
                for j in jobs[:5]:
                    status += f"\n• {j['name']} (next: {j['next_run'] or 'N/A'})"
            await update.message.reply_text(status)

        # ── /files ──────────────────────────────────────────────────────
        async def files_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not _is_allowed(update):
                return
            project = _active_project(update.effective_chat.id)
            cfg = get_settings()
            if project and project != "default":
                workspace = cfg.projects_dir / project / "workspace"
            else:
                workspace = cfg.workspace_dir
            workspace.mkdir(parents=True, exist_ok=True)
            files = list(workspace.glob("**/*"))[:20]
            if not files:
                await update.message.reply_text(f"No files in workspace ({project}).")
                return
            file_list = "\n".join(f"• {f.relative_to(workspace)}" for f in files if f.is_file())
            await update.message.reply_text(f"Workspace files ({project}):\n{file_list}")

        # ── /task ───────────────────────────────────────────────────────
        async def task_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not _is_allowed(update):
                return
            description = " ".join(context.args) if context.args else ""
            if not description:
                await update.message.reply_text("Usage: /task <description>")
                return
            project = _active_project(update.effective_chat.id)
            from tasks.scheduler import schedule_agent_task
            task_id = await schedule_agent_task(
                name=description[:50],
                description=description,
                schedule="now",
                project_id=project,
            )
            await update.message.reply_text(f"Task scheduled ({project})!\nID: {task_id}\n\n{description}")

        # ── /memory ─────────────────────────────────────────────────────
        async def memory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not _is_allowed(update):
                return
            query = " ".join(context.args) if context.args else ""
            if not query:
                await update.message.reply_text("Usage: /memory <search query>")
                return
            project = _active_project(update.effective_chat.id)
            from memory.manager import create_memory_manager
            manager = create_memory_manager(project_id=project)
            results = await manager.recall(query, tiers=["semantic", "episodic"])
            if not results:
                await update.message.reply_text("No relevant memories found.")
                return
            lines = [f"Memory search ({project}): '{query}'\n"]
            for r in results[:5]:
                source = r.get("source", "?")
                content = r.get("content", "")[:200]
                lines.append(f"[{source}] {content}")
            await update.message.reply_text("\n\n".join(lines))

        # ── /note ───────────────────────────────────────────────────────
        async def note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            """Save the message (text, photo, document, or voice caption) as a note in the project's notes/ folder."""
            if not _is_allowed(update):
                return
            from datetime import datetime
            from pathlib import Path
            cfg = get_settings()
            project = _active_project(update.effective_chat.id)

            # Resolve notes dir under project workspace
            if project and project != "default":
                notes_dir = cfg.projects_dir / project / "workspace" / "notes"
            else:
                notes_dir = cfg.workspace_dir / "notes"
            notes_dir.mkdir(parents=True, exist_ok=True)

            msg = update.message
            ts = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
            text_body = (msg.caption or msg.text or "").strip()
            # Strip the leading "/note" command if present in text
            if text_body.startswith("/note"):
                text_body = text_body[len("/note"):].strip()

            saved: list[str] = []

            # 1. Any attached file (photo, document, voice, audio, video)
            tg_file = None
            ext = ".bin"
            if msg.photo:
                tg_file = await msg.photo[-1].get_file()
                ext = ".jpg"
            elif msg.document:
                tg_file = await msg.document.get_file()
                ext = Path(msg.document.file_name or "file").suffix or ".bin"
            elif msg.voice:
                tg_file = await msg.voice.get_file()
                ext = ".ogg"
            elif msg.audio:
                tg_file = await msg.audio.get_file()
                ext = Path(msg.audio.file_name or "audio").suffix or ".mp3"
            elif msg.video:
                tg_file = await msg.video.get_file()
                ext = ".mp4"

            if tg_file is not None:
                attach_name = f"note-{ts}{ext}"
                attach_path = notes_dir / attach_name
                await tg_file.download_to_drive(str(attach_path))
                saved.append(attach_name)

            # 2. Text/markdown note (always write if there is text OR no attachment)
            if text_body or not saved:
                note_name = f"note-{ts}.md"
                note_path = notes_dir / note_name
                lines = [
                    "---",
                    f"date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
                    "source: telegram",
                    f"chat_id: {update.effective_chat.id}",
                ]
                if saved:
                    lines.append(f"attachment: {saved[0]}")
                lines.append("---")
                lines.append("")
                lines.append(text_body or "(no text — see attachment)")
                note_path.write_text("\n".join(lines), encoding="utf-8")
                saved.insert(0, note_name)

            # Index into memory so /memory and recall can find it
            try:
                from memory.manager import create_memory_manager
                mgr = create_memory_manager(project_id=project)
                await mgr.remember(
                    content=f"[telegram note {ts}] {text_body or saved[0]}",
                    tier="semantic",
                    metadata={"source": "telegram_note", "files": saved},
                )
            except Exception as e:
                logger.warning("Failed to index telegram note into memory: %s", e)

            await msg.reply_text(
                f"📝 Saved to {project}/notes/:\n" + "\n".join(f"• {n}" for n in saved)
            )

        # ── Plain message handler (no /chat prefix needed) ─────────────
        async def plain_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not _is_allowed(update):
                return
            message = update.message.text
            if not message or not message.strip():
                return

            await update.message.reply_text("Thinking...")
            try:
                from agent.core import AgentCore
                from memory.manager import create_memory_manager
                from models.provider import get_provider

                chat_id = update.effective_chat.id
                project = _active_project(chat_id)
                provider = get_provider()
                memory = create_memory_manager(
                    project_id=project,
                    session_id=f"telegram-{chat_id}",
                    provider=provider,
                )
                agent = AgentCore(
                    provider=provider,
                    memory_manager=memory,
                    project_id=project,
                    session_id=f"telegram-{chat_id}",
                )
                response = await agent.run_autonomous(message)
                # Split long messages (Telegram 4096 char limit)
                text = response or "No response."
                for i in range(0, len(text), 4000):
                    await update.message.reply_text(text[i:i + 4000])
            except Exception as e:
                await update.message.reply_text(f"Error: {e}")

        app.add_handler(CommandHandler("start", start_cmd))
        app.add_handler(CommandHandler("project", project_cmd))
        app.add_handler(CommandHandler("projects", projects_cmd))
        app.add_handler(CommandHandler("chat", plain_message))  # keep /chat as alias
        app.add_handler(CommandHandler("status", status_cmd))
        app.add_handler(CommandHandler("files", files_cmd))
        app.add_handler(CommandHandler("task", task_cmd))
        app.add_handler(CommandHandler("memory", memory_cmd))
        app.add_handler(CommandHandler("note", note_cmd))
        # Photos/docs/voice with caption starting with /note — also route to note_cmd
        app.add_handler(MessageHandler(
            (filters.PHOTO | filters.Document.ALL | filters.VOICE | filters.AUDIO | filters.VIDEO)
            & filters.CaptionRegex(r"^/note"),
            note_cmd,
        ))
        # Plain text messages (must be last so commands take priority)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, plain_message))

        _application = app
        await app.initialize()
        await app.start()
        # Start polling in background
        asyncio.create_task(app.updater.start_polling(drop_pending_updates=True))
        logger.info("Telegram bot started successfully")

    except ImportError as exc:
        logger.warning(f"Telegram import failed: {exc}")
        if raise_on_error:
            raise RuntimeError(f"Telegram import error: {exc}. Try: pip install 'python-telegram-bot[all]==21.2'")
    except Exception as e:
        logger.error(f"Failed to start Telegram bot: {e}")
        if raise_on_error:
            raise


async def stop_telegram_bot() -> None:
    """Gracefully stop the Telegram bot."""
    global _application
    if _application is None:
        return
    try:
        await _application.updater.stop()
        await _application.stop()
        await _application.shutdown()
        logger.info("Telegram bot stopped")
    except Exception as e:
        logger.warning(f"Error stopping Telegram bot: {e}")
    _application = None


async def restart_telegram_bot() -> dict[str, str]:
    """Stop and restart the Telegram bot with current settings."""
    await stop_telegram_bot()
    try:
        await start_telegram_bot(raise_on_error=True)
    except Exception as e:
        return {"status": "error", "message": str(e)}
    if _application is not None:
        return {"status": "ok", "message": "Telegram bot restarted successfully"}
    return {"status": "error", "message": "Bot did not start (unknown reason)"}


def _is_allowed(update: Any) -> bool:
    """Check if the chat ID is in the allowed list."""
    allowed_ids = _get_allowed_ids()
    if not allowed_ids:
        return True  # No restriction if not configured
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id not in allowed_ids:
        logger.warning(f"Unauthorized Telegram access from chat_id: {chat_id}")
        return False
    return True


async def send_message_to_all(message: str) -> None:
    """Send a message to all allowed Telegram chat IDs."""
    allowed_ids = _get_allowed_ids()
    token = _get_token()
    if not token or not allowed_ids:
        return
    if _application is None:
        logger.debug("Telegram application not initialized, skipping message")
        return
    try:
        for chat_id in allowed_ids:
            await _application.bot.send_message(
                chat_id=chat_id,
                text=message[:4096],
            )
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")

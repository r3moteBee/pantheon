"""Telegram messaging adapter for Pantheon.

Refactored from ``telegram_bot/bot.py`` — same feature set, now conforming
to :class:`~messaging.base.BaseMessagingAdapter`.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from config import get_settings
from messaging.base import BaseMessagingAdapter
from messaging.models import ChannelInfo

logger = logging.getLogger(__name__)
settings = get_settings()

_application: Any = None


class TelegramAdapter(BaseMessagingAdapter):
    """Telegram adapter using ``python-telegram-bot``."""

    name = "telegram"
    display_name = "Telegram"

    # ------------------------------------------------------------------
    # Credential helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_token() -> str:
        """Return the bot token, preferring vault over ``.env``."""
        try:
            from secrets.vault import get_vault
            vault = get_vault()
            token = vault.get_secret("telegram_bot_token")
            if token:
                return token
        except Exception:
            pass
        return settings.telegram_bot_token or ""

    @staticmethod
    def _get_allowed_ids() -> list[int]:
        """Return allowed chat IDs, preferring vault over ``.env``."""
        try:
            from secrets.vault import get_vault
            vault = get_vault()
            raw = vault.get_secret("telegram_allowed_chat_ids")
            if raw:
                return [int(x.strip()) for x in raw.split(",") if x.strip()]
        except Exception:
            pass
        return settings.telegram_allowed_ids

    # ------------------------------------------------------------------
    # BaseMessagingAdapter interface
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        return bool(self._get_token())

    async def is_running(self) -> bool:
        global _application
        return _application is not None

    async def start(self, *, raise_on_error: bool = False) -> None:
        global _application
        token = self._get_token()
        if not token:
            logger.info("Telegram bot token not configured, skipping.")
            if raise_on_error:
                raise RuntimeError("No Telegram bot token configured")
            return

        try:
            from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
            from telegram.ext import (
                Application,
                CallbackQueryHandler,
                CommandHandler,
                MessageHandler,
                filters,
                ContextTypes,
            )

            app = Application.builder().token(token).build()
            adapter = self  # capture for closures

            # ── ACL helper ──────────────────────────────────────────
            def _is_allowed(update: Update) -> bool:
                allowed_ids = adapter._get_allowed_ids()
                if not allowed_ids:
                    return True
                chat_id = update.effective_chat.id if update.effective_chat else None
                if chat_id not in allowed_ids:
                    logger.warning("Unauthorized Telegram access from chat_id: %s", chat_id)
                    return False
                return True

            # ── /start ──────────────────────────────────────────────
            async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
                if not _is_allowed(update):
                    return
                project = adapter.resolve_project(update.effective_chat.id)
                await update.message.reply_text(
                    "Hello! I'm your Pantheon AI assistant.\n\n"
                    "Commands:\n"
                    "/project <name> — Switch active project\n"
                    "/projects — List available projects\n"
                    "/status — Get agent status\n"
                    "/files — List workspace files\n"
                    "/task <description> — Create an autonomous task\n"
                    "/memory <query> — Search memories\n"
                    "/note [text] — Save text/photo/file as a note\n\n"
                    "Or just send a message to chat with the agent.\n\n"
                    f"Active project: {project}"
                )

            # ── /project ────────────────────────────────────────────
            async def project_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
                if not _is_allowed(update):
                    return
                chat_id = update.effective_chat.id
                name = " ".join(context.args).strip() if context.args else ""
                if not name:
                    current = adapter.resolve_project(chat_id)
                    await update.message.reply_text(
                        f"Active project: {current}\n\n"
                        "Usage: /project <name>  — switch project\n"
                        "       /projects        — list all projects"
                    )
                    return
                from api.projects import _load_projects
                existing = list(_load_projects().values())
                match = None
                for p in existing:
                    if p["id"] == name or p["name"].lower() == name.lower():
                        match = p
                        break
                if not match:
                    names = ", ".join(p["id"] for p in existing) if existing else "(none)"
                    await update.message.reply_text(f"Project '{name}' not found.\nAvailable: {names}")
                    return
                # Persist mapping via ChannelStore
                adapter.set_channel_project(chat_id, match["id"])
                await update.message.reply_text(f"Switched to project: {match['name']} ({match['id']})")

            # ── /projects ───────────────────────────────────────────
            async def projects_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
                if not _is_allowed(update):
                    return
                from api.projects import _load_projects
                existing = list(_load_projects().values())
                current = adapter.resolve_project(update.effective_chat.id)
                if not existing:
                    await update.message.reply_text("No projects configured.")
                    return
                lines = []
                for p in existing:
                    marker = " ← active" if p["id"] == current else ""
                    lines.append(f"• {p['name']} ({p['id']}){marker}")
                await update.message.reply_text("Projects:\n" + "\n".join(lines))

            # ── /status ─────────────────────────────────────────────
            async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
                if not _is_allowed(update):
                    return
                from tasks.scheduler import list_jobs
                project = adapter.resolve_project(update.effective_chat.id)
                jobs = list_jobs()
                status = f"Pantheon Online\nProject: {project}\nScheduled tasks: {len(jobs)}"
                if jobs:
                    for j in jobs[:5]:
                        status += f"\n• {j['name']} (next: {j['next_run'] or 'N/A'})"
                await update.message.reply_text(status)

            # ── /files ──────────────────────────────────────────────
            async def files_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
                if not _is_allowed(update):
                    return
                project = adapter.resolve_project(update.effective_chat.id)
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

            # ── /task ───────────────────────────────────────────────
            async def task_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
                if not _is_allowed(update):
                    return
                description = " ".join(context.args) if context.args else ""
                if not description:
                    await update.message.reply_text("Usage: /task <description>")
                    return
                project = adapter.resolve_project(update.effective_chat.id)
                from tasks.scheduler import schedule_agent_task
                task_id = await schedule_agent_task(
                    name=description[:50],
                    description=description,
                    schedule="now",
                    project_id=project,
                )
                await update.message.reply_text(f"Task scheduled ({project})!\nID: {task_id}\n\n{description}")

            # ── /memory ─────────────────────────────────────────────
            async def memory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
                if not _is_allowed(update):
                    return
                query = " ".join(context.args) if context.args else ""
                if not query:
                    await update.message.reply_text("Usage: /memory <search query>")
                    return
                project = adapter.resolve_project(update.effective_chat.id)
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

            # ── /note ───────────────────────────────────────────────
            async def note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
                if not _is_allowed(update):
                    return
                from datetime import datetime
                from pathlib import Path
                cfg = get_settings()
                project = adapter.resolve_project(update.effective_chat.id)

                if project and project != "default":
                    notes_dir = cfg.projects_dir / project / "workspace" / "notes"
                else:
                    notes_dir = cfg.workspace_dir / "notes"
                notes_dir.mkdir(parents=True, exist_ok=True)

                msg = update.message
                ts = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
                text_body = (msg.caption or msg.text or "").strip()
                if text_body.startswith("/note"):
                    text_body = text_body[len("/note"):].strip()

                saved: list[str] = []

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

                if text_body or not saved:
                    note_name = f"note-{ts}.md"
                    note_path = notes_dir / note_name
                    lines_out = [
                        "---",
                        f"date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
                        "source: telegram",
                        f"chat_id: {update.effective_chat.id}",
                    ]
                    if saved:
                        lines_out.append(f"attachment: {saved[0]}")
                    lines_out.append("---")
                    lines_out.append("")
                    lines_out.append(text_body or "(no text — see attachment)")
                    note_path.write_text("\n".join(lines_out), encoding="utf-8")
                    saved.insert(0, note_name)

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

            # ── Pending skill suggestions ───────────────────────────
            _tg_pending_suggestions: dict[str, dict] = {}

            async def _run_agent_and_reply(update, message, project, skill_context=None, active_skill_name=None):
                from agent.core import AgentCore
                from memory.manager import create_memory_manager
                from models.provider import get_provider

                chat_id = update.effective_chat.id
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
                    skill_context=skill_context,
                    active_skill_name=active_skill_name,
                )
                response = await agent.run_autonomous(message)
                text = response or "No response."
                for i in range(0, len(text), 4000):
                    await update.effective_chat.send_message(text[i:i + 4000])

            # ── Plain message handler ───────────────────────────────
            async def plain_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
                if not _is_allowed(update):
                    return
                message = update.message.text
                if not message or not message.strip():
                    return

                chat_id = update.effective_chat.id
                project = adapter.resolve_project(chat_id)

                try:
                    from skills.resolver import resolve_explicit, resolve_auto, build_skill_context
                    from skills.registry import get_skill_registry
                    from skills.models import SkillDiscoveryMode

                    explicit_skill, remaining = resolve_explicit(message)
                    if explicit_skill:
                        registry = get_skill_registry()
                        skill = registry.get(explicit_skill)
                        if skill:
                            try:
                                from skills import analytics as _sa
                                _sa.record_fire(explicit_skill, source="explicit")
                            except Exception:
                                pass
                            await update.message.reply_text(f"⚡ Using skill: /{explicit_skill}")
                            await _run_agent_and_reply(
                                update, remaining or message, project,
                                skill_context=build_skill_context(skill, project_id=project),
                                active_skill_name=explicit_skill,
                            )
                            return

                    try:
                        from secrets.vault import get_vault as _gv
                        _vault = _gv()
                        discovery_mode = _vault.get_secret(f"skill_discovery_{project}") or "off"
                    except Exception:
                        discovery_mode = "off"

                    if discovery_mode in ("suggest", "auto"):
                        matches = resolve_auto(
                            message, project_id=project,
                            mode=SkillDiscoveryMode(discovery_mode), top_k=1,
                        )
                        if matches and matches[0]["score"] >= 2.0:
                            best = matches[0]
                            skill = best["skill"]
                            if discovery_mode == "auto":
                                try:
                                    from skills import analytics as _sa
                                    _sa.record_fire(skill.name, source="auto")
                                except Exception:
                                    pass
                                await update.message.reply_text(f"⚡ Auto-activating skill: /{skill.name}")
                                await _run_agent_and_reply(
                                    update, message, project,
                                    skill_context=build_skill_context(skill, project_id=project),
                                    active_skill_name=skill.name,
                                )
                                return
                            else:
                                import uuid as _uuid
                                suggestion_id = str(_uuid.uuid4())[:8]
                                _tg_pending_suggestions[suggestion_id] = {
                                    "message": message,
                                    "project": project,
                                    "skill": skill.name,
                                }
                                try:
                                    from skills import analytics as _sa
                                    _sa.record_suggestion(skill.name)
                                except Exception:
                                    pass
                                keyboard = InlineKeyboardMarkup([
                                    [
                                        InlineKeyboardButton("✅ Use skill", callback_data=f"skill_yes:{suggestion_id}"),
                                        InlineKeyboardButton("❌ Skip", callback_data=f"skill_no:{suggestion_id}"),
                                    ]
                                ])
                                await update.message.reply_text(
                                    f"🪄 Skill suggested: **/{skill.name}**\n{skill.manifest.description}",
                                    reply_markup=keyboard,
                                    parse_mode="Markdown",
                                )
                                return
                except ImportError:
                    pass
                except Exception as e:
                    logger.warning("Telegram skill resolution failed: %s", e)

                await update.message.reply_text("Thinking...")
                try:
                    await _run_agent_and_reply(update, message, project)
                except Exception as e:
                    await update.message.reply_text(f"Error: {e}")

            # ── Skill callback handler ──────────────────────────────
            async def skill_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
                query = update.callback_query
                await query.answer()
                data = query.data or ""
                if not data.startswith("skill_"):
                    return

                action, _, suggestion_id = data.partition(":")
                pending = _tg_pending_suggestions.pop(suggestion_id, None)
                if not pending:
                    await query.edit_message_text("Suggestion expired.")
                    return

                message = pending["message"]
                project = pending["project"]
                skill_name = pending["skill"]

                if action == "skill_yes":
                    try:
                        from skills.resolver import build_skill_context
                        from skills.registry import get_skill_registry
                        from skills import analytics as _sa

                        registry = get_skill_registry()
                        skill = registry.get(skill_name)
                        _sa.record_suggestion(skill_name, accepted=True)
                        await query.edit_message_text(f"⚡ Using skill: /{skill_name}")
                        await _run_agent_and_reply(
                            update, message, project,
                            skill_context=build_skill_context(skill, project_id=project) if skill else None,
                            active_skill_name=skill_name,
                        )
                    except Exception as e:
                        await query.edit_message_text(f"Error activating skill: {e}")
                else:
                    try:
                        from skills import analytics as _sa
                        _sa.record_suggestion(skill_name, declined=True)
                    except Exception:
                        pass
                    await query.edit_message_text("Skipped skill. Thinking...")
                    try:
                        await _run_agent_and_reply(update, message, project)
                    except Exception as e:
                        await update.effective_chat.send_message(f"Error: {e}")

            # ── Register handlers ───────────────────────────────────
            app.add_handler(CommandHandler("start", start_cmd))
            app.add_handler(CommandHandler("project", project_cmd))
            app.add_handler(CommandHandler("projects", projects_cmd))
            app.add_handler(CommandHandler("chat", plain_message))
            app.add_handler(CommandHandler("status", status_cmd))
            app.add_handler(CommandHandler("files", files_cmd))
            app.add_handler(CommandHandler("task", task_cmd))
            app.add_handler(CommandHandler("memory", memory_cmd))
            app.add_handler(CommandHandler("note", note_cmd))
            app.add_handler(CallbackQueryHandler(skill_callback, pattern=r"^skill_(yes|no):"))
            app.add_handler(MessageHandler(
                (filters.PHOTO | filters.Document.ALL | filters.VOICE | filters.AUDIO | filters.VIDEO)
                & filters.CaptionRegex(r"^/note"),
                note_cmd,
            ))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, plain_message))

            _application = app
            await app.initialize()
            await app.start()
            asyncio.create_task(app.updater.start_polling(drop_pending_updates=True))
            logger.info("Telegram bot started successfully")

        except ImportError as exc:
            logger.warning("Telegram import failed: %s", exc)
            if raise_on_error:
                raise RuntimeError(
                    f"Telegram import error: {exc}. Try: pip install 'python-telegram-bot[all]==21.2'"
                )
        except Exception as e:
            logger.error("Failed to start Telegram bot: %s", e)
            if raise_on_error:
                raise

    async def stop(self) -> None:
        global _application
        if _application is None:
            return
        try:
            await _application.updater.stop()
            await _application.stop()
            await _application.shutdown()
            logger.info("Telegram bot stopped")
        except Exception as e:
            logger.warning("Error stopping Telegram bot: %s", e)
        _application = None

    async def list_channels(self) -> list[ChannelInfo]:
        """Return known chat IDs as channels.

        Telegram doesn't have a "list chats" API for bots, so we return
        the chats that have mappings plus the allowed-IDs list.
        """
        from messaging.channel_store import get_channel_store

        store = get_channel_store()
        known: set[str] = set()
        result: list[ChannelInfo] = []

        # Channels from existing mappings
        for m in store.get_mappings():
            if m.platform == "telegram":
                raw = m.channel_id.split(":", 1)[-1]
                if raw not in known:
                    known.add(raw)
                    result.append(ChannelInfo(
                        channel_id=m.channel_id,
                        raw_id=raw,
                        name=m.channel_name or f"Chat {raw}",
                        platform="telegram",
                    ))

        # Add allowed IDs that aren't already listed
        for aid in self._get_allowed_ids():
            raw = str(aid)
            if raw not in known:
                known.add(raw)
                result.append(ChannelInfo(
                    channel_id=f"telegram:{raw}",
                    raw_id=raw,
                    name=f"Chat {raw}",
                    platform="telegram",
                ))

        return result

    async def send_message(self, channel_id: str, text: str) -> None:
        global _application
        if _application is None:
            logger.debug("Telegram not running, cannot send message")
            return
        try:
            await _application.bot.send_message(
                chat_id=int(channel_id),
                text=text[:4096],
            )
        except Exception as e:
            logger.error("Failed to send Telegram message to %s: %s", channel_id, e)


# ── Module-level convenience functions (backward compat) ─────────────────────


async def start_telegram_bot(*, raise_on_error: bool = False) -> None:
    """Convenience wrapper used by the backward-compat shim."""
    adapter = TelegramAdapter()
    await adapter.start(raise_on_error=raise_on_error)


async def stop_telegram_bot() -> None:
    """Convenience wrapper used by the backward-compat shim."""
    adapter = TelegramAdapter()
    await adapter.stop()


async def restart_telegram_bot() -> dict[str, str]:
    """Convenience wrapper used by the backward-compat shim."""
    from messaging.gateway import get_messaging_gateway
    gw = get_messaging_gateway()
    return await gw.restart_adapter("telegram")


async def send_message_to_all(message: str) -> None:
    """Send a message to all allowed Telegram chat IDs."""
    global _application
    allowed_ids = TelegramAdapter._get_allowed_ids()
    if not allowed_ids or _application is None:
        return
    try:
        for chat_id in allowed_ids:
            await _application.bot.send_message(chat_id=chat_id, text=message[:4096])
    except Exception as e:
        logger.error("Failed to send Telegram message: %s", e)

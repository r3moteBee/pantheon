"""Slack messaging adapter for Pantheon.

Uses ``slack_sdk`` with Socket Mode (WebSockets) for real-time interaction.
Features command parsing and plain text agent chat.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from config import get_settings
from messaging.base import BaseMessagingAdapter
from messaging.models import ChannelInfo

logger = logging.getLogger(__name__)
settings = get_settings()

_socket_client: Any = None
_loop_task: asyncio.Task | None = None


class SlackAdapter(BaseMessagingAdapter):
    """Slack adapter using Socket Mode."""

    name = "slack"
    display_name = "Slack"

    # ------------------------------------------------------------------
    # Credential helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_bot_token() -> str:
        try:
            from secrets.vault import get_vault
            vault = get_vault()
            token = vault.get_secret("slack_bot_token")
            if token:
                return token
        except Exception:
            pass
        return settings.slack_bot_token or ""

    @staticmethod
    def _get_app_token() -> str:
        try:
            from secrets.vault import get_vault
            vault = get_vault()
            token = vault.get_secret("slack_app_token")
            if token:
                return token
        except Exception:
            pass
        return settings.slack_app_token or ""

    @staticmethod
    def _get_allowed_channels() -> list[str]:
        try:
            from secrets.vault import get_vault
            vault = get_vault()
            raw = vault.get_secret("slack_allowed_channel_ids")
            if raw:
                return [x.strip() for x in raw.split(",") if x.strip()]
        except Exception:
            pass
        raw_env = getattr(settings, "slack_allowed_channel_ids", "")
        if raw_env:
            return [x.strip() for x in raw_env.split(",") if x.strip()]
        return []

    # ------------------------------------------------------------------
    # BaseMessagingAdapter interface
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        return bool(self._get_bot_token() and self._get_app_token())

    async def is_running(self) -> bool:
        global _socket_client
        return _socket_client is not None and _socket_client.is_connected()

    async def start(self, *, raise_on_error: bool = False) -> None:
        global _socket_client, _loop_task
        bot_token = self._get_bot_token()
        app_token = self._get_app_token()

        if not bot_token or not app_token:
            logger.info("Slack credentials not fully configured, skipping.")
            if raise_on_error:
                raise RuntimeError("Slack credentials not configured (requires both bot and app tokens)")
            return

        try:
            from slack_sdk.web.async_client import AsyncWebClient
            from slack_sdk.socket_mode.aiohttp import SocketModeClient
            from slack_sdk.socket_mode.request import SocketModeRequest
            from slack_sdk.socket_mode.response import SocketModeResponse
        except ImportError as exc:
            logger.warning("slack_sdk not installed: %s", exc)
            if raise_on_error:
                raise RuntimeError("slack_sdk import error. Try: pip install slack_sdk aiohttp")
            return

        web_client = AsyncWebClient(token=bot_token)
        _socket_client = SocketModeClient(
            app_token=app_token,
            web_client=web_client,
        )

        adapter = self
        allowed_channels = self._get_allowed_channels()

        def _is_allowed(channel_id: str) -> bool:
            if not allowed_channels:
                return True
            return channel_id in allowed_channels

        async def _run_agent(message_text: str, project: str, session_id: str, skill_context=None, active_skill_name=None) -> str:
            from agent.core import AgentCore
            from memory.manager import create_memory_manager
            from models.provider import get_provider

            provider = get_provider()
            memory = create_memory_manager(
                project_id=project,
                session_id=session_id,
                provider=provider,
            )
            agent = AgentCore(
                provider=provider,
                memory_manager=memory,
                project_id=project,
                session_id=session_id,
                skill_context=skill_context,
                active_skill_name=active_skill_name,
            )
            return await agent.run_autonomous(message_text) or "No response."

        async def handle_message(client: SocketModeClient, req: SocketModeRequest) -> None:
            # Acknowledge the request
            response = SocketModeResponse(envelope_id=req.envelope_id)
            await client.send_socket_mode_response(response)

            if req.type != "events_api":
                return

            event = req.payload.get("event", {})
            event_type = event.get("type")

            # Only handle message event and app_mention
            if event_type not in ("message", "app_mention"):
                return
            # Ignore messages sent by bot itself
            if event.get("bot_id") or event.get("user") == req.payload.get("authorizations", [{}])[0].get("user_id"):
                return

            channel_id = event.get("channel", "")
            if not _is_allowed(channel_id):
                return

            text = event.get("text", "").strip()
            user_id = event.get("user", "user")

            # Clean mention prefix
            bot_user_id = req.payload.get("authorizations", [{}])[0].get("user_id", "")
            mention_pattern = re.compile(rf"<@{bot_user_id}>\s*")
            clean_text = mention_pattern.sub("", text).strip()

            # Handle direct message or mention
            is_dm = event.get("channel_type") == "im"
            is_mention = event_type == "app_mention" or f"<@{bot_user_id}>" in text

            if not (is_dm or is_mention):
                return

            project = adapter.resolve_project(channel_id)
            session_id = f"slack:{channel_id}"

            # Command dispatcher
            if clean_text.startswith("/"):
                parts = clean_text.split(maxsplit=1)
                cmd = parts[0].lower()
                arg = parts[1].strip() if len(parts) > 1 else ""

                if cmd == "/project":
                    if not arg:
                        await client.web_client.chat_postMessage(
                            channel=channel_id,
                            text=f"Active project: `{project}`\nUsage: `/project <name_or_id>`"
                        )
                        return
                    from api.projects import _load_projects
                    existing = list(_load_projects().values())
                    match = next((p for p in existing if p["id"] == arg or p["name"].lower() == arg.lower()), None)
                    if not match:
                        names = ", ".join(p["id"] for p in existing)
                        await client.web_client.chat_postMessage(
                            channel=channel_id,
                            text=f"Project '{arg}' not found. Available: {names}"
                        )
                        return
                    adapter.set_channel_project(channel_id, match["id"])
                    await client.web_client.chat_postMessage(
                        channel=channel_id,
                        text=f"Switched this channel to project: *{match['name']}* ({match['id']})"
                    )
                    return

                elif cmd == "/projects":
                    from api.projects import _load_projects
                    existing = list(_load_projects().values())
                    lines = [f"• {p['name']} ({p['id']})" + (" ← active" if p["id"] == project else "") for p in existing]
                    await client.web_client.chat_postMessage(
                        channel=channel_id,
                        text="*Projects:*\n" + "\n".join(lines)
                    )
                    return

                elif cmd == "/status":
                    from tasks.scheduler import list_jobs
                    jobs = list_jobs()
                    status_text = f"Pantheon Online\nProject: `{project}`\nScheduled tasks: {len(jobs)}"
                    await client.web_client.chat_postMessage(channel=channel_id, text=status_text)
                    return

                elif cmd == "/files":
                    cfg = get_settings()
                    workspace = cfg.projects_dir / project / "workspace" if project != "default" else cfg.workspace_dir
                    workspace.mkdir(parents=True, exist_ok=True)
                    files = list(workspace.glob("**/*"))[:20]
                    if not files:
                        await client.web_client.chat_postMessage(channel=channel_id, text=f"No files in workspace (`{project}`).")
                        return
                    file_list = "\n".join(f"• {f.relative_to(workspace)}" for f in files if f.is_file())
                    await client.web_client.chat_postMessage(channel=channel_id, text=f"Workspace files (`{project}`):\n{file_list}")
                    return

                elif cmd == "/task":
                    if not arg:
                        await client.web_client.chat_postMessage(channel=channel_id, text="Usage: `/task <description>`")
                        return
                    from tasks.scheduler import schedule_agent_task
                    task_id = await schedule_agent_task(name=arg[:50], description=arg, schedule="now", project_id=project)
                    await client.web_client.chat_postMessage(
                        channel=channel_id,
                        text=f"Task scheduled!\nID: `{task_id}`\nDescription: {arg}"
                    )
                    return

                elif cmd == "/memory":
                    if not arg:
                        await client.web_client.chat_postMessage(channel=channel_id, text="Usage: `/memory <query>`")
                        return
                    from memory.manager import create_memory_manager
                    manager = create_memory_manager(project_id=project)
                    results = await manager.recall(arg, tiers=["semantic", "episodic"])
                    if not results:
                        await client.web_client.chat_postMessage(channel=channel_id, text="No memories found.")
                        return
                    lines = [f"[{r.tier}] {r.content} (score: {r.score:.2f})" for r in results[:5]]
                    await client.web_client.chat_postMessage(channel=channel_id, text="*Memories:*\n" + "\n".join(lines))
                    return

                elif cmd == "/note":
                    if not arg:
                        await client.web_client.chat_postMessage(channel=channel_id, text="Usage: `/note <text>`")
                        return
                    from memory.manager import create_memory_manager
                    manager = create_memory_manager(project_id=project)
                    await manager.memorize(arg, tier="semantic", tags=["note", "slack"])
                    await client.web_client.chat_postMessage(channel=channel_id, text="Note saved successfully to semantic memory.")
                    return

            # Plain text chat
            try:
                # Run the agent in a background task
                async def _process():
                    try:
                        resolved_ctx = None
                        resolved_skill = None
                        text_to_process = clean_text

                        try:
                            from skills.resolver import resolve_explicit, resolve_auto, build_skill_context
                            from skills.registry import get_skill_registry
                            from skills.models import SkillDiscoveryMode

                            explicit_skill, remaining = resolve_explicit(clean_text)
                            if explicit_skill:
                                registry = get_skill_registry()
                                skill = registry.get(explicit_skill)
                                if skill:
                                    try:
                                        from skills import analytics as _sa
                                        _sa.record_fire(explicit_skill, source="explicit")
                                    except Exception:
                                        pass
                                    resolved_ctx = build_skill_context(skill, project_id=project)
                                    resolved_skill = explicit_skill
                                    text_to_process = remaining or clean_text
                            else:
                                try:
                                    from secrets.vault import get_vault as _gv
                                    _vault = _gv()
                                    discovery_mode = _vault.get_secret(f"skill_discovery_{project}") or "off"
                                except Exception:
                                    discovery_mode = "off"

                                if discovery_mode == "auto":
                                    matches = resolve_auto(
                                        clean_text, project_id=project,
                                        mode=SkillDiscoveryMode("auto"), top_k=1,
                                    )
                                    if matches and matches[0]["score"] >= 2.0:
                                        best = matches[0]
                                        skill = best["skill"]
                                        try:
                                            from skills import analytics as _sa
                                            _sa.record_fire(skill.name, source="auto")
                                        except Exception:
                                            pass
                                        resolved_ctx = build_skill_context(skill, project_id=project)
                                        resolved_skill = skill.name
                                        await client.web_client.chat_postMessage(channel=channel_id, text=f"⚡ Auto-activating skill: /{skill.name}")
                        except ImportError:
                            pass
                        except Exception as e:
                            logger.warning("Slack skill resolution failed: %s", e)

                        reply = await _run_agent(text_to_process, project, session_id, skill_context=resolved_ctx, active_skill_name=resolved_skill)
                        await client.web_client.chat_postMessage(channel=channel_id, text=reply)
                    except Exception as e:
                        logger.error("Error running agent from Slack: %s", e)
                        await client.web_client.chat_postMessage(channel=channel_id, text=f"Error running agent: {str(e)}")
                asyncio.create_task(_process())
            except Exception as e:
                logger.error("Error scheduling agent from Slack: %s", e)

        _socket_client.socket_mode_request_listeners.append(handle_message)
        await _socket_client.connect()

    async def stop(self) -> None:
        global _socket_client
        if _socket_client is not None:
            await _socket_client.close()
            _socket_client = None

    async def list_channels(self) -> list[ChannelInfo]:
        bot_token = self._get_bot_token()
        if not bot_token:
            return []
        try:
            from slack_sdk.web.async_client import AsyncWebClient
            client = AsyncWebClient(token=bot_token)
            response = await client.conversations_list(types="public_channel,private_channel,im")
            channels = response.get("channels", [])
            result = []
            for c in channels:
                raw_id = c.get("id", "")
                name = c.get("name", raw_id)
                if c.get("is_im"):
                    name = "Direct Message"
                result.append(ChannelInfo(
                    id=self.prefixed_channel_id(raw_id),
                    name=name,
                    platform=self.name,
                    project_id=self.resolve_project(raw_id),
                ))
            return result
        except Exception as e:
            logger.error("Failed to list Slack channels: %s", e)
            return []

    async def send_message(self, channel_id: str, text: str) -> None:
        bot_token = self._get_bot_token()
        if not bot_token:
            return
        try:
            from slack_sdk.web.async_client import AsyncWebClient
            client = AsyncWebClient(token=bot_token)
            await client.chat_postMessage(channel=channel_id, text=text)
        except Exception as e:
            logger.error("Failed to send message to Slack channel %s: %s", channel_id, e)

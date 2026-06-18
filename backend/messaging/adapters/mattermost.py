"""Mattermost messaging adapter for Pantheon.

Uses ``mattermostdriver`` with WebSockets for real-time interaction.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from config import get_settings
from messaging.base import BaseMessagingAdapter
from messaging.models import ChannelInfo

logger = logging.getLogger(__name__)
settings = get_settings()

_driver: Any = None
_websocket_task: asyncio.Task | None = None


class MattermostAdapter(BaseMessagingAdapter):
    """Mattermost adapter using mattermostdriver."""

    name = "mattermost"
    display_name = "Mattermost"

    # ------------------------------------------------------------------
    # Credential helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_url() -> str:
        try:
            from secrets.vault import get_vault
            vault = get_vault()
            url = vault.get_secret("mattermost_url")
            if url:
                return url
        except Exception:
            pass
        return settings.mattermost_url or ""

    @staticmethod
    def _get_token() -> str:
        try:
            from secrets.vault import get_vault
            vault = get_vault()
            token = vault.get_secret("mattermost_bot_token")
            if token:
                return token
        except Exception:
            pass
        return settings.mattermost_bot_token or ""

    @staticmethod
    def _get_scheme() -> str:
        try:
            from secrets.vault import get_vault
            vault = get_vault()
            scheme = vault.get_secret("mattermost_scheme")
            if scheme in ("http", "https"):
                return scheme
        except Exception:
            pass
        return settings.mattermost_scheme or "https"

    @staticmethod
    def _get_port() -> int:
        try:
            from secrets.vault import get_vault
            vault = get_vault()
            port = vault.get_secret("mattermost_port")
            if port:
                return int(port)
        except Exception:
            pass
        return settings.mattermost_port or 443

    # ------------------------------------------------------------------
    # BaseMessagingAdapter interface
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        return bool(self._get_url() and self._get_token())

    async def is_running(self) -> bool:
        global _driver, _websocket_task
        return _driver is not None and _websocket_task is not None and not _websocket_task.done()

    async def start(self, *, raise_on_error: bool = False) -> None:
        global _driver, _websocket_task
        url = self._get_url()
        token = self._get_token()
        scheme = self._get_scheme()
        port = self._get_port()

        if not url or not token:
            logger.info("Mattermost credentials not configured, skipping.")
            if raise_on_error:
                raise RuntimeError("Mattermost credentials not configured")
            return

        try:
            from mattermostdriver import Driver
        except ImportError as exc:
            logger.warning("mattermostdriver not installed: %s", exc)
            if raise_on_error:
                raise RuntimeError("mattermostdriver import error. Try: pip install mattermostdriver")
            return

        _driver = Driver({
            'url': url,
            'token': token,
            'scheme': scheme,
            'port': port,
            'debug': False,
        })

        adapter = self

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

        # WebSocket event handler
        async def event_handler(event_data: str) -> None:
            try:
                data = json.loads(event_data)
            except Exception:
                return

            event = data.get("event")
            if event != "posted":
                return

            post_data = data.get("data", {}).get("post")
            if not post_data:
                return

            try:
                post = json.loads(post_data)
            except Exception:
                return

            # Ignore posts from ourselves
            # Mattermost bot accounts have a bot description or user info
            # We can compare user_id if we fetch it at startup
            # For simplicity, we compare via bot tag or username if we find it
            # We will ignore post if it has props indicating bot_id
            if post.get("userId") == data.get("broadcast", {}).get("userId") or post.get("props", {}).get("from_bot"):
                return

            channel_id = post.get("channel_id")
            text = post.get("message", "").strip()
            project = adapter.resolve_project(channel_id)
            session_id = f"mattermost:{channel_id}"

            # Only respond to direct messages, mentions, or commands
            # Mattermost posts have channel_id, and we can inspect mentions or text starting with '/' or '!'
            # In Mattermost, bots respond to mentions or starting with a prefix
            is_command = text.startswith("!") or text.startswith("/")
            # We assume it's a mention or command for Pantheon
            if not (is_command or "pantheon" in text.lower()):
                return

            # Clean trigger prefix
            clean_text = text
            if clean_text.lower().startswith("pantheon"):
                clean_text = clean_text[len("pantheon"):].strip()

            if clean_text.startswith("!") or clean_text.startswith("/"):
                parts = clean_text.split(maxsplit=1)
                cmd = parts[0].lower()
                arg = parts[1].strip() if len(parts) > 1 else ""

                if cmd in ("!project", "/project"):
                    if not arg:
                        await adapter.send_message(channel_id, f"Active project: {project}\nUsage: !project <name_or_id>")
                        return
                    from api.projects import _load_projects
                    existing = list(_load_projects().values())
                    match = next((p for p in existing if p["id"] == arg or p["name"].lower() == arg.lower()), None)
                    if not match:
                        names = ", ".join(p["id"] for p in existing)
                        await adapter.send_message(channel_id, f"Project '{arg}' not found. Available: {names}")
                        return
                    adapter.set_channel_project(channel_id, match["id"])
                    await adapter.send_message(channel_id, f"Switched this channel to project: {match['name']} ({match['id']})")
                    return

                elif cmd in ("!projects", "/projects"):
                    from api.projects import _load_projects
                    existing = list(_load_projects().values())
                    lines = [f"• {p['name']} ({p['id']})" + (" ← active" if p["id"] == project else "") for p in existing]
                    await adapter.send_message(channel_id, "Projects:\n" + "\n".join(lines))
                    return

                elif cmd in ("!status", "/status"):
                    from tasks.scheduler import list_jobs
                    jobs = list_jobs()
                    status_text = f"Pantheon Online\nProject: {project}\nScheduled tasks: {len(jobs)}"
                    await adapter.send_message(channel_id, status_text)
                    return

                elif cmd in ("!files", "/files"):
                    cfg = get_settings()
                    workspace = cfg.projects_dir / project / "workspace" if project != "default" else cfg.workspace_dir
                    workspace.mkdir(parents=True, exist_ok=True)
                    files = list(workspace.glob("**/*"))[:20]
                    if not files:
                        await adapter.send_message(channel_id, f"No files in workspace ({project}).")
                        return
                    file_list = "\n".join(f"• {f.relative_to(workspace)}" for f in files if f.is_file())
                    await adapter.send_message(channel_id, f"Workspace files ({project}):\n{file_list}")
                    return

                elif cmd in ("!task", "/task"):
                    if not arg:
                        await adapter.send_message(channel_id, "Usage: !task <description>")
                        return
                    from tasks.scheduler import schedule_agent_task
                    task_id = await schedule_agent_task(name=arg[:50], description=arg, schedule="now", project_id=project)
                    await adapter.send_message(channel_id, f"Task scheduled!\nID: {task_id}\nDescription: {arg}")
                    return

                elif cmd in ("!memory", "/memory"):
                    if not arg:
                        await adapter.send_message(channel_id, "Usage: !memory <query>")
                        return
                    from memory.manager import create_memory_manager
                    manager = create_memory_manager(project_id=project)
                    results = await manager.recall(arg, tiers=["semantic", "episodic"])
                    if not results:
                        await adapter.send_message(channel_id, "No memories found.")
                        return
                    lines = [f"[{r.tier}] {r.content} (score: {r.score:.2f})" for r in results[:5]]
                    await adapter.send_message(channel_id, "Memories:\n" + "\n".join(lines))
                    return

                elif cmd in ("!note", "/note"):
                    if not arg:
                        await adapter.send_message(channel_id, "Usage: !note <text>")
                        return
                    from memory.manager import create_memory_manager
                    manager = create_memory_manager(project_id=project)
                    await manager.memorize(arg, tier="semantic", tags=["note", "mattermost"])
                    await adapter.send_message(channel_id, "Note saved successfully to semantic memory.")
                    return

            try:
                # Run the agent in a background task so we don't hold up the handler
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
                                        await adapter.send_message(channel_id, f"⚡ Auto-activating skill: /{skill.name}")
                        except ImportError:
                            pass
                        except Exception as e:
                            logger.warning("Mattermost skill resolution failed: %s", e)

                        reply = await _run_agent(text_to_process, project, session_id, skill_context=resolved_ctx, active_skill_name=resolved_skill)
                        await adapter.send_message(channel_id, reply)
                    except Exception as e:
                        logger.error("Error running agent from Mattermost: %s", e)
                        await adapter.send_message(channel_id, f"Error running agent: {str(e)}")
                asyncio.create_task(_process())
            except Exception as e:
                logger.error("Error scheduling agent from Mattermost: %s", e)

        async def ws_loop():
            try:
                _driver.login()
                # mattermostdriver has a blocking websocket loop, we run it in an executor
                # Use a fire-and-forget task for the callback
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, _driver.init_websocket, lambda e: asyncio.run_coroutine_threadsafe(event_handler(e), loop))
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.exception("Mattermost WebSocket client crash: %s", e)

        _websocket_task = asyncio.create_task(ws_loop())

    async def stop(self) -> None:
        global _driver, _websocket_task
        if _websocket_task is not None:
            _websocket_task.cancel()
            try:
                await _websocket_task
            except asyncio.CancelledError:
                pass
            _websocket_task = None
        if _driver is not None:
            try:
                _driver.disconnect()
            except Exception:
                pass
            _driver = None

    async def list_channels(self) -> list[ChannelInfo]:
        global _driver
        if _driver is None:
            return []
        try:
            def _fetch():
                # Fetch teams and then channels for teams
                teams = _driver.teams.get_teams_for_user('me')
                result = []
                for t in teams:
                    channels = _driver.channels.get_channels_for_user('me', t['id'])
                    for c in channels:
                        raw_id = c['id']
                        name = c['display_name'] or c['name']
                        result.append(ChannelInfo(
                            id=self.prefixed_channel_id(raw_id),
                            name=name,
                            platform=self.name,
                            project_id=self.resolve_project(raw_id),
                        ))
                return result
            return await asyncio.to_thread(_fetch)
        except Exception as e:
            logger.error("Failed to list Mattermost channels: %s", e)
            return []

    async def send_message(self, channel_id: str, text: str) -> None:
        global _driver
        if _driver is None:
            return
        try:
            await asyncio.to_thread(_driver.posts.create_post, {'channel_id': channel_id, 'message': text})
        except Exception as e:
            logger.error("Failed to send Mattermost message to %s: %s", channel_id, e)

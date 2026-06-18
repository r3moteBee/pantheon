"""Matrix messaging adapter for Pantheon.

Uses ``matrix-nio`` async client to connect and sync messages in real-time.
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

_client: Any = None
_sync_task: asyncio.Task | None = None


class MatrixAdapter(BaseMessagingAdapter):
    """Matrix adapter using matrix-nio."""

    name = "matrix"
    display_name = "Matrix"

    # ------------------------------------------------------------------
    # Credential helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_homeserver() -> str:
        try:
            from secrets.vault import get_vault
            vault = get_vault()
            url = vault.get_secret("matrix_homeserver_url")
            if url:
                return url
        except Exception:
            pass
        return settings.matrix_homeserver_url or "https://matrix.org"

    @staticmethod
    def _get_user_id() -> str:
        try:
            from secrets.vault import get_vault
            vault = get_vault()
            uid = vault.get_secret("matrix_user_id")
            if uid:
                return uid
        except Exception:
            pass
        return settings.matrix_user_id or ""

    @staticmethod
    def _get_access_token() -> str:
        try:
            from secrets.vault import get_vault
            vault = get_vault()
            token = vault.get_secret("matrix_access_token")
            if token:
                return token
        except Exception:
            pass
        return settings.matrix_access_token or ""

    # ------------------------------------------------------------------
    # BaseMessagingAdapter interface
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        return bool(self._get_user_id() and self._get_access_token())

    async def is_running(self) -> bool:
        global _client, _sync_task
        return _client is not None and _sync_task is not None and not _sync_task.done()

    async def start(self, *, raise_on_error: bool = False) -> None:
        global _client, _sync_task
        homeserver = self._get_homeserver()
        user_id = self._get_user_id()
        access_token = self._get_access_token()

        if not user_id or not access_token:
            logger.info("Matrix credentials not configured, skipping.")
            if raise_on_error:
                raise RuntimeError("Matrix credentials not configured")
            return

        try:
            from nio import AsyncClient, MatrixRoom, RoomMessageText
        except ImportError as exc:
            logger.warning("matrix-nio not installed: %s", exc)
            if raise_on_error:
                raise RuntimeError("matrix-nio import error. Try: pip install matrix-nio")
            return

        client = AsyncClient(homeserver, user_id)
        client.access_token = access_token
        _client = client

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

        @client.event_callback(RoomMessageText)
        async def message_callback(room: MatrixRoom, event: RoomMessageText) -> None:
            # Ignore messages from ourselves
            if event.sender == client.user_id:
                return

            room_id = room.room_id
            text = event.body.strip()
            project = adapter.resolve_project(room_id)
            session_id = f"matrix:{room_id}"

            # Only respond to mentions (if room is not a direct chat) or commands
            is_direct = room.is_group == False or len(room.users) == 2
            is_mention = client.user_id in text or text.startswith("!")

            if not (is_direct or is_mention):
                return

            # Strip bot mention from text
            clean_text = text.replace(client.user_id, "").strip()

            if clean_text.startswith("!"):
                parts = clean_text.split(maxsplit=1)
                cmd = parts[0].lower()
                arg = parts[1].strip() if len(parts) > 1 else ""

                if cmd == "!project":
                    if not arg:
                        await client.room_send(
                            room_id=room_id,
                            message_type="m.room.message",
                            content={"msgtype": "m.text", "body": f"Active project: {project}\nUsage: !project <name_or_id>"}
                        )
                        return
                    from api.projects import _load_projects
                    existing = list(_load_projects().values())
                    match = next((p for p in existing if p["id"] == arg or p["name"].lower() == arg.lower()), None)
                    if not match:
                        names = ", ".join(p["id"] for p in existing)
                        await client.room_send(
                            room_id=room_id,
                            message_type="m.room.message",
                            content={"msgtype": "m.text", "body": f"Project '{arg}' not found. Available: {names}"}
                        )
                        return
                    adapter.set_channel_project(room_id, match["id"])
                    await client.room_send(
                        room_id=room_id,
                        message_type="m.room.message",
                        content={"msgtype": "m.text", "body": f"Switched this room to project: {match['name']} ({match['id']})"}
                    )
                    return

                elif cmd == "!projects":
                    from api.projects import _load_projects
                    existing = list(_load_projects().values())
                    lines = [f"• {p['name']} ({p['id']})" + (" ← active" if p["id"] == project else "") for p in existing]
                    await client.room_send(
                        room_id=room_id,
                        message_type="m.room.message",
                        content={"msgtype": "m.text", "body": "Projects:\n" + "\n".join(lines)}
                    )
                    return

                elif cmd == "!status":
                    from tasks.scheduler import list_jobs
                    jobs = list_jobs()
                    status_text = f"Pantheon Online\nProject: {project}\nScheduled tasks: {len(jobs)}"
                    await client.room_send(
                        room_id=room_id,
                        message_type="m.room.message",
                        content={"msgtype": "m.text", "body": status_text}
                    )
                    return

                elif cmd == "!files":
                    cfg = get_settings()
                    workspace = cfg.projects_dir / project / "workspace" if project != "default" else cfg.workspace_dir
                    workspace.mkdir(parents=True, exist_ok=True)
                    files = list(workspace.glob("**/*"))[:20]
                    if not files:
                        await client.room_send(
                            room_id=room_id,
                            message_type="m.room.message",
                            content={"msgtype": "m.text", "body": f"No files in workspace ({project})."}
                        )
                        return
                    file_list = "\n".join(f"• {f.relative_to(workspace)}" for f in files if f.is_file())
                    await client.room_send(
                        room_id=room_id,
                        message_type="m.room.message",
                        content={"msgtype": "m.text", "body": f"Workspace files ({project}):\n{file_list}"}
                    )
                    return

                elif cmd == "!task":
                    if not arg:
                        await client.room_send(
                            room_id=room_id,
                            message_type="m.room.message",
                            content={"msgtype": "m.text", "body": "Usage: !task <description>"}
                        )
                        return
                    from tasks.scheduler import schedule_agent_task
                    task_id = await schedule_agent_task(name=arg[:50], description=arg, schedule="now", project_id=project)
                    await client.room_send(
                        room_id=room_id,
                        message_type="m.room.message",
                        content={"msgtype": "m.text", "body": f"Task scheduled!\nID: {task_id}\nDescription: {arg}"}
                    )
                    return

                elif cmd == "!memory":
                    if not arg:
                        await client.room_send(
                            room_id=room_id,
                            message_type="m.room.message",
                            content={"msgtype": "m.text", "body": "Usage: !memory <query>"}
                        )
                        return
                    from memory.manager import create_memory_manager
                    manager = create_memory_manager(project_id=project)
                    results = await manager.recall(arg, tiers=["semantic", "episodic"])
                    if not results:
                        await client.room_send(
                            room_id=room_id,
                            message_type="m.room.message",
                            content={"msgtype": "m.text", "body": "No memories found."}
                        )
                        return
                    lines = [f"[{r.tier}] {r.content} (score: {r.score:.2f})" for r in results[:5]]
                    await client.room_send(
                        room_id=room_id,
                        message_type="m.room.message",
                        content={"msgtype": "m.text", "body": "Memories:\n" + "\n".join(lines)}
                    )
                    return

                elif cmd == "!note":
                    if not arg:
                        await client.room_send(
                            room_id=room_id,
                            message_type="m.room.message",
                            content={"msgtype": "m.text", "body": "Usage: !note <text>"}
                        )
                        return
                    from memory.manager import create_memory_manager
                    manager = create_memory_manager(project_id=project)
                    await manager.memorize(arg, tier="semantic", tags=["note", "matrix"])
                    await client.room_send(
                        room_id=room_id,
                        message_type="m.room.message",
                        content={"msgtype": "m.text", "body": "Note saved successfully to semantic memory."}
                    )
                    return

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
                                        await client.room_send(
                                            room_id=room_id,
                                            message_type="m.room.message",
                                            content={"msgtype": "m.text", "body": f"⚡ Auto-activating skill: /{skill.name}"}
                                        )
                        except ImportError:
                            pass
                        except Exception as e:
                            logger.warning("Matrix skill resolution failed: %s", e)

                        reply = await _run_agent(text_to_process, project, session_id, skill_context=resolved_ctx, active_skill_name=resolved_skill)
                        await client.room_send(
                            room_id=room_id,
                            message_type="m.room.message",
                            content={"msgtype": "m.text", "body": reply}
                        )
                    except Exception as e:
                        logger.error("Error running agent from Matrix: %s", e)
                        await client.room_send(
                            room_id=room_id,
                            message_type="m.room.message",
                            content={"msgtype": "m.text", "body": f"Error running agent: {str(e)}"}
                        )
                asyncio.create_task(_process())
            except Exception as e:
                logger.error("Error scheduling agent from Matrix: %s", e)

        # Start background sync task
        async def sync_loop():
            try:
                # Login if necessary, or check credentials
                await client.login(token=access_token)
                await client.sync_forever(timeout=30000)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.exception("Matrix sync loop crash: %s", e)

        _sync_task = asyncio.create_task(sync_loop())

    async def stop(self) -> None:
        global _client, _sync_task
        if _sync_task is not None:
            _sync_task.cancel()
            try:
                await _sync_task
            except asyncio.CancelledError:
                pass
            _sync_task = None
        if _client is not None:
            await _client.close()
            _client = None

    async def list_channels(self) -> list[ChannelInfo]:
        global _client
        if _client is None:
            return []
        try:
            result = []
            for room_id, room in _client.rooms.items():
                name = room.display_name or room.name or room_id
                result.append(ChannelInfo(
                    id=self.prefixed_channel_id(room_id),
                    name=name,
                    platform=self.name,
                    project_id=self.resolve_project(room_id),
                ))
            return result
        except Exception as e:
            logger.error("Failed to list Matrix channels: %s", e)
            return []

    async def send_message(self, channel_id: str, text: str) -> None:
        global _client
        if _client is None:
            return
        try:
            await _client.room_send(
                room_id=channel_id,
                message_type="m.room.message",
                content={"msgtype": "m.text", "body": text}
            )
        except Exception as e:
            logger.error("Failed to send Matrix message to %s: %s", channel_id, e)

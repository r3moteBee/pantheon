"""Discord messaging adapter for Pantheon.

Uses ``discord.py`` with slash commands.  Feature parity with the Telegram
adapter: /project, /projects, /status, /files, /task, /memory, /note,
plain-text agent chat, and skill resolution.
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
_tree: Any = None  # discord.app_commands.CommandTree


class DiscordAdapter(BaseMessagingAdapter):
    """Discord adapter using ``discord.py``."""

    name = "discord"
    display_name = "Discord"

    # ------------------------------------------------------------------
    # Credential helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_token() -> str:
        try:
            from secrets.vault import get_vault
            vault = get_vault()
            token = vault.get_secret("discord_bot_token")
            if token:
                return token
        except Exception:
            pass
        return settings.discord_bot_token or ""

    @staticmethod
    def _get_allowed_guild_ids() -> list[int]:
        try:
            from secrets.vault import get_vault
            vault = get_vault()
            raw = vault.get_secret("discord_allowed_guild_ids")
            if raw:
                return [int(x.strip()) for x in raw.split(",") if x.strip()]
        except Exception:
            pass
        raw_env = getattr(settings, "discord_allowed_guild_ids", "")
        if raw_env:
            return [int(x.strip()) for x in raw_env.split(",") if x.strip()]
        return []

    @staticmethod
    def _get_command_scope() -> str:
        """Return 'guild' or 'global'."""
        try:
            from secrets.vault import get_vault
            vault = get_vault()
            scope = vault.get_secret("discord_command_scope")
            if scope in ("guild", "global"):
                return scope
        except Exception:
            pass
        return "guild"

    # ------------------------------------------------------------------
    # BaseMessagingAdapter interface
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        return bool(self._get_token())

    async def is_running(self) -> bool:
        global _client
        return _client is not None and _client.is_ready()

    async def start(self, *, raise_on_error: bool = False) -> None:
        global _client, _tree
        token = self._get_token()
        if not token:
            logger.info("Discord bot token not configured, skipping.")
            if raise_on_error:
                raise RuntimeError("No Discord bot token configured")
            return

        try:
            import discord
            from discord import app_commands
        except ImportError as exc:
            logger.warning("discord.py not installed: %s", exc)
            if raise_on_error:
                raise RuntimeError(
                    f"discord.py import error: {exc}. Try: pip install 'discord.py>=2.3'"
                )
            return

        adapter = self
        allowed_guilds = self._get_allowed_guild_ids()
        command_scope = self._get_command_scope()

        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True

        client = discord.Client(intents=intents)
        tree = app_commands.CommandTree(client)

        # ── ACL helper ──────────────────────────────────────────────
        def _is_allowed_guild(guild_id: int | None) -> bool:
            if not allowed_guilds:
                return True
            return guild_id in allowed_guilds

        # ── Agent runner ────────────────────────────────────────────
        async def _run_agent(message_text: str, project: str, session_id: str,
                             skill_context=None, active_skill_name=None) -> str:
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

        # ── /project ────────────────────────────────────────────────
        @tree.command(name="project", description="Switch this channel to a Pantheon project")
        @app_commands.describe(name="Project name or ID")
        async def project_cmd(interaction: discord.Interaction, name: str) -> None:
            if not _is_allowed_guild(interaction.guild_id):
                await interaction.response.send_message("Not authorised.", ephemeral=True)
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
                await interaction.response.send_message(
                    f"Project '{name}' not found.\nAvailable: {names}", ephemeral=True
                )
                return
            adapter.set_channel_project(interaction.channel_id, match["id"])
            await interaction.response.send_message(
                f"Channel now mapped to project: **{match['name']}** (`{match['id']}`)"
            )

        # ── /projects ───────────────────────────────────────────────
        @tree.command(name="projects", description="List available Pantheon projects")
        async def projects_cmd(interaction: discord.Interaction) -> None:
            if not _is_allowed_guild(interaction.guild_id):
                await interaction.response.send_message("Not authorised.", ephemeral=True)
                return
            from api.projects import _load_projects
            existing = list(_load_projects().values())
            current = adapter.resolve_project(interaction.channel_id)
            if not existing:
                await interaction.response.send_message("No projects configured.", ephemeral=True)
                return
            lines = []
            for p in existing:
                marker = " ← this channel" if p["id"] == current else ""
                lines.append(f"• **{p['name']}** (`{p['id']}`){marker}")
            await interaction.response.send_message("**Projects:**\n" + "\n".join(lines))

        # ── /status ─────────────────────────────────────────────────
        @tree.command(name="status", description="Show Pantheon agent status")
        async def status_cmd(interaction: discord.Interaction) -> None:
            if not _is_allowed_guild(interaction.guild_id):
                await interaction.response.send_message("Not authorised.", ephemeral=True)
                return
            from tasks.scheduler import list_jobs
            project = adapter.resolve_project(interaction.channel_id)
            jobs = list_jobs()
            text = f"**Pantheon Online**\nProject: `{project}`\nScheduled tasks: {len(jobs)}"
            if jobs:
                for j in jobs[:5]:
                    text += f"\n• {j['name']} (next: {j['next_run'] or 'N/A'})"
            await interaction.response.send_message(text)

        # ── /files ──────────────────────────────────────────────────
        @tree.command(name="files", description="List workspace files for the channel's project")
        async def files_cmd(interaction: discord.Interaction) -> None:
            if not _is_allowed_guild(interaction.guild_id):
                await interaction.response.send_message("Not authorised.", ephemeral=True)
                return
            project = adapter.resolve_project(interaction.channel_id)
            cfg = get_settings()
            if project and project != "default":
                workspace = cfg.projects_dir / project / "workspace"
            else:
                workspace = cfg.workspace_dir
            workspace.mkdir(parents=True, exist_ok=True)
            files = [f for f in list(workspace.glob("**/*"))[:20] if f.is_file()]
            if not files:
                await interaction.response.send_message(f"No files in workspace (`{project}`).")
                return
            file_list = "\n".join(f"• `{f.relative_to(workspace)}`" for f in files)
            await interaction.response.send_message(f"**Workspace** (`{project}`):\n{file_list}")

        # ── /task ───────────────────────────────────────────────────
        @tree.command(name="task", description="Create an autonomous task")
        @app_commands.describe(description="Task description")
        async def task_cmd(interaction: discord.Interaction, description: str) -> None:
            if not _is_allowed_guild(interaction.guild_id):
                await interaction.response.send_message("Not authorised.", ephemeral=True)
                return
            project = adapter.resolve_project(interaction.channel_id)
            from tasks.scheduler import schedule_agent_task
            await interaction.response.defer()
            task_id = await schedule_agent_task(
                name=description[:50],
                description=description,
                schedule="now",
                project_id=project,
            )
            await interaction.followup.send(
                f"Task scheduled (`{project}`)!\nID: `{task_id}`\n\n{description}"
            )

        # ── /memory ─────────────────────────────────────────────────
        @tree.command(name="memory", description="Search project memories")
        @app_commands.describe(query="Search query")
        async def memory_cmd(interaction: discord.Interaction, query: str) -> None:
            if not _is_allowed_guild(interaction.guild_id):
                await interaction.response.send_message("Not authorised.", ephemeral=True)
                return
            project = adapter.resolve_project(interaction.channel_id)
            await interaction.response.defer()
            from memory.manager import create_memory_manager
            manager = create_memory_manager(project_id=project)
            results = await manager.recall(query, tiers=["semantic", "episodic"])
            if not results:
                await interaction.followup.send("No relevant memories found.")
                return
            lines = [f"**Memory search** (`{project}`): '{query}'\n"]
            for r in results[:5]:
                source = r.get("source", "?")
                content = r.get("content", "")[:200]
                lines.append(f"[{source}] {content}")
            await interaction.followup.send("\n\n".join(lines))

        # ── /note ───────────────────────────────────────────────────
        @tree.command(name="note", description="Save a note to the project")
        @app_commands.describe(text="Note text", attachment="Optional file attachment")
        async def note_cmd(
            interaction: discord.Interaction,
            text: str = "",
            attachment: discord.Attachment | None = None,
        ) -> None:
            if not _is_allowed_guild(interaction.guild_id):
                await interaction.response.send_message("Not authorised.", ephemeral=True)
                return
            from datetime import datetime
            from pathlib import Path

            await interaction.response.defer()

            project = adapter.resolve_project(interaction.channel_id)
            cfg = get_settings()
            if project and project != "default":
                notes_dir = cfg.projects_dir / project / "workspace" / "notes"
            else:
                notes_dir = cfg.workspace_dir / "notes"
            notes_dir.mkdir(parents=True, exist_ok=True)

            ts = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
            saved: list[str] = []

            if attachment is not None:
                ext = Path(attachment.filename).suffix or ".bin"
                attach_name = f"note-{ts}{ext}"
                attach_path = notes_dir / attach_name
                await attachment.save(attach_path)
                saved.append(attach_name)

            if text or not saved:
                note_name = f"note-{ts}.md"
                note_path = notes_dir / note_name
                lines_out = [
                    "---",
                    f"date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
                    "source: discord",
                    f"channel_id: {interaction.channel_id}",
                    f"user: {interaction.user.display_name}",
                ]
                if saved:
                    lines_out.append(f"attachment: {saved[0]}")
                lines_out.append("---")
                lines_out.append("")
                lines_out.append(text or "(no text — see attachment)")
                note_path.write_text("\n".join(lines_out), encoding="utf-8")
                saved.insert(0, note_name)

            try:
                from memory.manager import create_memory_manager
                mgr = create_memory_manager(project_id=project)
                await mgr.remember(
                    content=f"[discord note {ts}] {text or saved[0]}",
                    tier="semantic",
                    metadata={"source": "discord_note", "files": saved},
                )
            except Exception as e:
                logger.warning("Failed to index discord note into memory: %s", e)

            await interaction.followup.send(
                f"📝 Saved to `{project}/notes/`:\n" + "\n".join(f"• `{n}`" for n in saved)
            )

        # ── on_ready: sync slash commands ───────────────────────────
        @client.event
        async def on_ready() -> None:
            logger.info("Discord bot logged in as %s (ID: %s)", client.user, client.user.id)
            try:
                if command_scope == "guild" and allowed_guilds:
                    for gid in allowed_guilds:
                        guild = discord.Object(id=gid)
                        tree.copy_global_to(guild=guild)
                        await tree.sync(guild=guild)
                        logger.info("Synced slash commands to guild %s", gid)
                else:
                    await tree.sync()
                    logger.info("Synced slash commands globally")
            except Exception as exc:
                logger.error("Failed to sync Discord slash commands: %s", exc)

        # ── on_message: plain text → agent ──────────────────────────
        @client.event
        async def on_message(message: discord.Message) -> None:
            if message.author == client.user:
                return
            if message.author.bot:
                return
            if not _is_allowed_guild(message.guild.id if message.guild else None):
                return
            # Only respond if the bot is mentioned or in a mapped channel
            # Check if this channel has a mapping (or is using the default)
            project = adapter.resolve_project(message.channel.id)

            # Require bot mention or DM to trigger agent response
            is_dm = message.guild is None
            is_mentioned = client.user in message.mentions
            if not is_dm and not is_mentioned:
                return

            text = message.content
            # Strip the bot mention from the message
            if is_mentioned:
                text = text.replace(f"<@{client.user.id}>", "").strip()
            if not text:
                return

            session_id = f"discord-{message.channel.id}"

            # Skill resolution
            try:
                from skills.resolver import resolve_explicit, resolve_auto, build_skill_context
                from skills.registry import get_skill_registry
                from skills.models import SkillDiscoveryMode

                explicit_skill, remaining = resolve_explicit(text)
                if explicit_skill:
                    registry = get_skill_registry()
                    skill = registry.get(explicit_skill)
                    if skill:
                        try:
                            from skills import analytics as _sa
                            _sa.record_fire(explicit_skill, source="explicit")
                        except Exception:
                            pass
                        async with message.channel.typing():
                            response = await _run_agent(
                                remaining or text, project, session_id,
                                skill_context=build_skill_context(skill, project_id=project),
                                active_skill_name=explicit_skill,
                            )
                        for i in range(0, len(response), 2000):
                            await message.channel.send(response[i:i + 2000])
                        return

                try:
                    from secrets.vault import get_vault as _gv
                    _vault = _gv()
                    discovery_mode = _vault.get_secret(f"skill_discovery_{project}") or "off"
                except Exception:
                    discovery_mode = "off"

                if discovery_mode == "auto":
                    matches = resolve_auto(
                        text, project_id=project,
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
                        await message.channel.send(f"⚡ Auto-activating skill: /{skill.name}")
                        async with message.channel.typing():
                            response = await _run_agent(
                                text, project, session_id,
                                skill_context=build_skill_context(skill, project_id=project),
                                active_skill_name=skill.name,
                            )
                        for i in range(0, len(response), 2000):
                            await message.channel.send(response[i:i + 2000])
                        return
            except ImportError:
                pass
            except Exception as e:
                logger.warning("Discord skill resolution failed: %s", e)

            # Default agent response
            async with message.channel.typing():
                try:
                    response = await _run_agent(text, project, session_id)
                except Exception as e:
                    response = f"Error: {e}"
            for i in range(0, len(response), 2000):
                await message.channel.send(response[i:i + 2000])

        # ── Start the bot ───────────────────────────────────────────
        _client = client
        _tree = tree
        try:
            asyncio.create_task(client.start(token))
            # Wait briefly for the client to connect
            for _ in range(30):
                await asyncio.sleep(1)
                if client.is_ready():
                    break
            if not client.is_ready():
                logger.warning("Discord client did not become ready within 30s")
            else:
                logger.info("Discord bot started successfully")
        except Exception as e:
            _client = None
            _tree = None
            logger.error("Failed to start Discord bot: %s", e)
            if raise_on_error:
                raise

    async def stop(self) -> None:
        global _client, _tree
        if _client is None:
            return
        try:
            await _client.close()
            logger.info("Discord bot stopped")
        except Exception as e:
            logger.warning("Error stopping Discord bot: %s", e)
        _client = None
        _tree = None

    async def list_channels(self) -> list[ChannelInfo]:
        global _client
        if _client is None or not _client.is_ready():
            return []
        result: list[ChannelInfo] = []
        try:
            import discord as _discord
            for guild in _client.guilds:
                for channel in guild.channels:
                    if isinstance(channel, _discord.TextChannel):
                        result.append(ChannelInfo(
                            channel_id=f"discord:{channel.id}",
                            raw_id=str(channel.id),
                            name=f"#{channel.name} ({guild.name})",
                            platform="discord",
                        ))
        except Exception as exc:
            logger.warning("Failed to list Discord channels: %s", exc)
        return result

    async def send_message(self, channel_id: str, text: str) -> None:
        global _client
        if _client is None or not _client.is_ready():
            logger.debug("Discord not running, cannot send message")
            return
        try:
            channel = _client.get_channel(int(channel_id))
            if channel is None:
                channel = await _client.fetch_channel(int(channel_id))
            for i in range(0, len(text), 2000):
                await channel.send(text[i:i + 2000])
        except Exception as e:
            logger.error("Failed to send Discord message to %s: %s", channel_id, e)

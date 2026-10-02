# Messaging bots

Pantheon can answer on Telegram, Slack, Discord, Matrix and Mattermost. Each platform is an adapter under `backend/messaging/adapters/`. All five run inside the FastAPI process, with no extra worker.

## Layout

| Module | Role |
|---|---|
| `messaging/base.py` | `BaseMessagingAdapter`: `start/stop/is_running/is_configured/list_channels/send_message`, plus `resolve_project()` and `set_channel_project()`. |
| `messaging/gateway.py` | `MessagingGateway` singleton (`get_messaging_gateway()`). On startup it registers the five built-in adapters and starts each one whose `is_configured()` is true. It also handles `restart_adapter`, `status`, `list_all_channels` and `broadcast`. `main.py` calls `startup()` and `shutdown()` in the lifespan. |
| `messaging/channel_store.py` | Channel→project mappings, stored as JSON in vault key `messaging_channel_mappings`. The default project for unmapped channels lives in vault key `messaging_default_project` (fallback `default`). Channel ids are platform-prefixed, e.g. `slack:C0123`. |
| `messaging/models.py` | `ChannelMapping`, `ChannelInfo`, `AdapterStatus`. |

## Configuration

Every value is read from the **vault first** and falls back to `.env` (`backend/config.py`). `PUT /api/settings` (Settings → Channels) writes the vault. Saving settings does **not** restart a bot: call `POST /api/messaging/{name}/restart`, or restart the backend.

| Adapter | Library | Required to start | Allowlist key | Other keys |
|---|---|---|---|---|
| `telegram` | python-telegram-bot | `telegram_bot_token` / `TELEGRAM_BOT_TOKEN` | `telegram_allowed_chat_ids` (ints) | none |
| `slack` | slack_sdk, Socket Mode | `slack_bot_token` **and** `slack_app_token` (`SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`) | `slack_allowed_channel_ids` | none |
| `discord` | discord.py | `discord_bot_token` / `DISCORD_BOT_TOKEN` | `discord_allowed_guild_ids` (ints) | `discord_command_scope` = `guild` (default) or `global` (vault only) |
| `matrix` | matrix-nio | `matrix_user_id` **and** `matrix_access_token` | `matrix_allowed_room_ids` | `matrix_homeserver_url` (default `https://matrix.org`) |
| `mattermost` | mattermostdriver | `mattermost_url` **and** `mattermost_bot_token` | `mattermost_allowed_channel_ids` | `mattermost_scheme` (`https`), `mattermost_port` (`443`) |

The `.env` names are the upper-case forms of the keys shown. Allowlists are comma-separated.

### Allowlists are deny-by-default

An empty allowlist means **nobody**. Messages from a chat, channel, guild or room that isn't listed are dropped, and the adapter logs `Ignoring … add it to <key> to allow`. Discord checks the guild id, so Discord DMs (no guild) are always refused.

## What triggers a reply

- **Telegram:** any text in an allowed chat, plus `/start /project /projects /chat /status /files /task /memory /note`. Photos, documents, voice, audio and video captioned `/note` are saved as notes.
- **Slack:** DMs and `@mention`s (`app_mention`). A channel mention is answered once, not twice. Commands: `/project /projects /status /files /task /memory /note`.
- **Discord:** slash commands with the same set, plus plain messages that mention the bot. Commands register per allowed guild (`guild` scope) or globally.
- **Matrix:** direct rooms, messages that mention the bot's user id, and `!`-prefixed commands (`!project`, `!task`, …).
- **Mattermost:** messages starting with `!` or `/`, or containing the word "pantheon".

`/project <name>` persists the channel→project mapping. `/task <description>` calls `tasks.scheduler.schedule_agent_task(schedule="now")`.

## How replies are generated

Every adapter builds the same pipeline for a plain message:

1. It resolves the project through `ChannelStore.resolve(platform, channel_id)`.
2. It checks for skills: an explicit `/skill-name`, then auto-discovery using the project's `skill_discovery_<project>` vault mode. `suggest` shows accept/decline buttons on platforms that support them.
3. It creates `create_memory_manager(project_id, session_id)` and `AgentCore(host_exec=host_exec_allowed("background"), …)`, then `await agent.run_autonomous(message)`.
4. It sends the reply back in chunks (4000 characters on Telegram).

Session ids are stable per channel (`telegram-<chat>`, `slack:<channel>`, `discord-<channel>`, `matrix:<room>`, `mattermost:<channel>`), so episodic memory builds up per conversation.

**Host exec is off for bots.** `host_exec_allowed("background")` is false unless `AGENT_HOST_EXEC=always`, so bots never see `run_command`, `code_execute` or `git_*`. Bots also never pass `interactive=True`, so a `create_task` issued from a bot lands as a **proposed** plan awaiting approval in the web UI (see [jobs.md](jobs.md)).

## Outbound

- `MessagingGateway.broadcast(message, project_id=None)` sends to every mapped channel, optionally filtered by project.
- The `send_telegram` agent tool calls `messaging.adapters.telegram.send_message_to_all()`, which posts to every id in `telegram_allowed_chat_ids`.

## API (`backend/api/messaging.py`, mounted under `/api`)

| Method + path | Purpose |
|---|---|
| `GET /messaging/status` | `{"adapters": [...]}`, each `{name, display_name, running, configured, channel_count, error}` |
| `POST /messaging/{adapter}/restart` | Stop, then start with `raise_on_error=True`. Returns 500 with the message on failure. |
| `GET /messaging/channels` | Channels visible to running adapters |
| `GET` / `PUT /messaging/mappings` | List mappings, or bulk upsert (`{"mappings": [...]}`) |
| `PUT` / `DELETE /messaging/mappings/{channel_id}` | Set or remove one mapping (the channel falls back to the default project). DELETE returns 404 if there is no mapping. |
| `GET` / `PUT /messaging/default-project` | Default project for unmapped channels |

Tokens and allowlists are written through `PUT /api/settings`. `GET /api/settings` reports whether each token is set but never returns the token itself.

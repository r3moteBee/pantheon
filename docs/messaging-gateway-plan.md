# Messaging Gateway — Implementation Plan

**Date:** 2026-04-13  
**Version:** 1.0  
**Status:** Proposed

## Overview

Abstract messaging into a unified gateway so that Telegram, Discord, and future platforms (WhatsApp, Slack, etc.) share a common interface. Each platform adapter registers channels, and channels auto-map to Pantheon projects. A new **Messaging** settings page in the UI replaces the current Telegram-only section.

## Goals

1. **Gateway abstraction** — A `MessagingGateway` that manages adapter lifecycle (start/stop/restart) and routes inbound messages to the correct project via channel mappings.
2. **Discord integration** — A Discord adapter with feature parity to the existing Telegram bot (chat, /project, /memory, /task, /note, /files, skill resolution).
3. **Channel → Project mapping** — Persistent mappings stored in the vault, with a configurable default project for unmapped channels. Auto-discovery of channels on bot connect.
4. **Unified settings UI** — A "Messaging" tab/section that shows all adapters, their status, credentials, and channel-project mappings in one place.

## Architecture

### Backend directory structure

```
backend/
├── messaging/
│   ├── __init__.py              # Package init, exports
│   ├── gateway.py               # MessagingGateway — adapter registry, lifecycle, routing
│   ├── base.py                  # BaseMessagingAdapter ABC
│   ├── models.py                # Pydantic models (ChannelMapping, AdapterConfig, InboundMessage)
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── telegram.py          # TelegramAdapter (refactored from telegram_bot/bot.py)
│   │   └── discord.py           # DiscordAdapter (new)
│   └── channel_store.py         # CRUD for channel-project mappings (vault-backed)
```

### Core abstractions

#### `BaseMessagingAdapter` (base.py)

```python
class BaseMessagingAdapter(ABC):
    """Base class all messaging platform adapters must implement."""
    
    name: str                    # "telegram", "discord", etc.
    display_name: str            # "Telegram", "Discord"
    
    @abstractmethod
    async def start(self) -> None: ...
    
    @abstractmethod
    async def stop(self) -> None: ...
    
    @abstractmethod
    async def is_running(self) -> bool: ...
    
    @abstractmethod
    async def list_channels(self) -> list[ChannelInfo]: ...
    
    @abstractmethod
    async def send_message(self, channel_id: str, text: str) -> None: ...
    
    def resolve_project(self, channel_id: str) -> str:
        """Look up channel→project mapping, fall back to default project."""
        ...
```

Key design points:

- `resolve_project()` is implemented on the base class, not per-adapter. It calls the shared `ChannelStore` so all adapters use the same mapping table.
- Each adapter normalises its channel IDs to strings with a platform prefix: `telegram:123456`, `discord:987654321`. This prevents collisions and makes the mapping table self-describing.
- The `list_channels()` method lets the UI auto-discover available channels for mapping.

#### `MessagingGateway` (gateway.py)

```python
class MessagingGateway:
    """Central registry managing all messaging adapters."""
    
    _adapters: dict[str, BaseMessagingAdapter]
    
    async def startup(self) -> None:
        """Start all configured adapters."""
    
    async def shutdown(self) -> None:
        """Stop all running adapters."""
    
    async def restart_adapter(self, name: str) -> dict: ...
    
    def get_adapter(self, name: str) -> BaseMessagingAdapter | None: ...
    
    def status(self) -> list[AdapterStatus]: ...
    
    async def broadcast(self, message: str, project_id: str | None = None) -> None:
        """Send to all channels mapped to a project (or all channels)."""
```

The gateway is a singleton (like `get_mcp_manager()`). On app startup, it replaces the current direct `start_telegram_bot()` call in `main.py`.

#### `ChannelStore` (channel_store.py)

Stores mappings in the vault as a JSON blob under key `messaging_channel_mappings`:

```python
@dataclass
class ChannelMapping:
    channel_id: str          # "discord:123456789" or "telegram:-100123"
    platform: str            # "discord", "telegram"
    channel_name: str        # Human-readable (auto-discovered)
    project_id: str          # Pantheon project ID
    
class ChannelStore:
    def get_mappings(self) -> list[ChannelMapping]: ...
    def set_mapping(self, channel_id: str, project_id: str) -> None: ...
    def remove_mapping(self, channel_id: str) -> None: ...
    def get_default_project(self) -> str: ...
    def set_default_project(self, project_id: str) -> None: ...
    def resolve(self, platform: str, raw_channel_id: str) -> str:
        """Return project_id for a channel, or the default project."""
```

The default project is stored under vault key `messaging_default_project` (defaults to `"default"`).

#### Pydantic models (models.py)

```python
class InboundMessage(BaseModel):
    platform: str
    channel_id: str
    user_id: str
    user_display_name: str
    text: str
    attachments: list[Attachment] = []
    
class ChannelInfo(BaseModel):
    channel_id: str          # Platform-prefixed
    raw_id: str              # Platform-native ID
    name: str
    platform: str
    
class AdapterStatus(BaseModel):
    name: str
    display_name: str
    running: bool
    configured: bool         # Has credentials
    channel_count: int
    error: str | None = None
```

### Adapter details

#### Telegram adapter

Refactor the existing `telegram_bot/bot.py` (569 lines) into `messaging/adapters/telegram.py` implementing `BaseMessagingAdapter`. The logic stays identical — same commands, same skill resolution, same note handling — but:

- Replace the in-memory `_chat_projects` dict with `ChannelStore.resolve()`.
- Prefix channel IDs: `telegram:{chat_id}`.
- Move `_get_token()` and `_get_allowed_ids()` into the adapter class.
- Keep `telegram_bot/` as a thin re-export for backward compatibility during transition.

#### Discord adapter

New file: `messaging/adapters/discord.py`

Uses `discord.py` library (add `discord.py>=2.3` to requirements.txt).

```python
class DiscordAdapter(BaseMessagingAdapter):
    name = "discord"
    display_name = "Discord"
```

Feature mapping (Discord equivalents of Telegram commands):

| Telegram | Discord | Notes |
|----------|---------|-------|
| /start | Bot join message | Sent on guild join |
| /project \<name\> | /project \<name\> | Slash command |
| /projects | /projects | Slash command |
| /status | /status | Slash command |
| /files | /files | Slash command |
| /task \<desc\> | /task \<desc\> | Slash command |
| /memory \<query\> | /memory \<query\> | Slash command |
| /note | /note | Slash command + attachment support |
| Plain text → agent | Plain text in mapped channel | Messages in a mapped channel route to that project's agent |
| Skill suggest (inline keyboard) | Skill suggest (Discord buttons) | Interactive component |

Channel-project mapping behaviour for Discord:

- Each Discord text channel has a unique snowflake ID → `discord:{channel_id}`.
- When a message arrives in a channel, `ChannelStore.resolve("discord", channel_id)` returns the mapped project or the default project.
- The `/project` slash command overrides the channel mapping for the current interaction (ephemeral per-user), but the persistent mapping is set via the Settings UI.
- `list_channels()` returns all text channels the bot can see across all guilds.

Credential storage:

- `discord_bot_token` in vault (same pattern as `telegram_bot_token`).
- `discord_allowed_guild_ids` in vault (optional whitelist, empty = all guilds the bot is in).

### API routes

New router: `backend/api/messaging.py`

```
GET    /api/messaging/status              → list all adapters with status
POST   /api/messaging/{adapter}/restart   → restart a specific adapter
GET    /api/messaging/channels            → all discovered channels across platforms
GET    /api/messaging/mappings            → current channel→project mappings
PUT    /api/messaging/mappings            → bulk update mappings
PUT    /api/messaging/mappings/{channel_id} → set single mapping
DELETE /api/messaging/mappings/{channel_id} → remove mapping (falls back to default)
GET    /api/messaging/default-project     → get default project
PUT    /api/messaging/default-project     → set default project
```

The existing `POST /api/settings/restart-telegram` is preserved for backward compatibility but internally delegates to `gateway.restart_adapter("telegram")`.

### Settings model changes

Add to `SettingsUpdate` in `api/settings.py`:

```python
discord_bot_token: str | None = None
discord_allowed_guild_ids: str | None = None
messaging_default_project: str | None = None
```

Add to `config.py` `Settings`:

```python
discord_bot_token: str = Field(default="", env="DISCORD_BOT_TOKEN")
discord_allowed_guild_ids: str = Field(default="", env="DISCORD_ALLOWED_GUILD_IDS")
```

Add to `.env.example`:

```env
# Discord
DISCORD_BOT_TOKEN=
DISCORD_ALLOWED_GUILD_IDS=
```

### Frontend changes

#### New: `MessagingSettings.jsx` component

Replaces the current `TelegramSection` inside `Settings.jsx` with a full messaging gateway UI:

**Layout:**

1. **Adapter cards** — One card per platform (Telegram, Discord). Each shows:
   - Status indicator (running/stopped/unconfigured)
   - Credential fields (token input, allowed IDs)
   - Start/Stop/Restart buttons
   
2. **Channel mapping table** — Below the adapter cards:
   - Auto-populated from `GET /api/messaging/channels`
   - Columns: Platform icon, Channel name, Channel ID, Project dropdown, Remove button
   - "Default project" selector at the top
   - Unmapped channels show the default project in grey italic
   
3. **Auto-map button** — Fetches channels from all running adapters, shows them in the table for mapping.

**API client additions** (`api/client.js`):

```javascript
export const messagingApi = {
  status: () => api.get('/messaging/status'),
  restartAdapter: (name) => api.post(`/messaging/${name}/restart`),
  getChannels: () => api.get('/messaging/channels'),
  getMappings: () => api.get('/messaging/mappings'),
  updateMappings: (mappings) => api.put('/messaging/mappings', { mappings }),
  setMapping: (channelId, projectId) => api.put(`/messaging/mappings/${channelId}`, { project_id: projectId }),
  removeMapping: (channelId) => api.delete(`/messaging/mappings/${channelId}`),
  getDefaultProject: () => api.get('/messaging/default-project'),
  setDefaultProject: (projectId) => api.put('/messaging/default-project', { project_id: projectId }),
}
```

### Startup integration (main.py)

Replace:
```python
from telegram_bot.bot import start_telegram_bot, stop_telegram_bot
await start_telegram_bot()
# ...
await stop_telegram_bot()
```

With:
```python
from messaging.gateway import get_messaging_gateway
gateway = get_messaging_gateway()
await gateway.startup()
# ...
await gateway.shutdown()
```

## Implementation phases

### Phase 1: Gateway abstraction + Telegram refactor

**Files created/modified:**

| File | Action |
|------|--------|
| `backend/messaging/__init__.py` | Create |
| `backend/messaging/base.py` | Create — BaseMessagingAdapter ABC |
| `backend/messaging/models.py` | Create — Pydantic models |
| `backend/messaging/gateway.py` | Create — MessagingGateway singleton |
| `backend/messaging/channel_store.py` | Create — vault-backed channel mappings |
| `backend/messaging/adapters/__init__.py` | Create |
| `backend/messaging/adapters/telegram.py` | Create — refactor from telegram_bot/bot.py |
| `backend/telegram_bot/bot.py` | Modify — thin wrapper that imports from messaging.adapters.telegram |
| `backend/api/messaging.py` | Create — new API router |
| `backend/main.py` | Modify — use gateway instead of direct telegram imports |
| `backend/api/settings.py` | Modify — add messaging_default_project field |
| `backend/config.py` | Modify — add discord env vars |
| `.env.example` | Modify — add Discord + messaging vars |

**Deliverable:** Existing Telegram functionality works identically, but now through the gateway. Channel mappings are available via API. No UI changes yet.

### Phase 2: Discord adapter

**Files created/modified:**

| File | Action |
|------|--------|
| `backend/messaging/adapters/discord.py` | Create — full Discord adapter |
| `backend/requirements.txt` | Modify — add `discord.py>=2.3` |

**Deliverable:** Discord bot connects, responds to slash commands, routes messages by channel mapping.

### Phase 3: Frontend messaging settings

**Files created/modified:**

| File | Action |
|------|--------|
| `frontend/src/components/MessagingSettings.jsx` | Create — adapter cards + channel mapping UI |
| `frontend/src/components/Settings.jsx` | Modify — replace TelegramSection with MessagingSettings |
| `frontend/src/api/client.js` | Modify — add messagingApi |

**Deliverable:** Unified messaging settings page with adapter management and visual channel-to-project mapping.

### Phase 4: Polish + docs

- Update README.md with Discord setup instructions
- Add docs/messaging-gateway.md with architecture reference
- Update DELIVERY_CHECKLIST.md
- Bump VERSION

## Dependencies

**New Python packages:**
- `discord.py>=2.3` — Discord bot library (async, slash commands, components)

**No new frontend packages required.**

## Migration notes

- The `telegram_bot/` package remains as a backward-compatible shim. It re-exports `start_telegram_bot`, `stop_telegram_bot`, and `restart_telegram_bot` from the new `messaging.adapters.telegram` module so that any external references continue to work.
- Existing `.env` variables (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS`) continue to work unchanged.
- Vault secrets for Telegram are unchanged; Discord secrets follow the same pattern.
- The `_chat_projects` in-memory dict (current per-chat project override) becomes a session-level override on top of the persistent `ChannelStore` mapping. If a user runs `/project foo` in a channel, that overrides only for their current session — the persistent mapping remains what the admin configured in Settings.

## Open questions

1. **Discord slash command registration** — Global commands (available in all guilds) take up to an hour to propagate. Guild-specific commands are instant. Recommend guild-specific during development, global for production. Should this be configurable?
2. **Per-user vs per-channel project in Discord** — Telegram is 1:1 chats so chat_id ≈ user. Discord channels are shared. The plan above maps channels to projects (not users to projects). A user running `/project` gets a session override. Is this the right model?
3. **Rate limiting** — Discord has stricter rate limits than Telegram. Should we add a message queue / retry layer in the base adapter, or handle it per-adapter?

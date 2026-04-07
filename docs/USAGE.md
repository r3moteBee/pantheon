# Pantheon Usage Guide

A practical guide to getting the most out of your Pantheon agent. This covers how to drive the agent effectively via the web UI, Telegram, and autonomous tasks.

## 1. Projects

Projects are Pantheon's unit of isolation. Each project has its own:

- Workspace (files the agent can read/write)
- Memory (semantic, episodic, graph — none of it leaks between projects)
- Personality (soul.md — how the agent speaks and thinks in that context)
- MCP tool scopes

**Rule of thumb:** one project per domain or long-running initiative. Don't use a single project for unrelated work — it pollutes memory and makes recall noisy.

Switch projects from the sidebar in the web UI, or via `/project <name>` in Telegram.

## 2. Personalities and personas

Every project has a `soul.md` file that defines the agent's identity in that project. Personas (in the Persona Library) are reusable templates — applying one is a **one-time copy** into the project. Edits you make after applying stay in the project; they do not flow back to the library.

To save a customised project personality as a new persona, use **"Save as Persona"** in the Personality Editor. This prevents personality proliferation: changes are explicit, not automatic.

## 3. Talking to the agent effectively

### Be specific about anaphora
The agent's tool layer has no implicit handle on your previous messages or its own. When you say "save this" or "remember that observation", it's reliable to either:

- Name the target explicitly: *"Save your previous response verbatim to `ANALYSIS/2026-04-07-ai-maturity.md`."*
- Or trust the new `save_last_response` behaviour (see §5) which interprets self-references automatically.

### Give paths, not just verbs
*"Write a note"* is ambiguous. *"Write a note to `research/notes/hbm-supply-chain.md`"* produces exactly what you want.

### Chain tools in one prompt
The agent can execute multiple tools per turn. *"Search for X, then save a summary to `research/X-summary.md`"* is usually faster than doing it in two turns.

## 4. Memory tiers

Pantheon has four memory stores the agent can read and write:

- **Working** — short-term scratch within a single conversation
- **Episodic** — conversational facts, who said what, when
- **Semantic** — key insights, facts, distilled knowledge
- **Graph** — entities and their relationships

When you want the agent to remember something across sessions, ask it to `remember` in the appropriate tier. *"Remember in semantic memory that…"* is more durable than just *"remember that…"*.

To recall, say *"what do you know about X"* — the agent will search across tiers. You can also use `/memory <query>` in Telegram.

## 5. Saving the agent's own output

A common pattern: the agent produces a long analysis, and you want to file it. Use any of these:

- **"Save your last response to `ANALYSIS/<filename>.md`"** — routes through the `save_last_response` tool, which reads the previous assistant message directly.
- **"Add this observation as a trend in the ANALYSIS folder"** — the agent now interprets "this/that/above" as a reference to its own last message and will not ask you to paste it back.
- For .md files, a YAML frontmatter block (title, date, tags) is added automatically.

## 6. Web search and the browser

Pantheon ships with:

- `web_search` — free DuckDuckGo scraping by default, or SearXNG if you installed with `--with-searxng`
- `web_fetch` — plain HTTP GET for static pages
- **Browser tools** (if you installed with `--with-browser`) — Playwright-backed `browser_open`, `browser_read`, `browser_click`, `browser_type`, `browser_screenshot`. Use these for JavaScript-heavy sites, logged-in pages, or multi-step interactions. The browser session persists per project across tool calls.

Set `BROWSER_HEADLESS=false` in `.env` to watch the browser drive itself during debugging.

## 7. Autonomous tasks

Use `create_task` (or `/task <description>` in Telegram) to schedule the agent to work on something independently:

- `schedule: "now"` — run immediately
- `schedule: "interval:60"` — every 60 minutes
- `schedule: "0 9 * * *"` — daily at 9 AM (cron)

Long-running tasks should call `send_telegram` at key checkpoints so you stay in the loop.

## 8. Telegram integration

After setting `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_CHAT_IDS` in `.env`, your bot supports:

| Command | Description |
|---|---|
| `/start` | Greeting + help |
| `/project <name>` | Switch active project |
| `/projects` | List projects |
| `/status` | Agent status |
| `/files` | List workspace files |
| `/task <desc>` | Schedule autonomous task |
| `/memory <query>` | Search memories |
| `/note [text]` | Save message (or attachment) as a note in the project |
| *(plain text)* | Chat with the agent |

### `/note` — capture anything on the go
`/note` is a fast-capture command. Anything you send with it lands in `<project>/workspace/notes/`:

- **Text only**: `/note Interesting thought about HBM supply chains…` → saves `note-<timestamp>.md`.
- **Photo with caption**: attach a photo and use `/note caption text` → saves both the image and a markdown sidecar linking to it.
- **File upload**: attach any document with caption `/note your commentary` → saves the file and a markdown note.
- **Voice memo**: attach a voice clip with caption `/note` → saves the `.ogg`.

All notes are also indexed into semantic memory so you can recall them later via `/memory` or through the agent in chat.

## 9. Indexing a corpus into memory

Drop files into the project workspace and tell the agent: *"index the workspace"* (or call `index_workspace` directly). This ingests Markdown (with frontmatter), text, CSV, PDF, and code files into semantic + graph memory. After indexing, recall and chat become much richer.

Re-index with `force: true` when you edit files.

## 10. Operational tips

- **Run `consolidate_memory` at the end of a productive session.** It distills the conversation into semantic and graph memory so future sessions pick up where you left off.
- **Keep `soul.md` short and specific.** Long, generic personalities bleed into analytical answers. Use the `minimal` personality weight for research projects.
- **Watch the cost of long context.** Recall returns ~10-13 items by default; if you see it crowding out current work, ask the agent to narrow its recall with specific tier filters.
- **Back up `~/pantheon/data/`** — it contains all your memory, projects, and workspace files.

## 11. Uninstall / reinstall

```bash
./uninstall.sh              # stop services, keep data
./uninstall.sh --purge      # wipe everything, including data/
```

Reinstall is idempotent:

```bash
curl -fsSL https://raw.githubusercontent.com/r3moteBee/pantheon/main/deploy.sh | bash -s -- --yes
```

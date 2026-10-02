# Pantheon Usage Guide

A practical guide to getting the most out of your Pantheon agent. This covers how to drive the agent effectively via the web UI, the messaging bots (Telegram, Slack, Discord, Matrix, Mattermost), and autonomous tasks.

## 1. Projects

Projects are Pantheon's unit of isolation. Each project has its own:

- Workspace (files the agent can read/write)
- Memory (semantic, episodic, graph — none of it leaks between projects)
- Personality (soul.md — how the agent speaks and thinks in that context)
- MCP tool scopes

**Rule of thumb:** one project per domain or long-running initiative. Don't use a single project for unrelated work — it pollutes memory and makes recall noisy.

Switch projects from the sidebar in the web UI, or via `/project <name>` in a messaging bot (`!project` on Matrix).

### Example Project Setup
* **Project 1: `semiconductor-research`**: Dedicated to market research on chip manufacturing. Contains spec sheets, company filings, and news articles.
* **Project 2: `fitness-coach`**: Dedicated to personal health and training.
If you ask the agent in `fitness-coach` for a workout plan, it will not recall any information about chip manufacturing from `semiconductor-research`, keeping context perfectly clean.

## 2. Personality and presets

The agent's identity is `soul.md` and its working rules are `agent.md`, both edited in **Settings → Personality**. Every project follows the **global** personality, including your later edits to it, until you give the project its own.

- **Presets** (Hermes, Athena, Mnemosyne, …) are ready-made `soul.md` voices. Applying one to a project is a one-time copy. The global **Key Commitments** (never fabricate, flag uncertainty, …) are always appended.
- **Where to apply:** in Settings → Personality with the project as the scope, in the project's **Settings** tab (Personality), or on its card in Projects.
- **Use global:** removes the project's copy, so it follows the global personality again.
- **Save as preset:** snapshots the current `soul.md` so you can reuse it. Delete your own presets from the same picker.

New projects follow the global personality unless you pick a preset when creating them.

## 3. Talking to the agent effectively

### Be specific about anaphora
The agent's tool layer has no implicit handle on your previous messages or its own. When you say "save this" or "remember that observation", it's reliable to either:

- Name the target explicitly: *"Save your previous response verbatim to `ANALYSIS/2026-04-07-ai-maturity.md`."*
- Or trust the new `save_last_response` behaviour (see §5) which interprets self-references automatically.

### Give paths, not just verbs
*"Write a note"* is ambiguous. *"Write a note to `research/notes/hbm-supply-chain.md`"* produces exactly what you want.

### Chain tools in one prompt
The agent can execute multiple tools per turn. *"Search for X, then save a summary to `research/X-summary.md`"* is usually faster than doing it in two turns.

### Chat slash commands
The web chat understands two kinds of leading `/` command:

- **`/<skill-slug> [args]`** — run an installed skill explicitly (typing `/` opens the skill picker; `_` and `-` are interchangeable).
- **`/model <class|auto> [message]`** — pin this conversation to a model class: `agent`, `quick`, `code`, `long_context` or `vision`. `/model auto` returns to automatic routing; `/model` alone shows the current pin. The model picker next to the chat input does the same thing. Pins last until the backend restarts, and a class only takes effect if it has its own route under Settings → LLMs → Model routing.

## 4. Memory tiers

Pantheon has five memory stores the agent can read and write:

- **Working** — in-process scratch for the current conversation; not persisted
- **Episodic** — chat history and task logs: who said what, when
- **Semantic** — embedded chunks from indexed artifacts and workspace files
- **Graph** — entities and their relationships
- **Archival** — markdown notes and a project summary (Memory → Archival tab)

When you want the agent to remember something across sessions, ask it to `remember` in the appropriate tier. *"Remember in semantic memory that…"* is more durable than just *"remember that…"*.

To recall, say *"what do you know about X"* — the agent will search across tiers. You can also use `/memory <query>` in a messaging bot. Recall runs on every chat turn anyway, so you don't need magic phrases to reach earlier work.

How automatic recall behaves:

- It looks for memories related to your message, reranks them, and leaves out ones the reranker scores as unrelated (`RECALL_MIN_RELEVANCE`, default 0.05). Small talk gets no memories at all. It also skips messages from the current conversation, because the agent can already see those.
- The agent sees each memory labelled by where it came from: `[note]` (things you asked it to remember, indexed sources), `[user said]` (something you said in an earlier chat), `[your earlier reply]` (its own past answer, which may be out of date) and `[graph]` / `[archive]`. For anything time-sensitive, like latest versions, prices or news, it is told to use its tools even when a memory seems to answer.
- Memories and the current time are added to your newest message, not to the system prompt. The long, unchanging part of the prompt therefore stays identical from turn to turn, and local model servers (llama.cpp, vLLM) can reuse their cache for it, which makes replies noticeably faster.

**Long conversations:** each turn sends only the newest part of the chat that fits `HISTORY_TOKEN_BUDGET` (by default a quarter of the model's context window, at most 24K tokens). Older turns drop out in blocks of about 10 turns, so the model server can keep reusing its cache between drops. They aren't forgotten: when your message relates to an earlier turn, recall repeats that turn next to your message, labelled `[earlier in this chat, ...]`. It does the same for relevant turns still in the history but far back, because small models tend to miss facts in the middle of a long prompt. Asking "did I mention X earlier?" works the same way.

**"What's the latest version of X?"** When a search asks about a product's version, `web_search` also returns that product's release table from endoflife.date, or, for projects released on GitHub, its newest stable release (newer pre-releases are flagged as not stable). These sit above the search snippets, which are often months old. The lookup sends only the product name. Turn it off with `SEARCH_RELEASE_FACTS=false`.

**New or unfamiliar names:** if you ask what something is, or ask the agent to use, set up or switch to something with a capitalised or camel-case name, such as "What is Jev?" or "use Jev to…", and its first draft didn't search the web, Pantheon searches "What is <name>?" before it answers. The same check applies to time-sensitive questions (`AGENT_FORCE_SEARCH`). The agent guide also tells it to check that a product can actually do what you're asking before giving setup steps.

**Sources:** when an answer relies on web results, it ends with a **Sources** list. If the agent doesn't write one, Pantheon appends up to two result URLs whose text actually contains the answer's main fact (the version, name or value it gives). Turn it off with `ANSWER_SOURCES=false`.

**Speed:** questions that have to be looked up (time-sensitive questions or an unfamiliar name) search right away, before the model's first step; version questions use the plain question as the query, other time-sensitive ones get the current month and year added. With `AGENT_THINKING`, the model stops thinking once it has tool results, except in the round right after that first search, and a reply it had left inside its reasoning is streamed. Settings: `AGENT_PRE_SEARCH`, `AGENT_THINKING_AFTER_TOOLS`.

**Small local models:** if the agent answers current-fact questions from memory instead of searching, set `AGENT_THINKING=true`. The agent model then reasons before acting; the server must accept `chat_template_kwargs` (llama.cpp and vLLM do). In one test with a 9B model it searched on 14 of 14 such questions instead of 8.

## 5. Saving the agent's own output

A common pattern: the agent produces a long analysis, and you want to file it. Use any of these:

- **"Save your last response to `ANALYSIS/<filename>.md`"** — routes through the `save_last_response` tool, which reads the previous assistant message directly.
- **"Add this observation as a trend in the ANALYSIS folder"** — the agent interprets "this/that/above" as a reference to its own last message and will not ask you to paste it back.
- **Control the scope and transform.** `save_last_response` accepts `history_count` and `mode`, so you can say:
  - *"Save the last 5 messages verbatim to `notes/session.md`"*
  - *"Summarize the last 3 messages and save to `ANALYSIS/ai-maturity-summary.md`"* (`mode=summarize`)
  - *"Research the last observation further and save the expanded note to `research/hbm-deep-dive.md`"* (`mode=research` — runs a web search pass and asks the model to write an enriched briefing)
  - *"Save the last response as a bulleted action-items list to `todos.md`"* (`mode=custom` with your own transform prompt)
- For .md files, a YAML frontmatter block (title, date, tags, mode, source_messages) is added automatically.

### Example Chat Exchange
> **User**: *Provide a high-level summary of the architectural tiers of Pantheon.*
> **Agent**: *(Outputs the detailed memory and runtime architecture...)*
> **User**: *Save that response verbatim to docs/memory_tiers.md with title 'Memory Tiers Overview' and tags 'architecture, docs'*
> 
> *The agent calls `save_last_response` with path=`docs/memory_tiers.md` and generates a markdown file in the workspace with automatic frontmatter:*
> ```markdown
> ---
> title: Memory Tiers Overview
> date: 2026-05-30
> tags: [architecture, docs]
> ---
> (The detailed architectural breakdown is written here verbatim)
> ```

## 6. Web search and the browser

### Search provider chain
Pantheon's `web_search` tool walks a configurable chain of providers (default: **Brave → SearXNG → DuckDuckGo**). It falls through to the next provider on any of:

- HTTP error / network failure
- Empty result set (HTTP 200 but zero hits)
- Exhausted daily or monthly quota (tracked locally per provider)
- Rate-limit cap (per-provider RPS, e.g. Brave free tier = 1 req/sec)

Configure the chain in **Connections → Web search**: reorder providers, set per-provider daily/monthly limits and RPS caps, add API keys, enable/disable individual providers, and watch live usage progress bars. Reset counters at the start of each billing cycle. Results are cached for 10 minutes per query so retry loops don't burn quota.

When a fallthrough happens, the result is prefixed with a one-line trace like `[searched via ddg — fallthrough: brave: skipped (monthly quota 2000 reached); searxng: error (HTTPError)]` so you (and the agent) can see exactly why a provider was skipped.

Pantheon also ships with:

- `web_search` — Walks a configurable chain of search providers including:
  - **SearXNG** (local dockerized proxy search)
  - **Brave Search** (requires Brave API key)
  - **DuckDuckGo** (scraped fallback, free)
  - **Tavily Search** (API-optimized search for LLMs)
  - **Google Custom Search** (requires Custom Search Engine ID + Custom Search JSON API Key)
  - **Bing Web Search** (requires Bing Web Search API Key)
  - **Wikipedia** (returns structured Wiki summaries and articles)
- `web_fetch` — reads a page as markdown **without saving it**. To keep a source (indexed, searchable, linked into the graph), use `ingest_source` instead.
- **Browser tools** (if you installed with `--with-browser`) — Playwright-backed `browser_open`, `browser_read`, `browser_click`, `browser_type`, `browser_screenshot`. Use these for JavaScript-heavy sites, logged-in pages, or multi-step interactions. The browser session persists per project across tool calls.

Set `BROWSER_HEADLESS=false` in `.env` to watch the browser drive itself during debugging.

## 7. Autonomous tasks

Use `create_task` (or `/task <description>` in a messaging bot) to schedule the agent to work on something independently:

- `schedule: "now"` — run immediately
- `schedule: "interval:60"` — every 60 minutes
- `schedule: "0 9 * * *"` — daily at 9 AM (cron)

Long-running tasks should call `send_telegram` at key checkpoints so you stay in the loop.

### Examples of Autonomous Tasks
* **Weekly Market Briefing**
  * **Description**: *"Every Monday, search for recent updates on High Bandwidth Memory (HBM) supply chains, summarize the top 3 news items, write the report to `research/weekly-hbm-report.md`, and notify me on Telegram with the summary."*
  * **Schedule**: `"0 9 * * 1"` (Cron expression for every Monday at 9 AM)
* **Daily Release Watch**
  * **Description**: *"Check the releases page of GitHub repository 'ollama/ollama', ingest any new release notes via the github/release adapter, index it into memory, and alert me if a new version is released."*
  * **Schedule**: `"interval:1440"` (every 24 hours/1440 minutes)

## 8. Messaging bots

Pantheon ships five messaging adapters: **Telegram, Slack, Discord, Matrix and Mattermost**. Configure them in **Settings → Channels** (stored in the vault) or via `.env` (`TELEGRAM_*`, `SLACK_*`, `DISCORD_*`, `MATRIX_*`, `MATTERMOST_*`).

**Allowlists are deny-by-default.** An empty `telegram_allowed_chat_ids`, `slack_allowed_channel_ids`, `discord_allowed_guild_ids`, `matrix_allowed_room_ids` or `mattermost_allowed_channel_ids` means *nobody* can talk to that bot. Messages from unlisted chats are ignored and logged with the ID to add.

All five support the same commands (`/project`, `/projects`, `/status`, `/files`, `/task`, `/memory`, `/note`) plus plain-text chat. Matrix uses a `!` prefix (`!project`), Mattermost accepts `!` or `/`. Bots run as background contexts, so host-exec tools (`run_command`, `git_*`) are off there unless `AGENT_HOST_EXEC=always`. Setup details per platform: [messaging.md](messaging.md).

### Telegram

After setting the bot token and allowed chat IDs, your bot supports:

| Command | Description |
|---|---|
| `/start` | Greeting + help |
| `/chat <text>` | Chat with the agent (same as plain text) |
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

Drop files into the project workspace and tell the agent: *"index the workspace"* (or call `index(target="workspace")` directly). This ingests Markdown (with frontmatter), text, CSV, PDF, and code files into semantic + graph memory. After indexing, recall and chat become much richer.

Re-index with `force: true` when you edit files.

### Ingesting sources (URLs, videos, filings)
For web sources you want to keep, ask the agent to *"ingest <url> as a blog/announcement"* — it calls `ingest_source` (or `batch_ingest_sources`), which fetches, extracts typed topics, saves an artifact and links it into the graph. `list_source_adapters` shows every source type (YouTube, blog, PDF, web, forum, podcast, GitHub, CFR, MA Legislature, SEC EDGAR). Re-ingesting the same source updates the existing artifact instead of duplicating it.

For YouTube, connect a YouTube transcript MCP server; the agent searches with that MCP's tools (`mcp_<server>_search_youtube` …) and forwards each video's `published` date to `ingest_source` so paths get real dates.

### Ingestion Example
If you place a PDF named `HBM3_Specification.pdf` into your workspace folder `~/pantheon/data/projects/<project-slug>/workspace/`, you can query the agent:
> **User**: *Index the workspace.*
> 
> *The agent will call `index(target="workspace")`, extract the text from the PDF, chunk it, embed it, and insert it into ChromaDB. Once finished, you can query information from that file across sessions:*
> 
> **User**: *What are the pin configurations for HBM3 based on the specs in the workspace?*
> **Agent**: *(Automatically recalls relevant sections of the PDF from semantic memory and answers the question with source provenance.)*

## 10. Operational tips

- **MCP budgets.** Every MCP call is counted per connection (Connections → MCP servers → expand a card → *Usage & budget*). Set a daily or monthly limit on any connection. Over the limit, calls are refused, and search tools fall back to the built-in web search. Past 80% of a limit, tool results carry a note so the agent can pace itself. Tavily is metered in credits using its pricing (advanced search = 2, …) and also shows your account's own usage. Other connections count calls.

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

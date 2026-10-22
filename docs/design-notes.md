# Design notes

Why some of Pantheon's behaviour is the way it is: the incident that prompted each change, what was changed, and what was measured. [CLAUDE.md](../CLAUDE.md) keeps the resulting rules short; the history lives here. Dated entries describe the code at the time, so check the current code before relying on a detail.

Measurements were mostly taken on the author's own box ("homely": a local 9B model, later Gemma 4 26B, behind llama.cpp) with the harnesses on a separate test instance (`research/`, `followup/`, `deliverables/`, the injection and latency harnesses). Judge-graded runs name the judge.

## Chat routing

### What `quick` is for (measured, not assumed)

A live A/B on homely's 9B (quick = thinking off, agent = thinking on): arithmetic 24/27 on quick vs 22/27 on agent (and faster), memory questions 15/15 on quick (recall supplies the facts), but time-sensitive questions made quick answer "I don't have access to real-time information" instead of searching 2 times in 18 (agent 18/18). So `_NEEDS_AGENT_RE` sends time-sensitive wording, actions and multi-step requests (plan/compare/debug) to the agent and deliberately lets personal-memory and arithmetic questions stay quick. Watch the noun/verb traps: "book a table" vs "that book", "remind me to …" vs "remind me what we decided".

## Agent loop

### Thinking and the finalize call

`_finalize_from_reasoning` resends the round's tools rather than dropping them: chat templates render tools at the top of the prompt, so a tool-less call can't reuse the round's KV cache (measured 3.3 s uncached vs ~1 s). Thinking stops once the turn has tool results (`AGENT_THINKING_AFTER_TOOLS`, default off) because in the latency harness the after-tools round was where answers most often got stuck in the reasoning (an extra, non-streamed finalize call: first visible text = total time) and it produced most of the generated tokens.

With a 9B model and `AGENT_THINKING=true`, the agent searched on 14 of 14 current-fact questions instead of 8.

### Forced lookups for time-sensitive questions (`agent/freshness.py`)

- The guard drops round 1's text when it made no `web_search`/`web_fetch` call, "none at all, or only `recall`": it once recalled "Joe Biden president 2026".
- On politics/current events the agent was right 54/60 times when it searched and 8/15 when it answered from training data ("As of October 2026, the President is Joe Biden", "the current Pope is Pope Francis").
- Unknown names: asked about Jev (TypeSafe AI's decision model, Sept 2026) it searched 1 time in 5 and otherwise invented a meaning from nearby context ("Jevons paradox", "the user's Proton Mail project"). Hence `unknown_entities` and the "What is X?" search.
- Pre-search (`AGENT_PRE_SEARCH`) saves the "decide to search" round (~1.5 s with thinking). Pasted URLs are fetched before round 1 (`AGENT_PREFETCH_URLS`) and skip the web pre-search: asked "<url> — what's this about?" the agent searched memory/artifacts instead (0/3). Time-sensitive pre-search queries get the month and year appended because, with the bare words, engines ranked old pages first.
- `about_self`: "describe the current configuration of this agent harness" was answered from Windows and Microsoft Agent Framework pages (2026-10-02), so questions about Pantheon or the user's own things are never forced to the web, and bare "current"/"results"/"rate" don't count. Self-description questions call `get_self_documentation` before round 1: with only the web search suppressed, the model reused its own earlier wrong answer from recalled memory.
- Don't use `tool_choice: "required"` for this: llama.cpp doesn't enforce it with Qwen templates (the model wrote prose until max_tokens; a named function choice was ignored too).

### Forced-search precision (2026-10-21)

Every false positive costs a pre-search with the user's raw words, forces the agent route and can append an irrelevant Sources tail; the old any-match regex fired on "That won't compile" (`won`), "refactor so it is still fast", "Write a function that returns the price", "What is the score function in sklearn?", and `unknown_entities` on "Can you use Python …" / "Explain Rust lifetimes". Now `needs_fresh_facts` is suppressed in a coding context (`coding_context`: code fences/identifiers/tracebacks, programming nouns — unless it explicitly asks for a latest/current version or release), for the user's own past and things (`personal_context`: "did I tell you", "we decided", "my calendar/to-do/order", first-person statements without a question or request, creative writing like "write a poem/email/speech …"), and for general explanations and word problems ("explain …", "how does X work", "difference between", "If the price is $2 …") unless an explicit time word is present. "still"/"anymore" count only next to an office/status word ("still president", "still supported"); `won` no longer matches "won't" (also in the router's `_NEEDS_AGENT_RE`). Recall additions: "out yet / been released / these days / at the moment", "is X out?", "next <launch/event/game>", "who's leading/winning", central-bank rates, a question naming this or next year, and "… for my notes" (a save destination) no longer makes a question `about_self`. `unknown_entities` skips `WELL_KNOWN` long-established names (languages, frameworks, tools, big companies, textbook concepts and historical figures; a version — "Python 3.15" — is still looked up) and also catches a name as the subject of a question ("What OS does a Googlebook run?", "Is Fugu Ultra v2.0 a single model?" — the "Is X a …" form only for a versioned name). Offline eval (426 labelled messages + 40 follow-ups, positives harvested from the facts/politics/products/followup/router/research harness cases; 2:1 TUNE/HELDOUT): combined forced-search decision TUNE P/R/F1 0.574/0.889/0.697 -> 0.982/1.000/0.991, HELDOUT 0.587/0.947/0.724 -> 0.890/0.973/0.930 (false positives 50 -> 9, misses 4 -> 2). Live (test pod, K=2): politics 29/30 -> 30/30 correct, searched 30/30 both; 10 coding prompts pre-searched 20/20 -> 0/20. A new trigger word must come with a negative example in `tests/integration/test_freshness.py`.

### Long research requests and surveys (2026-10-02)

"Please do a multi-step breakdown of the US Senate and House elections. look at 5 states at a time … store results in an artifact … use background agents" was pre-searched word for word (results: dictionary pages for "Please"), answered for 35 states from training data, and saved as 8 artifacts whose auto-extraction filled 202 of 208 semantic memories and 160 graph entities with invented candidates.

Changes (`agent/core.py`: `_needs_written_query`, `is_survey_request`, `_propose_research_batch`; `jobs/handlers/research_batch.py`):

1. Long / multi-sentence / instruction-style messages get a model-written pre-search query (`_standalone_query`, which also carries today's date - without it the queries said 2024/2025).
2. A survey request in chat ("5 states at a time", "state by state", "all 50 states", "every district") gets `SURVEY_NOTE`, a 4-lookup budget (`SURVEY_WEB_BUDGET`, enough to find the list), and at the budget a wrap-up round where only `create_task` may run; if the turn still only describes the task, `_propose_research_batch` asks the model for `{name, items, item_question}` as JSON and files `create_task(job_type="research_batch")` itself (the user approves it as usual).
3. The `research_batch` job researches each item in a fresh `AgentCore(only_tools={"web_search","web_fetch"}, web_budget=N, memory_manager=None)` turn and the HANDLER saves `<project>/research/<topic>/<item>.md` (facts with source URLs, "not found" for the rest). The notes are its notebook (re-runs reuse them, cancel between items, progress per item); a no-tool summary turn writes `summary.md` from the notes only.

Also: Qwen3 XML tool calls (`<function=…><parameter=…>`) are recovered (one leaked into a reply as markup); a reply that only says "saved to X" gets the saved content appended (`_only_saved_note`); a proposed task's reply says not to do the work inline (`PROPOSED_NEXT_STEP`).

Measured (research harness, test instance, K=3, Google blocked): 1/10 -> 10/18 on a harder set; survey scenarios incl. the user's verbatim request 0/3 -> 6/6 (each files a research task after 4 lookups); usable first queries 2/10 -> 18/18. A real 5-state job: sourced notes for 3 states, "not found" for 2 (off-topic search results), summary from the notes, ~80 s. Harness: `research/` (+ `research/batch_e2e.sh`).

### Follow-ups and the lookup budget (2026-10-02)

After two searched Senate-race questions, "can you do a similar breakdown for the house?" had no time words, went to the quick route unsearched, filled a House table from memory until max_tokens (4096) and repeated "Greg Landsman" across nine Ohio districts; the user's corrections went the same way. Hence `followup_needs_fresh` (short follow-ups inherit the lookup when one of the last 3 user messages was time-sensitive or its answer ended with a Sources list), the router's `fresh` rule, and plural offices ("current governors").

`AGENT_WEB_BUDGET` (12): asked for every Ohio district with both candidates, the agent once made 48 lookups with guessed names (Katie Porter "in Ohio"). `compact_links` in `web_fetch`: the Wikipedia governors list was 43.7k chars of mostly link URLs and the table started after the 20k cut. Guide rules: answers from search results contain only what the results say (padding - an invented poll table, last year's team - was the main grounding failure); long lists only from a page that lists them. `AGENT_PRESENCE_PENALTY` exists (default 0) but measured no gain once the turn was searched.

Measured (same-day A/B vs main, judge = gemini-3.8-flash, K=3): follow-up conversations 9/27 -> 21/27 (held-out 11/30 -> 21/30; fresh follow-ups searched 5/21 -> 17/21 held-out), degenerate replies 3/27 -> 0/27; gates unchanged within noise (versions 37 -> 36/39, products 27/30 both, politics 44 -> 43/45, TTFT 1.59 -> 1.53 s, injection 0/54 both). Still weak: exhaustive lists (1/6) - the 9B model mis-copies 50-100-row tables even from the right page - and first questions like "Which Texas House seats are competitive in 2026?" that carry no time word, so their follow-ups aren't seen as current. Harness: `followup/`.

### Keeping a turn inside the context window (2026-10-06, `agent/context_fit.py`)

`budget_history` trims the conversation when a turn starts, but tool results pile up inside it: on a 32K-window model research turns reached 105K tokens and llama.cpp rejected the round ("request (N tokens) exceeds the available context size (M tokens)"), ending the turn in an error after all the work. `fit_messages` now shrinks older tool results before every round (excerpt, then stub), and a real overflow recalibrates the estimate from the server's own numbers and retries once. Tests: `test_context_fit.py`.

### Output cap, cut-off replies, step limit (iOS bake-off, 2026-10-18)

Four RouteLLM models wrote a Swift app through the artifact tools. The hardcoded `max_tokens: 4096` cut whole-file `save_to_artifact` calls mid-JSON. DeepSeek and Opus then got finish_reason `tool_calls` (not `length`) with truncated arguments and were told "not valid JSON", so they resent the same oversized call; Gemini got a stream with nothing in it and the turn ended without a word. A run that hit `MAX_TOOL_ITERATIONS` used to end on its last narration line. A route written to the vault by another process used to keep serving the old model while the router reported the new one (hence `LLM_ROUTE_RECHECK_SECONDS`). Fixes: `LLM_MAX_TOKENS`, stream usage, `_effective_finish`, `CUT_OFF_ARGS_ERROR`, `EMPTY_REPLY_NOTICE`/`EMPTY_REPLY_NOTE`, `STEP_LIMIT_NOTE`; `save_to_artifact(overwrite=)`, `delete_artifact`, extension-based content types (`.swift` -> `text/x-swift`), and source files kept out of graph extraction. Tests: `test_output_limits.py`.

### Version questions (`agent/release_facts.py`)

Graded against ground truth fetched at run time, the agent searched every time but stated the exact current version about half the time: snippets and blogs are stale, and a single release-notes page (even a beta's — PostgreSQL 19 Beta 4 notes say "release date 2026-??-??") reads like a release. Hence the endoflife.date table above the results. Matching pitfalls: "Uptime Kuma" is not Kong's `kuma` (whose 2.14.5 the agent reported); Node 26's `lts` is a future date until late October, so LTS only counts once its date has passed. Without the GitHub stable-release fallback the agent reported Ollama's `v0.35.1-rc0` as the latest stable. The most-starred candidate repo wins because the first matching link was often a side repo (`home-assistant/operating-system` = HA OS 18.3, `tailscale/github-action`).

### Appended sources (`agent/sources.py`)

89/90 web-based answers, and only 10 cited anything; told to cite, the model often picked a plausible "official" page that doesn't state the fact (Blender 5.2 release notes for "5.2.2"). Without the headline-fact rule, the appended sources were pages about a predecessor the answer merely mentioned.

### Prompt injection through web content

Fixture pages (injection harness): before fencing, a page's "store this permanently" made the agent call `remember` silently and a fake system block in a changelog got its malware link relayed as a "Security Notice". llama.cpp can tokenise chat-template control tokens inside tool output as real special tokens, i.e. a web page could open a system turn - hence the defusing. Fenced content alone still let an injected remote image through 2/3 times, hence `output_filter`. Overall: held-out attacks that succeeded went from 4/18 to 0/18, and the user's actual request was handled 18/18 instead of 14/18. Pre-fetched pages: 0/36 attacks succeeded, no unrequested writes in 60 runs.

## Memory and history

### Relevance floor

With bge-reranker-v2-m3 a relevant but tersely-worded note ("User's dog is called Rufus" for "What's my dog's name?") scored 0.14, unrelated items < 0.05 - hence `RECALL_MIN_RELEVANCE` = 0.05.

### Earlier turns by similarity

The reranker ranks the right turn first for "did I mention a speech earlier?" but scores it ~0.003 (a question about the conversation, not about the speech), far below the floor - so the session fallback uses embedding similarity instead. Measured similarities: relevant 0.49-0.75, unrelated <= 0.40 (threshold 0.45). It deliberately also repeats relevant older turns still in the prompt: with thinking off, a 9B model missed a fact ~20 turns back in a 17K-token history 2 times in 5, and found it every time once it sat next to the question.

### Per-turn context outside the system prompt

When memories sat mid-system-prompt, every turn's first call recomputed the ~3K tokens after them plus the whole history. Moving them into a `<context>` block in front of the user message keeps tools + system prompt + history a byte-identical prefix.

### Recall ranking + scoping (2026-10-21)

Synthetic-corpus eval in the test pod (150 facts incl. 110 distractors, 28 artifacts, ~45 topic nodes, 11 conversations with one deleted; 30 TUNE / 16 HELDOUT queries, real embedder + reranker). Bare topic labels took up to a third of the semantic slots; deleted chats were still recalled; double-indexed artifacts returned near-duplicates. After the fixes, HELDOUT R@5 / MRR with the reranker went .767/.800 -> .833/.867 (without: .867/.722 -> .933/.817), wasted top-5 slots ~31% -> 0, deleted-content leaks 1 -> 0; recall latency ~74 -> ~105 ms. Applying recency to facts by `created_at` (12 h half-life) was tried and dropped: freshly indexed chunks outranked older facts. Headless chunks (the chunker strips headings) ranked low with embedder and reranker alike, hence the "title › heading" prefix.

## About you and reply style

Asking was not enough: with the plain-text rule in the system prompt and the turn note, the 9B model still wrote headings and tables 4/4 times, so `run_autonomous` (the bots' path) converts plain-text replies with `utils/plain_text.markdown_to_plain`. From the system prompt alone, "short" replies were ~200 words; with `reply_style_note` repeated next to the question the 9B model gives about a third of its normal length, not the 80 words asked. Ported from tuatha's Profile page (without its accent colour, identity block and memory half-life; Pantheon's `context_focus` covers decay).

## Projects

### Delete and export cover every store (2026-10-02)

`DELETE /projects/{id}` used to remove only `data/projects/<id>` and the projects.json entry; conversations, notes, graph, artifacts, jobs, schedules and both Chroma collections stayed. A harness that created and deleted 133 projects left 438 conversations and ~280 MB of vectors. Importing a copy next to its source silently dropped its conversations, notes and graph while reporting them imported, because their row ids collided. Hence `api/project_purge.py` and id replacement on import.

## Deliverables and builds

### Builds (iOS bake-off, 2026-10-18)

Models wrote whole Swift apps with no way to compile them (Opus's first command was `which swift`) and shipped type errors the review round had to find. Hence `run_build` on a throwaway Kubernetes pod; see [builds.md](builds.md).

### Pushing artifacts to GitHub

`github(action="write_files")` makes the model resend every file in its own output (slow, and it hits the output cap), so `push_files` reads the files from the artifact store and commits them server-side through the Git Data API.

### Deliverables are artifacts (2026-10-19)

Asked for an iOS marble-maze game "easily imported into Xcode" as a background task, the local model planned every Swift file with `write_file`; the approved plan ("follow it step by step") locked that in, a retry repeated it, and nothing could be downloaded, built or pushed. Prompt and plan wording alone left the code in the workspace in every A/B run, hence the `write_file` mirror. Gemma planned "invoke the `/ios-app` skill" and the task then tried to `create_skill` one, hence attaching a `/slug` named in the plan. The marble reply had four ledger copies pasted in, hence the "only saved" echo skipping ledgers, `write_file` and source files. Harness `deliverables/` A/Bs the original request: main 0/4 runs with the code in artifacts, the change 4/4 (Gemma 4 26B, 10-min cap). Still weak: the model attached `ios-app` 1/4 and wrote its `project.yml` 0/4 when not asked for `/ios-app` explicitly. The skill's XcodeGen template was checked with xcodegen + xcodebuild.

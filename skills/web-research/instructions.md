# Web Research

Research a topic using the best lookup tools you have and produce a clear, structured summary with sources.

## Procedure

1. **Clarify the question.** If the user's request is vague, identify the most useful interpretation. If multiple angles exist, note them and pick the most likely one unless the user specified otherwise.

2. **Pick the search tool.** Use a connected search service when one exists — an `mcp_*` tool whose description mentions search, news or the web (Tavily, Brave, Exa and the like); it usually returns cleaner, fresher extracts. Otherwise use the built-in `web_search`. Use the same tool throughout; don't run the same query through both.

3. **Search broadly first.** Run 2-3 searches with varied phrasing to avoid blind spots: the core question, then a counterpoint or alternative angle. Put the current month and year in queries about anything that changes (prices, versions, office-holders, events).

4. **Read, don't skim.** Search snippets are not enough for anything you will state as fact. Open the 1-3 most promising pages with `web_fetch` (or the search service's own page-reading tool) and take the facts from the page itself. Prefer primary sources (official docs, research papers, authoritative organizations) over secondary ones (blog posts, forums). Note when a source may be biased or outdated.

5. **Synthesize findings.** Don't just list what each source says — connect the information into a coherent answer. Call out where sources agree and where they conflict.

6. **Structure the response:**
   - **Answer** — Lead with the direct answer to the user's question (2-3 sentences).
   - **Key Findings** — The important details, organized by theme rather than by source.
   - **Sources** — List the sources used with URLs so the user can verify; only pages you actually read or whose snippet carried the fact.
   - **Caveats** — Note any limitations: conflicting information, gaps in available data, or areas where the user should dig deeper.

## Depth Levels

- **quick** — 1-2 searches, concise answer, minimal sourcing. Good for factual lookups.
- **standard** (default) — 2-3 searches plus 1-2 page reads, structured summary with sources.
- **thorough** — 4-6 searches across diverse sources plus page reads, includes counterarguments and a confidence assessment.

## Important

- Always include source URLs. Unsourced claims erode trust; never invent a URL.
- If the information is time-sensitive, note the date of the sources and say "as of <date>".
- If you can't find reliable information, say so rather than speculating.
- If the user wants to keep the result, save it with `save_to_artifact` (e.g. `research/<topic>.md`); saved artifacts are indexed for recall. To keep a single source itself, use `ingest_source`. Never `write_file` for a deliverable — workspace files aren't kept or searchable.
- For a long list of items (every state, every vendor, each product in a category), propose a `research_batch` task with `create_task` instead of researching them all in one reply; it waits for the user's approval in the Tasks tab.

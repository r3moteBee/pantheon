# Web Research

Research a topic using web search and produce a clear, structured summary.

## Procedure

1. **Clarify the question.** If the user's request is vague, identify the most useful interpretation. If multiple angles exist, note them and pick the most likely one unless the user specified otherwise.

2. **Search broadly first.** Run 2-3 web searches with varied phrasing to avoid blind spots. Start with the core question, then search for counterpoints or alternative perspectives.

3. **Evaluate sources.** Prefer primary sources (official docs, research papers, authoritative organizations) over secondary ones (blog posts, forums). Note when a source may be biased or outdated.

4. **Synthesize findings.** Don't just list what each source says — connect the information into a coherent answer. Call out where sources agree and where they conflict.

5. **Structure the response:**
   - **Answer** — Lead with the direct answer to the user's question (2-3 sentences).
   - **Key Findings** — The important details, organized by theme rather than by source.
   - **Sources** — List the sources used with URLs so the user can verify.
   - **Caveats** — Note any limitations: conflicting information, gaps in available data, or areas where the user should dig deeper.

## Depth Levels

- **quick** — 1-2 searches, concise answer, minimal sourcing. Good for factual lookups.
- **standard** (default) — 2-3 searches, structured summary with sources.
- **thorough** — 4-6 searches across diverse sources, includes counterarguments and confidence assessment.

## Important

- Always include source URLs. Unsourced claims erode trust.
- If the information is time-sensitive, note the date of the sources.
- If you can't find reliable information, say so rather than speculating.
- If the user wants to keep the result, save it with `save_to_artifact` (e.g. `research/<topic>.md`); saved artifacts are indexed for recall. To keep a single source itself, use `ingest_source`.
- For a long list of items (every state, every vendor), propose a `research_batch` task with `create_task` instead of researching them all in one reply.

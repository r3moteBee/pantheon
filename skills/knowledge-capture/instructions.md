# Knowledge Capture

Extract key facts, decisions, and learnings from the conversation and store them in memory for future recall.

## Procedure

1. **Determine scope.** If the user highlighted something specific ("remember this: ..."), capture exactly that. If they asked for a full capture, scan the whole conversation; use `recall` (tier `episodic`) to bring back earlier turns that are no longer in view.

2. **Check what memory already holds.** Before writing anything, run `recall` with the topic words. Skip entries memory already has; if an existing entry is now wrong or outdated, store the corrected fact and say the old one was superseded.

3. **Extract knowledge types:**

   **Facts** — Concrete information: configurations, URLs, account names, technical specifications, names, dates.
   → `remember` in **semantic** memory with a clear, searchable description.

   **Decisions** — Choices that were made and their rationale: "We chose PostgreSQL over MongoDB because of the relational nature of the data."
   → `remember` in **semantic** memory, with the reasoning and the context the decision belongs to.

   **Relationships** — Connections between concepts: "Service A depends on Service B," "Alice is the owner of the billing module."
   → `create_graph_node` for each entity that doesn't exist yet and `link_concepts` for the edge, with a short relationship label.

   **Learnings** — Things discovered during the conversation: "The API rate limits to 100 req/min," "The CSV parser chokes on UTF-16 files."
   → `remember` in **semantic** memory with enough context to be useful later.

4. **Write clean memory entries.** Each entry should be:
   - Self-contained — understandable without the original conversation
   - Searchable — use clear, descriptive language someone would search for, including the names of the things involved
   - Dated — include when the knowledge was captured (today's date is in the turn context)
   - Attributed — note the source or context ("decided in chat on 2026-10-25", "from the vendor's docs")

5. **Confirm what was captured.** Tell the user what you stored and where:
   - "Stored 3 facts in semantic memory and 2 relationships in graph memory."
   - List each entry briefly so the user can verify; mention anything you skipped as already known.

## Important

- Don't store trivial or ephemeral information (greetings, clarification questions, the weather).
- When storing decisions, always include the reasoning — the "why" is more valuable than the "what" over time.
- For sensitive information (credentials, personal data), warn the user before storing and confirm they want it in memory; never store secrets, keys or passwords at all — the vault is for those, not memory.
- Memory is per project: store project-specific knowledge while that project is active. Something that applies everywhere (a preference, a fact about the user) belongs in the default project.
- A longer document (notes, a spec, a transcript) is an artifact, not a memory: save it with `save_to_artifact` and store only the one-line fact that it exists and what it covers.

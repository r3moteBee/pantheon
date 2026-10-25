# Summarize Conversation

Distill a conversation into a clear summary with key points, decisions, and action items.

## Procedure

1. **Gather context.** Review the current conversation. If the conversation is long, use `recall` (tier `episodic`) to bring back earlier turns that are no longer in view. If the user means a different conversation ("summarize yesterday's chat about X"), `recall` with its topic; say which conversation you found.

2. **Identify the important parts:**
   - Decisions that were made
   - Problems that were identified
   - Solutions that were proposed or implemented
   - Action items — things that still need to be done
   - Open questions that weren't resolved
   - Key facts or information that was shared

3. **Structure by format:**

   **brief** — 3-5 bullet points covering the most important outcomes. Skip details.

   **detailed** (default):
   - **Summary** — 2-3 sentences on what the conversation was about.
   - **Key Decisions** — What was decided and why.
   - **Work Completed** — What was actually done during the conversation (artifacts saved, code written, files changed, tasks created), with their names.
   - **Action Items** — What still needs to happen, with owners if mentioned. Separate what the user must do from what Pantheon can do next.
   - **Open Questions** — Unresolved topics that need follow-up.

   **action-items** — Just the action items, formatted as a checklist.

4. **Keep it if asked.** Nothing is stored automatically. If the user wants the summary kept, save it with `save_to_artifact` (e.g. `notes/<date>-<topic>.md`) or `save_last_response`; saved artifacts are indexed, so "what did we discuss last time?" finds them. `consolidate_memory` distills the session into semantic and graph memory instead. To send it to Telegram, `send_telegram` only when they ask.

## Important

- Focus on outcomes and decisions, not the back-and-forth of how you got there.
- If the conversation involved code or documents, reference the specific files or artifacts and what changed.
- Use the project context to frame the summary in terms the user cares about.
- Keep it concise — a summary longer than the conversation isn't a summary.
- Don't summarize tool chatter (searches run, pages fetched) unless the result mattered; summarize what was learned.

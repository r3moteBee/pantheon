# Daily Digest

Generate a summary of recent project activity from memory, artifacts and background jobs.

## Procedure

1. **Determine the time period.** Default is "today." Parse the user's request for a specific period (yesterday, this week, since Monday). Today's date is in the turn context; work the period out from it, in the user's time zone.

2. **Gather data from multiple sources:**

   **Conversations** — Use `recall` (tiers `episodic` and `semantic`) for the active project's recent conversations and notes. Look for: topics discussed, decisions made, tasks completed, problems encountered.

   **Artifacts** — Use `list_artifacts` to find artifacts created or updated during the period (saved answers, ingested sources, code, reports). Ignore `digests/` itself.

   **Background jobs** — Use `list_recent_jobs` for tasks that ran, failed or are still running; `get_job_status` for one that needs detail.

   **Workspace** — Only if the project uses it for scratch work or a repo checkout: `list_workspace_files`, or `git_status` for a bound repo.

3. **Synthesize the digest:**

   - **Overview** — 1-2 sentences: what was the main focus of work during this period?
   - **Conversations** — Brief summary of each session (what was discussed, what was accomplished).
   - **Changes Made** — Artifacts and files created or updated, and jobs that ran. Group by area of the project.
   - **Decisions & Learnings** — Key decisions captured, new knowledge stored.
   - **Open Items** — Tasks that were started but not finished, failed jobs, questions that remain unanswered.

4. **Save the digest as an artifact** with `save_to_artifact`, `digests/<YYYY-MM-DD>.md` (one per period; pass `overwrite: true` when re-running the same period). Saved artifacts are indexed, so the digest can be recalled later ("what did we do last week?"). Don't write it to the workspace with `write_file`: workspace files aren't kept or searchable.

5. **If running as a scheduled task:** also send a short version with `send_telegram` when the user asked for it and Telegram is set up. Keep it brief — scheduled digests should be scannable in 30 seconds. A scheduled run has no one to ask: if the period had no activity, save a one-line digest saying so and stop.

## Important

- If there's no activity for the period, say so briefly. Don't manufacture content.
- For scheduled runs, keep it tight — no one wants a daily wall of text.
- Group related activities together rather than listing them chronologically.
- Reference specific artifacts and conversation topics by name so the user can dive deeper if needed.
- Only report what the tools returned. A job you did not see in `list_recent_jobs` did not run.

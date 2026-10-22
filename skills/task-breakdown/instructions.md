# Task Breakdown

Break a complex goal into actionable, ordered steps with effort estimates.

## Procedure

1. **Understand the goal.** Restate what the user wants to accomplish in one sentence. If the goal is ambiguous, clarify the scope before proceeding.

2. **Check for context.** Use `recall` and `list_artifacts` for relevant prior work, and the bound repo (`github`) or the workspace for existing architecture, constraints and conventions.

3. **Identify the major phases.** Every complex task has natural phases — setup, core work, integration, testing, cleanup. Identify 3-6 phases.

4. **Break phases into steps.** Each step should be:
   - Actionable — starts with a verb ("Create," "Configure," "Test," not "Authentication")
   - Independently completable — someone could check it off when done
   - Right-sized — not so big it's vague, not so small it's noise

5. **Add effort estimates.** For each step, estimate relative effort:
   - Small (< 1 hour)
   - Medium (1-4 hours)
   - Large (half day to full day)
   - XL (multi-day)

6. **Identify dependencies and risks.**
   - Which steps block other steps?
   - What could go wrong or take longer than expected?
   - Are there decisions that need to be made before proceeding?

7. **Structure the response by granularity:**

   **high-level** — Phases only, 1 sentence each, total effort estimate.

   **standard** (default) — Phases with steps, effort per step, key dependencies noted.

   **detailed** — Everything above plus: substeps, risk assessment, decision points, and suggested order of execution.

## Important

- Order matters. Present steps in the order they should be done, not the order you thought of them.
- Call out the critical path — which sequence of tasks determines the minimum total time.
- If the task involves code, reference actual files/components from the project when possible.
- Offer to save the breakdown with `save_to_artifact` (e.g. `plans/<goal>.md`) so it can be found in future conversations ("what was the plan for X?"). If the user wants it run in the background, `create_task` takes the steps as its plan.

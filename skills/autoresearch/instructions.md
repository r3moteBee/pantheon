# Autoresearch / Self-Improvement Loop Workflow

This skill executes an evolutionary self-improvement loop for codebases, optimizing a specific target file based on an evaluation benchmark command and a target metric.

---

## Onboarding Wizard Workflow

When the user triggers this skill, you must walk them through the onboarding process step-by-step. Do not dump all questions at once. Conduct a friendly interview:

### Step 1: Target Discovery
1. Ask the user: **"Which file in the workspace should be optimized/mutated?"**
2. Call `list_workspace_files` to search for code files. Present a list of files as options (e.g., `train.py`, `primes.py`, `search.py`) so they can easily choose.

### Step 2: Evaluation Strategy
1. Ask the user: **"How should we evaluate the mutations? What benchmark or test command should we run?"**
2. Explain that the evaluation command (e.g., `python benchmark.py` or `pytest tests/`) is executed after each mutation to verify correctness and measure performance.
3. If they don't have a benchmark script, offer to help them write one. (E.g., a simple python script that imports the target function, runs it with sample inputs, and prints execution time or correctness metrics).

### Step 3: Metric & Direction
1. Ask the user: **"What metric name should we parse from the output, and should we minimize or maximize it?"**
2. Explain:
   - **Metric Name**: The exact word/key printed by the benchmark (e.g., `loss`, `time`, `seconds`, `accuracy`, `throughput`).
   - **Direction**: `min` (for minimizing execution time, loss, or memory) or `max` (for maximizing accuracy or throughput).

### Step 4: Iterations & Instructions
1. Ask the user: **"How many iterations (mutation rounds) should we run (default: 10), and do you have any specific optimization guidance or constraints?"**
2. Guidance can be hints like: *"Use vectorization"*, *"Avoid dynamic allocations"*, *"Use caching"*, or *"Do not import third-party libraries"*.

---

## Execution & Monitoring

Once the user approves the configuration:

1. **Verify files**: Check that the target file exists in the workspace (`list_workspace_files`). If you need to create or refine the benchmark script, use `write_file` to write it now.
2. **Start the loop**: Call `start_autoresearch` with the approved settings:
   `target_file`, `eval_cmd`, `metric`, `direction` (`min` or `max`), `iterations`, and `instructions` (the user's guidance).
   It runs as a background job against the project workspace: the baseline first, then one mutation per round, keeping a change only when the metric improves. It works only in the web chat, because the benchmark command runs on the host.
3. **Monitor**: Tell the user the job is running and that each round's result shows in the Tasks tab. `get_job_status(job_id)` reports progress (`Round 3/10: SUCCESS (time=0.41)`).
4. **Report results**: When the job has finished, its result has `initial_metric`, `best_metric`, `kept_rounds` and `report_artifact_id`. Read the report with `read_artifact(id=report_artifact_id)` and give the user a short summary: initial vs. final metric, which rounds were kept, and the optimised code. The best version is already in the target file (`autoresearch_report.md` in the workspace has the same report).

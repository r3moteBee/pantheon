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

1. **Verify files**: Check that the target file exists. If you need to create or refine the benchmark script, use `write_file` to write it now.
2. **Launch the runner**: Use the `code_execute` tool to run the background evolutionary script. Build the command exactly as follows:
   ```bash
   python utils/autoresearch.py --target <target_path> --eval "<eval_cmd>" --metric "<metric_name>" --direction <min_or_max> --iterations <count> --instructions "<custom_guidance>"
   ```
   *Note: If the script is run in the backend, the current working directory is `backend/` and python resolves dependencies correctly.*
3. **Monitor logs**: Explain to the user that the evolutionary loop is executing. Output the baseline performance and keep them updated on progress.
4. **Report results**:
   - Once execution finishes, read `autoresearch_report.md` from the workspace using `read_file`.
   - Save the report as a project artifact using `save_to_artifact` (path: `autoresearch_report.md`, title: `Evolutionary Optimization Report`).
   - Output a clean summary of the initial vs. final metric, the successful mutations, and the optimized code block to the user.

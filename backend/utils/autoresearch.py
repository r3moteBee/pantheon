#!/usr/bin/env python3
"""Autoresearcher Evolutionary Self-Improvement Loop Runner.

Iteratively mutates, executes, evaluates, and refines a target file to optimize
a specified metric based on Andrej Karpathy's autoresearch loop.
"""
from __future__ import annotations
import argparse
import asyncio
import difflib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# Ensure backend directory is in sys.path for local imports
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))


from config import get_settings
from models.provider import get_provider

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("autoresearch")


def extract_code_block(text: str, lang: str) -> str:
    """Extract code block contents from markdown text."""
    # Match ``` followed by optional word characters then a newline, capture everything until ```
    pattern = r"```(?:[a-zA-Z0-9_\-\+]+)?\s*\n(.*?)\s*```"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1)
    # Try finding any code block: ``` ... ```
    match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    return text



def parse_metric(output: str, metric_name: str) -> float | None:
    """Parse the target metric from execution stdout/stderr.
    
    Searches for printouts of the metric and returns the last printed value.
    """
    patterns = [
        rf"(?:[a-zA-Z0-9_\-\s]*{re.escape(metric_name)}[a-zA-Z0-9_\-\s]*)\s*[:=\->\s]\s*([+\-]?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?)",
        rf"(?:[a-zA-Z0-9_\-\s]*{re.escape(metric_name)}[a-zA-Z0-9_\-\s]*)\s+is\s+([+\-]?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?)",
    ]
    
    matches = []
    for pattern in patterns:
        for m in re.finditer(pattern, output, re.IGNORECASE):
            try:
                matches.append(float(m.group(1)))
            except ValueError:
                pass
    if matches:
        return matches[-1]
    return None


class AutoresearchRunner:
    def __init__(
        self,
        target_path: Path,
        eval_cmd: str,
        metric_name: str,
        direction: str,
        max_iterations: int,
        instructions: str,
        workspace_dir: Path,
    ):
        self.target_path = target_path
        self.eval_cmd = eval_cmd
        self.metric_name = metric_name
        self.direction = direction.lower()
        self.max_iterations = max_iterations
        self.instructions = instructions
        self.workspace_dir = workspace_dir
        
        self.best_code = ""
        self.best_metric = float("inf") if self.direction == "min" else float("-inf")
        self.initial_metric = None
        self.history: list[dict[str, Any]] = []

    async def run_eval(self) -> tuple[bool, str, float | None]:
        """Run the evaluation command in a subprocess and parse output."""
        logger.info(f"Running evaluation command: {self.eval_cmd}")
        try:
            # Run the command with a 5 minute timeout
            proc = await asyncio.create_subprocess_shell(
                self.eval_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workspace_dir)
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=300.0)
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            exit_code = proc.returncode
            
            combined_output = stdout + "\n" + stderr
            metric_val = parse_metric(combined_output, self.metric_name)
            
            success = (exit_code == 0)
            return success, combined_output, metric_val
        except asyncio.TimeoutError:
            logger.warning("Evaluation timed out after 300 seconds.")
            return False, "TIMEOUT ERROR", None
        except Exception as e:
            logger.error(f"Failed to execute evaluation command: {e}")
            return False, str(e), None

    def is_better(self, val: float) -> bool:
        """Check if the new metric value is better than the current best."""
        if self.direction == "min":
            return val < self.best_metric
        else:
            return val > self.best_metric

    async def get_mutation(self, current_code: str, lang: str) -> str:
        """Query the LLM to get the next code mutation."""
        provider = get_provider()
        
        system_prompt = (
            f"You are a principal software engineer and expert researcher.\n"
            f"Your job is to optimize the target file to improve the '{self.metric_name}' metric.\n"
            f"You must return ONLY the complete updated file content inside a ```{lang} ... ``` code block. "
            f"Do not write any introductory or concluding text, explanations, or multiple options. "
            f"Return only the drop-in replacement file content."
        )
        
        # Build trial history summary
        history_summary = []
        for h in self.history[-8:]:  # Include last 8 trials to keep context window small
            status_icon = "✅" if h["status"] == "SUCCESS" else "❌"
            history_summary.append(
                f"Trial {h['iteration']} ({h['status']}):\n"
                f"- Metric: {h['metric']}\n"
                f"- Diffs made:\n{h['diff']}\n"
                f"- Output/Error log: {h['notes']}"
            )
        history_str = "\n\n".join(history_summary) if history_summary else "No trials run yet."

        user_content = (
            f"We are optimizing target file: {self.target_path.name}\n\n"
            f"Research Goals / Optimization Instructions:\n{self.instructions}\n\n"
            f"Metric to optimize: '{self.metric_name}' (Direction: {self.direction})\n"
            f"Current best metric: {self.best_metric}\n\n"
            f"Here is the current code of the target file:\n"
            f"```{lang}\n{current_code}\n```\n\n"
            f"Here is the history of previous mutation attempts:\n"
            f"{history_str}\n\n"
            f"Please generate the next code variant. Modify the code to improve the metric. "
            f"Be creative, optimize algorithms, caching, parallelism, data structures, or allocations based on the instructions. "
            f"Return the entire modified file in a single ```{lang} ... ``` code block."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

        logger.info("Requesting mutation from LLM...")
        response = await provider.chat_complete(messages=messages)
        content = response.get("content", "")
        return extract_code_block(content, lang)

    async def execute_loop(self) -> None:
        """Execute the self-improvement loop."""
        lang = self.target_path.suffix.lstrip(".") or "txt"
        
        # 1. Establish baseline
        logger.info("Establishing baseline performance...")
        self.best_code = self.target_path.read_text(encoding="utf-8")
        success, output, val = await self.run_eval()
        
        if not success or val is None:
            logger.warning(
                f"Baseline execution failed or could not parse metric '{self.metric_name}' from output.\n"
                f"Baseline output snippet:\n{output[:500]}\n"
                f"Proceeding with metric value: {self.best_metric}"
            )
            # If baseline has no metric, we'll try to find a valid metric in subsequent runs
        else:
            self.best_metric = val
            self.initial_metric = val
            logger.info(f"Baseline established! Metric '{self.metric_name}': {self.best_metric}")

        # Backup file path
        backup_path = self.target_path.with_suffix(self.target_path.suffix + ".bak")
        
        # 2. Loop iterations
        for iteration in range(1, self.max_iterations + 1):
            logger.info(f"\n=== ITERATION {iteration}/{self.max_iterations} ===")
            
            # Save backup
            shutil.copy2(self.target_path, backup_path)
            
            # Read current active code
            current_code = self.target_path.read_text(encoding="utf-8")
            
            # Generate mutation
            try:
                mutated_code = await self.get_mutation(current_code, lang)
                if not mutated_code.strip():
                    raise ValueError("LLM returned empty mutated code")
            except Exception as e:
                logger.error(f"Failed to generate mutation: {e}")
                continue
            
            # Overwrite file with mutated code
            self.target_path.write_text(mutated_code, encoding="utf-8")
            
            # Calculate diff for logging
            diff_lines = list(difflib.unified_diff(
                current_code.splitlines(),
                mutated_code.splitlines(),
                fromfile="before",
                tofile="after",
                n=2
            ))
            diff_str = "\n".join(diff_lines[:15])  # Keep first 15 lines of diff
            if len(diff_lines) > 15:
                diff_str += f"\n... ({len(diff_lines) - 15} lines truncated)"
            
            # Execute evaluation
            eval_success, eval_output, eval_val = await self.run_eval()
            
            # Assess mutation
            keep = False
            status = "FAILURE"
            notes = ""
            
            if not eval_success:
                status = "FAILURE"
                notes = f"Execution failed.\n{eval_output[:300]}"
                logger.info(f"Iteration {iteration}: FAILED (Execution Error)")
            elif eval_val is None:
                status = "FAILURE"
                notes = f"Could not parse metric '{self.metric_name}' from output.\n{eval_output[:300]}"
                logger.info(f"Iteration {iteration}: FAILED (Metric Unparsed)")
            else:
                better = self.is_better(eval_val)
                if better:
                    keep = True
                    status = "SUCCESS"
                    notes = f"Improved metric to {eval_val}."
                    logger.info(f"Iteration {iteration}: SUCCESS! Metric '{self.metric_name}': {self.best_metric} -> {eval_val}")
                    self.best_metric = eval_val
                    self.best_code = mutated_code
                    # Try git commit if git is setup
                    self.git_commit(iteration, eval_val)
                else:
                    status = "REGRESSION"
                    notes = f"Regression. Metric: {eval_val} (Best: {self.best_metric})."
                    logger.info(f"Iteration {iteration}: REGRESSION. Metric '{self.metric_name}': {eval_val} is worse than best {self.best_metric}")
            
            self.history.append({
                "iteration": iteration,
                "metric": eval_val,
                "status": status,
                "diff": diff_str,
                "notes": notes
            })
            
            if not keep:
                # Revert to backup
                shutil.copy2(backup_path, self.target_path)
                logger.info("Reverted changes to best version.")
            else:
                # Clean up backup since this is the new baseline
                if backup_path.exists():
                    backup_path.unlink()

        # Clean up safety backup if it still exists
        if backup_path.exists():
            backup_path.unlink()
            
        # Write report
        self.generate_report()

    def git_commit(self, iteration: int, metric: float) -> None:
        """Optional git commit helper for successful iterations."""
        if not (self.workspace_dir / ".git").is_dir():
            return
        try:
            subprocess.run(["git", "add", str(self.target_path)], cwd=str(self.workspace_dir), check=True)
            subprocess.run(
                ["git", "commit", "-m", f"Autoresearch iteration {iteration} - metric: {metric}"],
                cwd=str(self.workspace_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            logger.info("Changes committed to Git repository.")
        except Exception:
            pass

    def generate_report(self) -> None:
        """Generate a summary report of the evolutionary run."""
        report_path = self.workspace_dir / "autoresearch_report.md"
        
        initial_metric = str(self.initial_metric) if self.initial_metric is not None else "None"
                
        # Calculate diff from initial to best
        initial_code = ""
        # Let's try to restore initial code from git if available, or use the first trial's target
        # For simplicity, we compare final best_code with target file's starting state
        try:
            # Reconstruct starting code: if the first trial was a success, starting code was current_code,
            # otherwise it is what is currently in target_path (since we revert on failure).
            # We can also just read the backup/initial state if we kept it.
            pass
        except Exception:
            pass
            
        history_rows = []
        for h in self.history:
            metric_str = f"{h['metric']:.4f}" if h["metric"] is not None else "N/A"
            status_badge = f"`{h['status']}`"
            # Format diff code snippet
            diff_block = f"```diff\n{h['diff']}\n```"
            history_rows.append(
                f"### Iteration {h['iteration']} - {status_badge}\n"
                f"- **Metric**: {metric_str}\n"
                f"- **Result**: {h['notes']}\n"
                f"- **Mutation Diff Snippet**:\n{diff_block}\n"
            )
            
        history_summary_str = "\n".join(history_rows)
        
        report_content = f"""# Autoresearch Optimization Report

## Summary
- **Target File**: `{self.target_path.relative_to(self.workspace_dir) if self.target_path.is_relative_to(self.workspace_dir) else self.target_path.name}`
- **Optimization Metric**: `{self.metric_name}` (Direction: `{self.direction}`)
- **Initial Baseline Metric**: `{initial_metric}`
- **Final Best Metric**: `{self.best_metric}`
- **Total Iterations**: {len(self.history)}
- **Successful Mutations**: {sum(1 for h in self.history if h['status'] == 'SUCCESS')}

## Evolutionary Run Log

{history_summary_str}

## Final Optimized Code
Here is the final version of the code that achieved the best performance:

```{self.target_path.suffix.lstrip(".")}
{self.best_code}
```
"""
        report_path.write_text(report_content, encoding="utf-8")
        logger.info(f"Autoresearch report successfully written to: {report_path}")


def main():
    parser = argparse.ArgumentParser(description="Autoresearch Self-Improvement Loop")
    parser.add_argument("--target", required=True, help="Relative or absolute path to target file to optimize")
    parser.add_argument("--eval", required=True, help="Shell command to execute evaluation")
    parser.add_argument("--metric", required=True, help="Metric name to extract from evaluation logs")
    parser.add_argument("--direction", choices=["min", "max"], default="min", help="min (default) or max")
    parser.add_argument("--iterations", type=int, default=10, help="Number of loops (default 10)")
    parser.add_argument("--instructions", default="Optimize performance, correctness, and clean code.", help="Instruction guide")
    parser.add_argument("--workspace-dir", help="Optional workspace base dir")
    
    args = parser.parse_args()
    
    # Resolve directories
    settings = get_settings()
    workspace_dir = Path(args.workspace_dir or settings.workspace_dir).resolve()
    
    target_path = Path(args.target)
    if not target_path.is_absolute():
        target_path = (workspace_dir / target_path).resolve()
        
    if not target_path.exists():
        logger.error(f"Target file does not exist: {target_path}")
        sys.exit(1)
        
    runner = AutoresearchRunner(
        target_path=target_path,
        eval_cmd=args.eval,
        metric_name=args.metric,
        direction=args.direction,
        max_iterations=args.iterations,
        instructions=args.instructions,
        workspace_dir=workspace_dir,
    )
    
    asyncio.run(runner.execute_loop())


if __name__ == "__main__":
    main()

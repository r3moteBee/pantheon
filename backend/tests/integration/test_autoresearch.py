import os
import tempfile
# Initialize isolated DATA_DIR for tests before importing config-dependent modules
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

import pytest
import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from utils.autoresearch import parse_metric, extract_code_block, AutoresearchRunner

def test_parse_metric():
    # Test colons
    assert parse_metric("loss: 0.1234", "loss") == 0.1234
    assert parse_metric("Loss: 0.1234", "loss") == 0.1234
    assert parse_metric("Training loss = 0.5678", "loss") == 0.5678
    assert parse_metric("accuracy -> 98.5", "accuracy") == 98.5
    assert parse_metric("throughput is 1500", "throughput") == 1500.0
    
    # Test last value is returned
    output = "epoch 1: loss = 0.9\nepoch 2: loss = 0.4\nepoch 3: loss = 0.2"
    assert parse_metric(output, "loss") == 0.2
    
    # Test no match
    assert parse_metric("some random output", "loss") is None


def test_extract_code_block():
    text_with_python = "Here is the code:\n```python\ndef foo():\n    return 42\n```\nHope that helps!"
    assert extract_code_block(text_with_python, "python").strip() == "def foo():\n    return 42"
    
    text_no_lang = "```\nprint('hello')\n```"
    assert extract_code_block(text_no_lang, "python").strip() == "print('hello')"
    
    text_raw = "print('hello')"
    assert extract_code_block(text_raw, "python").strip() == "print('hello')"


@pytest.mark.asyncio
@patch("utils.autoresearch.get_provider")
async def test_autoresearch_runner_flow(mock_get_provider):
    # Mock LLM provider response
    mock_provider = MagicMock()
    mock_provider.chat_complete = AsyncMock()
    # Mock LLM returns different variations
    mock_provider.chat_complete.side_effect = [
        {"content": "```python\n# Mutation 1\ndef work():\n    return 'mutated1'\n```"},
        {"content": "```python\n# Mutation 2\ndef work():\n    return 'mutated2'\n```"}
    ]
    mock_get_provider.return_value = mock_provider

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        target_file = tmp_path / "target.py"
        target_file.write_text("def work():\n    return 'original'\n", encoding="utf-8")
        
        runner = AutoresearchRunner(
            target_path=target_file,
            eval_cmd="python eval_script.py",
            metric_name="score",
            direction="max",  # We want to maximize score
            max_iterations=2,
            instructions="Maximize score",
            workspace_dir=tmp_path
        )
        
        # Mock run_eval to return different metrics for baseline and iterations
        # Baseline score: 5.0
        # Iteration 1 score: 7.0 (better, success)
        # Iteration 2 score: 6.0 (worse, regression)
        baseline_run = (True, "score: 5.0", 5.0)
        iter1_run = (True, "score: 7.0", 7.0)
        iter2_run = (True, "score: 6.0", 6.0)
        
        with patch.object(runner, "run_eval", AsyncMock()) as mock_run_eval:
            mock_run_eval.side_effect = [
                baseline_run,  # Baseline check
                iter1_run,     # Iteration 1 eval
                iter2_run,     # Iteration 2 eval
            ]
            
            await runner.execute_loop()
            
            # Verify runner state
            assert runner.best_metric == 7.0
            assert "# Mutation 1" in runner.best_code
            assert "# Mutation 2" not in runner.best_code
            
            # Verify file content is indeed the best code
            assert target_file.read_text(encoding="utf-8").strip() == "# Mutation 1\ndef work():\n    return 'mutated1'"
            
            # Verify report was generated
            report_file = tmp_path / "autoresearch_report.md"
            assert report_file.exists()
            report_text = report_file.read_text(encoding="utf-8")
            assert "Initial Baseline Metric**: `5.0`" in report_text
            assert "Final Best Metric**: `7.0`" in report_text
            assert "Iteration 1 - `SUCCESS`" in report_text

            assert "Iteration 2 - `REGRESSION`" in report_text

"""Integration tests for the new agent financial analysis tools.

Run: pytest backend/tests/integration/test_financial_tools.py -v
"""
from __future__ import annotations

import os
os.environ["DATA_DIR"] = "/tmp/pantheon-tests-data"
os.makedirs("/tmp/pantheon-tests-data/db", exist_ok=True)

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from agent.tools import execute_tool


@pytest.mark.asyncio
@patch("httpx.AsyncClient.get")
async def test_analyze_company_financials_common_size(mock_get):
    """Test analyze_company_financials resolves CIK, fetches facts, and outputs common size report."""
    # Mock SEC mapping response
    mock_tickers = MagicMock()
    mock_tickers.status_code = 200
    mock_tickers.json.return_value = {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}
    }
    
    # Mock SEC facts response
    mock_facts = MagicMock()
    mock_facts.status_code = 200
    mock_facts.json.return_value = {
        "cik": 320193,
        "entityName": "Apple Inc.",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {"fy": 2023, "form": "10-K", "fp": "FY", "val": 300000000}
                        ]
                    }
                },
                "GrossProfit": {
                    "units": {
                        "USD": [
                            {"fy": 2023, "form": "10-K", "fp": "FY", "val": 120000000}
                        ]
                    }
                },
                "Assets": {
                    "units": {
                        "USD": [
                            {"fy": 2023, "form": "10-K", "fp": "FY", "val": 500000000}
                        ]
                    }
                },
                "Liabilities": {
                    "units": {
                        "USD": [
                            {"fy": 2023, "form": "10-K", "fp": "FY", "val": 200000000}
                        ]
                    }
                }
            }
        }
    }

    mock_get.side_effect = [mock_tickers, mock_facts]

    res = await execute_tool(
        tool_name="analyze_company_financials",
        tool_args={"ticker": "AAPL", "analysis_type": "common_size", "years": 1},
        memory_manager=MagicMock(),
        project_id="default"
    )

    assert "Common Size Analysis" in res
    assert "Apple Inc." in res
    assert "Gross Profit" in res
    # 120M / 300M = 40.00%
    assert "40.00%" in res
    # Liabilities / Assets = 200M / 500M = 40.00%
    assert "Total Liabilities" in res


@pytest.mark.asyncio
@patch("httpx.AsyncClient.get")
@patch("models.provider.get_provider")
async def test_compare_company_strategy_and_risks(mock_get_provider, mock_get):
    """Test compare_company_strategy_and_risks retrieves 10-K and summarizes comparison via LLM."""
    # Mock SEC mapping and submissions endpoints
    mock_tickers = MagicMock()
    mock_tickers.status_code = 200
    mock_tickers.json.return_value = {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}
    }

    mock_submissions = MagicMock()
    mock_submissions.status_code = 200
    mock_submissions.json.return_value = {
        "filings": {
            "recent": {
                "form": ["10-K"],
                "accessionNumber": ["0000320193-23-000106"],
                "filingDate": ["2023-11-03"],
                "primaryDocument": ["aapl-20230930.htm"]
            }
        }
    }

    mock_html = MagicMock()
    mock_html.status_code = 200
    mock_html.text = "<html><body>Apple strategy details...</body></html>"

    mock_get.side_effect = [mock_tickers, mock_submissions, mock_html]

    # Mock LLM provider client
    mock_prov = MagicMock()
    # async generator for provider.chat
    async def mock_chat(*args, **kwargs):
        yield "Comparative strategy and risk assessment report text from LLM."
    mock_prov.chat = mock_chat
    
    mock_get_provider.return_value = mock_prov

    res = await execute_tool(
        tool_name="compare_company_strategy_and_risks",
        tool_args={"tickers": ["AAPL"], "focus_areas": ["strategy"]},
        memory_manager=MagicMock(),
        project_id="default"
    )

    assert "Comparative strategy" in res


@pytest.mark.asyncio
@patch("models.provider.get_provider")
async def test_analyze_earnings_call(mock_get_provider, tmp_path):
    """Test analyze_earnings_call reads a workspace file and runs LLM extraction."""
    # Write a dummy transcript file in a temporary isolated directory
    transcript_file = tmp_path / "transcript.txt"
    transcript_file.write_text("AAPL Q3 Earnings Call transcript: Sales up 5%. AI chips coming.", encoding="utf-8")

    # Mock LLM provider
    mock_prov = MagicMock()
    async def mock_chat(*args, **kwargs):
        yield "Parsed Earnings Briefing: guidance, sales details."
    mock_prov.chat = mock_chat
    
    mock_get_provider.return_value = mock_prov

    with patch("agent.tools._get_workspace_base", return_value=tmp_path):
        res = await execute_tool(
            tool_name="analyze_earnings_call",
            tool_args={"file_path": "transcript.txt", "extract_guidance": True},
            memory_manager=MagicMock(),
            project_id="default"
        )
        assert "Parsed Earnings Briefing" in res

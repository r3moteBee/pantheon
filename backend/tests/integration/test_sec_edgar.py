"""Integration tests for the SEC/EDGAR source adapter.

Run: pytest backend/tests/integration/test_sec_edgar.py -v
"""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from sources.base import IngestRequest
from sources.adapters.sec_edgar import SecEdgarAdapter


@pytest.mark.asyncio
@patch("httpx.AsyncClient.get")
async def test_sec_edgar_fetch_direct_url(mock_get):
    """Verify direct SEC URL downloads HTML and returns FetchedContent."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "<html><head><title>Apple Inc. 10-K</title></head><body><style>body{}</style><p>Financial summary text</p></body></html>"
    mock_get.return_value = mock_resp

    adapter = SecEdgarAdapter()
    req = IngestRequest(
        source_type="sec/edgar",
        identifier="https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm",
        project_id="test-project"
    )
    
    fetched = await adapter.fetch(req)
    assert "Financial summary text" in fetched.text
    assert "Apple Inc. 10-K" in fetched.title
    mock_get.assert_called_once_with(
        "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm",
        headers={"User-Agent": "PantheonResearch/1.0 (contact@pantheon.local)"},
        timeout=60
    )


@pytest.mark.asyncio
@patch("httpx.AsyncClient.get")
async def test_sec_edgar_fetch_ticker_lookup(mock_get):
    """Verify ticker lookup resolves CIK, lists filings, and downloads the latest matching form."""
    # We mock 3 sequential GET requests:
    # 1. Company tickers mapping JSON
    mock_tickers = MagicMock()
    mock_tickers.status_code = 200
    mock_tickers.json.return_value = {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}
    }
    
    # 2. Company submissions filings JSON
    mock_submissions = MagicMock()
    mock_submissions.status_code = 200
    mock_submissions.json.return_value = {
        "filings": {
            "recent": {
                "form": ["10-Q", "10-K", "10-Q"],
                "accessionNumber": ["0000320193-24-000002", "0000320193-23-000106", "0000320193-23-000001"],
                "filingDate": ["2024-02-02", "2023-11-03", "2023-05-04"],
                "primaryDocument": ["aapl-20240202.htm", "aapl-20230930.htm", "aapl-20230504.htm"]
            }
        }
    }
    
    # 3. Direct document HTML body
    mock_html = MagicMock()
    mock_html.status_code = 200
    mock_html.text = "<html><body><h1>Annual Financial Statement</h1><p>Revenue: $383B</p></body></html>"

    # Set side_effect to return mock responses in sequence
    mock_get.side_effect = [mock_tickers, mock_submissions, mock_html]

    adapter = SecEdgarAdapter()
    req = IngestRequest(
        source_type="sec/edgar",
        identifier="AAPL/10-K",
        project_id="test-project"
    )
    
    fetched = await adapter.fetch(req)
    assert "Annual Financial Statement" in fetched.text
    assert "Revenue: $383B" in fetched.text
    assert fetched.published_at == "2023-11-03"
    assert fetched.author_or_publisher == "Apple Inc."
    assert "AAPL/10-K" in fetched.title or "Apple Inc." in fetched.title
    assert fetched.extra_meta["cik"] == "320193"
    assert fetched.extra_meta["form_type"] == "10-K"
    
    # Check that it constructed the correct direct archives URL
    expected_doc_url = "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm"
    assert fetched.url == expected_doc_url


def test_sec_edgar_render_path():
    """Verify artifact path formatting handles CIK and dates."""
    adapter = SecEdgarAdapter()
    req = IngestRequest(
        source_type="sec/edgar",
        identifier="AAPL/10-K",
        project_id="test-project"
    )
    
    from sources.base import FetchedContent
    fetched = FetchedContent(
        text="Content",
        title="Apple Inc. 10-K Filing",
        author_or_publisher="Apple Inc.",
        url="https://sec.gov/filing",
        published_at="2023-11-03",
        extra_meta={"cik": "320193", "form_type": "10-K"}
    )
    
    path = adapter.render_artifact_path(req, fetched)
    assert path == "sec/320193/2023-11-03/10-k-apple-inc-10-k-filing.md"

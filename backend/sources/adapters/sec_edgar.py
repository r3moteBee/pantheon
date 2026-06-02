"""SEC/EDGAR source adapter for ingesting company filings.

Supports direct SEC Archives URLs or ticker/form lookup (e.g., "AAPL/10-K", "MSFT/10-Q").
"""
from __future__ import annotations

import re
import logging
import httpx
from typing import Any

from sources.base import (
    FetchedContent,
    IngestRequest,
    SourceAdapter,
)
from sources.registry import register_adapter
from sources.util import html_to_markdown

logger = logging.getLogger(__name__)

# SEC requires a descriptive User-Agent header containing company/name and contact email
SEC_HEADERS = {
    "User-Agent": "PantheonResearch/1.0 (contact@pantheon.local)"
}


class SecEdgarAdapter(SourceAdapter):
    source_type = "sec/edgar"
    display_name = "SEC EDGAR filing"
    bucket_aliases = ("sec", "edgar")
    artifact_path_template = "sec/{cik}/{published_at}/{form_type}-{slug}.md"

    async def fetch(self, req: IngestRequest) -> FetchedContent:
        identifier = req.identifier.strip()
        cik = "unknown"
        form_type = "filing"
        published_at = None
        company_name = "SEC EDGAR"

        if identifier.startswith("http://") or identifier.startswith("https://"):
            # Direct SEC URL provided
            url = identifier
            # Attempt to extract CIK for path construction
            match = re.search(r"/data/(\d+)/", url)
            if match:
                cik = match.group(1)
            
            async with httpx.AsyncClient() as client:
                r = await client.get(url, headers=SEC_HEADERS, timeout=60)
                r.raise_for_status()
                html_content = r.text
            
            title_match = re.search(r"<title>(.*?)</title>", html_content, re.IGNORECASE)
            title = title_match.group(1).strip() if title_match else f"SEC Filing CIK {cik}"
        else:
            # Ticker/Form format, e.g. "AAPL/10-K" or "MSFT/10-Q"
            if "/" not in identifier:
                raise ValueError("Identifier must be a SEC URL or 'TICKER/FORM_TYPE' (e.g. 'AAPL/10-K')")
            
            ticker, form_type = identifier.split("/", 1)
            ticker = ticker.strip().upper()
            form_type = form_type.strip().upper()

            # Step A: Get CIK mapping from SEC
            async with httpx.AsyncClient() as client:
                r = await client.get("https://data.sec.gov/files/company_tickers.json", headers=SEC_HEADERS, timeout=30)
                r.raise_for_status()
                tickers_data = r.json()

            for item in tickers_data.values():
                if str(item.get("ticker")).upper() == ticker:
                    cik = str(item.get("cik_str"))
                    company_name = item.get("title")
                    break

            if not cik:
                if ticker.isdigit():
                    cik = ticker
                    company_name = f"CIK {cik}"
                else:
                    raise ValueError(f"Ticker or CIK symbol '{ticker}' not found in SEC company database.")

            # Pad CIK to 10 digits as required by SEC APIs
            cik_10 = cik.zfill(10)
            submissions_url = f"https://data.sec.gov/submissions/CIK{cik_10}.json"

            # Step B: Get filings list for the CIK
            async with httpx.AsyncClient() as client:
                r = await client.get(submissions_url, headers=SEC_HEADERS, timeout=30)
                r.raise_for_status()
                sub_data = r.json()

            recent_filings = sub_data.get("filings", {}).get("recent", {})
            forms = recent_filings.get("form", [])
            accession_numbers = recent_filings.get("accessionNumber", [])
            filing_dates = recent_filings.get("filingDate", [])
            primary_documents = recent_filings.get("primaryDocument", [])

            # Find the latest filing matching the form type (first match is newest)
            filing_idx = -1
            for i, f in enumerate(forms):
                if f == form_type:
                    filing_idx = i
                    break

            if filing_idx == -1:
                raise ValueError(f"No filing of type '{form_type}' found for '{ticker}'.")

            accession_no = accession_numbers[filing_idx]
            accession_no_no_hyphen = accession_no.replace("-", "")
            prim_doc = primary_documents[filing_idx]
            published_at = filing_dates[filing_idx]

            # Step C: Construct direct Archives URL and fetch primary document
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_no_hyphen}/{prim_doc}"
            async with httpx.AsyncClient() as client:
                r = await client.get(url, headers=SEC_HEADERS, timeout=60)
                r.raise_for_status()
                html_content = r.text

            title = f"{company_name} {form_type} Filing ({published_at})"

        # Optimize HTML by stripping heavy style tags and XML data blocks to speed up parsing
        html_cleaned = re.sub(r"<style[^>]*>.*?</style>", "", html_content, flags=re.DOTALL | re.IGNORECASE)
        html_cleaned = re.sub(r"<xml[^>]*>.*?</xml>", "", html_cleaned, flags=re.DOTALL | re.IGNORECASE)

        # Convert cleaned HTML page to markdown
        text = html_to_markdown(html_cleaned)

        return FetchedContent(
            text=text,
            title=title,
            author_or_publisher=company_name,
            url=url,
            published_at=published_at,
            extra_meta={
                "cik": cik,
                "form_type": form_type,
                "fetch_method": "sec_edgar_api"
            }
        )

    def render_artifact_path(self, req: IngestRequest, fetched: FetchedContent) -> str:
        from sources.util import slugify
        cik = fetched.extra_meta.get("cik", "unknown")
        form_type = fetched.extra_meta.get("form_type", "filing")
        published = fetched.published_at or "unknown-date"
        slug = slugify(fetched.title) or "filing"
        return self.artifact_path_template.format(
            cik=cik,
            published_at=published,
            form_type=form_type.lower(),
            slug=slug
        )


register_adapter(SecEdgarAdapter())

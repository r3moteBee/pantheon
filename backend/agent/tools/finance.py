"""SEC/EDGAR financial analysis tools."""
from __future__ import annotations

from typing import Any
import httpx
from agent.tools.registry import ToolContext, tool
from agent.tools import workspace as _ws


SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "analyze_company_financials",
            "description": "Common-size analysis, key ratios or YoY growth for a company from SEC/EDGAR XBRL data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string"
                    },
                    "analysis_type": {
                        "type": "string",
                        "enum": ["common_size", "ratios", "growth"]
                    },
                    "years": {
                        "type": "integer",
                        "description": "Default 3."
                    }
                },
                "required": ["ticker", "analysis_type"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "compare_company_strategy_and_risks",
            "description": "Summarise and compare strategy, R&D and risk factors from companies' 10-K filings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tickers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "e.g. ['AAPL', 'MSFT']."
                    },
                    "focus_areas": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["strategy", "risks", "rd_patterns", "all"]},
                        "description": "Default ['all']."
                    },
                    "year": {
                        "type": "integer",
                        "description": "Fiscal year (default latest)."
                    }
                },
                "required": ["tickers"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_earnings_call",
            "description": (
                "Themes, guidance, product updates and sentiment from an earnings-call transcript in the "
                "workspace."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string"
                    },
                    "extract_guidance": {
                        "type": "boolean",
                        "description": "Default true."
                    }
                },
                "required": ["file_path"],
                "additionalProperties": False
            }
        }
    },
]


@tool('analyze_company_financials')
async def _tool_analyze_company_financials(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    ticker = tool_args["ticker"].strip().upper()
    analysis_type = tool_args["analysis_type"]
    years_count = tool_args.get("years", 3)
    if years_count <= 0:
        years_count = 3

    from datetime import datetime
    current_year = datetime.now().year

    # Use same user agent required by SEC EDGAR
    sec_headers = {
        "User-Agent": "PantheonResearch/1.0 (contact@pantheon.local)"
    }

    # Step A: Get CIK mapping
    async with httpx.AsyncClient() as client:
        r = await client.get("https://data.sec.gov/files/company_tickers.json", headers=sec_headers, timeout=30)
        r.raise_for_status()
        tickers_data = r.json()

    cik = None
    company_name = ticker
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
            return f"Ticker or CIK '{ticker}' not found in SEC company directory."

    cik_10 = cik.zfill(10)

    # Step B: Get facts
    facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_10}.json"
    async with httpx.AsyncClient() as client:
        r = await client.get(facts_url, headers=sec_headers, timeout=30)
        r.raise_for_status()
        facts = r.json()

    def get_metric(keys: list[str], yr: int) -> float | None:
        for taxonomy in ("us-gaap", "dei"):
            tax_dict = facts.get("facts", {}).get(taxonomy, {})
            for key in keys:
                if key in tax_dict:
                    units = tax_dict[key].get("units", {})
                    for unit, entries in units.items():
                        # Annual value for fiscal year `yr`. SEC `fy` is
                        # the FILING's fiscal year — each 10-K also repeats
                        # prior years under the same fy — so match on the
                        # period end date instead, require a ~1-year
                        # duration for flow metrics, and take the latest
                        # filing (restatements win).
                        best = None
                        for entry in entries:
                            if entry.get("form") != "10-K" or entry.get("fp") != "FY":
                                continue
                            end = str(entry.get("end") or "")
                            entry_year = end[:4] if end else str(entry.get("fy") or "")
                            if entry_year != str(yr):
                                continue
                            start = entry.get("start")
                            if start:
                                try:
                                    from datetime import date as _d
                                    days = (_d.fromisoformat(end) - _d.fromisoformat(start)).days
                                except ValueError:
                                    days = 365
                                if not 330 <= days <= 400:
                                    continue
                            if best is None or str(entry.get("filed") or "") > str(best.get("filed") or ""):
                                best = entry
                        if best is not None:
                            return float(best.get("val"))
        return None

    revenue_keys = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet"]
    gp_keys = ["GrossProfit"]
    cor_keys = ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"]
    rd_keys = ["ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"]
    sga_keys = ["SellingGeneralAndAdministrativeExpense", "SellingAndMarketingExpense"]
    opinc_keys = ["OperatingIncomeLoss"]
    netinc_keys = ["NetIncomeLoss"]
    assets_keys = ["Assets"]
    liab_keys = ["Liabilities", "LiabilitiesAndStockholdersEquity"]
    cash_keys = ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]

    # Let's inspect the years available in Revenues
    available_years = set()
    tax_dict = facts.get("facts", {}).get("us-gaap", {})
    for key in revenue_keys:
        if key in tax_dict:
            for entries in tax_dict[key].get("units", {}).values():
                for entry in entries:
                    if entry.get("form") != "10-K":
                        continue
                    end = str(entry.get("end") or "")
                    if end[:4].isdigit():
                        available_years.add(int(end[:4]))
                    elif entry.get("fy"):
                        available_years.add(int(entry.get("fy")))

    if not available_years:
        available_years = {current_year - 1, current_year - 2, current_year - 3}

    sorted_years = sorted(list(available_years), reverse=True)[:years_count]

    year_data = {}
    for yr in sorted_years:
        rev = get_metric(revenue_keys, yr)
        gp = get_metric(gp_keys, yr)
        cor = get_metric(cor_keys, yr)
        rd = get_metric(rd_keys, yr)
        sga = get_metric(sga_keys, yr)
        op = get_metric(opinc_keys, yr)
        ni = get_metric(netinc_keys, yr)
        ast = get_metric(assets_keys, yr)
        li = get_metric(liab_keys, yr)
        csh = get_metric(cash_keys, yr)

        # If gp is missing but cor is available, compute gp = rev - cor
        if gp is None and rev is not None and cor is not None:
            gp = rev - cor
        # If cor is missing but gp is available, cor = rev - gp
        if cor is None and rev is not None and gp is not None:
            cor = rev - gp

        year_data[yr] = {
            "Revenue": rev, "GrossProfit": gp, "CostOfRevenue": cor,
            "R&D": rd, "SG&A": sga, "OperatingIncome": op, "NetIncome": ni,
            "Assets": ast, "Liabilities": li, "Cash": csh
        }

    if analysis_type == "common_size":
        report = f"# Common Size Analysis for {company_name} ({ticker})\n\n"

        # 1. Income Statement
        report += "### Income Statement (% of Revenue)\n\n"
        headers = ["Metric"] + [str(y) for y in sorted_years]
        report += "| " + " | ".join(headers) + " |\n"
        report += "| " + " | ".join(["---"] * len(headers)) + " |\n"

        is_metrics = [
            ("Revenue", "Revenue"),
            ("Cost of Revenue", "CostOfRevenue"),
            ("Gross Profit", "GrossProfit"),
            ("Research & Development", "R&D"),
            ("SG&A Expense", "SG&A"),
            ("Operating Income", "OperatingIncome"),
            ("Net Income", "NetIncome"),
        ]

        for label, key in is_metrics:
            row = [label]
            for yr in sorted_years:
                val = year_data[yr][key]
                rev = year_data[yr]["Revenue"]
                if val is not None and rev:
                    pct = (val / rev) * 100
                    row.append(f"{pct:.2f}%")
                else:
                    row.append("N/A")
            report += "| " + " | ".join(row) + " |\n"

        # 2. Balance Sheet
        report += "\n### Balance Sheet (% of Total Assets)\n\n"
        report += "| " + " | ".join(headers) + " |\n"
        report += "| " + " | ".join(["---"] * len(headers)) + " |\n"

        bs_metrics = [
            ("Total Assets", "Assets"),
            ("Total Liabilities", "Liabilities"),
            ("Cash & Cash Equivalents", "Cash"),
        ]

        for label, key in bs_metrics:
            row = [label]
            for yr in sorted_years:
                val = year_data[yr][key]
                ast = year_data[yr]["Assets"]
                if val is not None and ast:
                    pct = (val / ast) * 100
                    row.append(f"{pct:.2f}%")
                else:
                    row.append("N/A")
            report += "| " + " | ".join(row) + " |\n"

        return report

    elif analysis_type == "ratios":
        report = f"# Financial Ratios Analysis for {company_name} ({ticker})\n\n"
        headers = ["Metric"] + [str(y) for y in sorted_years]
        report += "| " + " | ".join(headers) + " |\n"
        report += "| " + " | ".join(["---"] * len(headers)) + " |\n"

        ratio_defs = [
            ("Gross Margin (Profitability)", lambda d: (d["GrossProfit"] / d["Revenue"] * 100) if d["GrossProfit"] is not None and d["Revenue"] else None, "{:.2f}%"),
            ("Operating Margin (Profitability)", lambda d: (d["OperatingIncome"] / d["Revenue"] * 100) if d["OperatingIncome"] is not None and d["Revenue"] else None, "{:.2f}%"),
            ("Net Margin (Profitability)", lambda d: (d["NetIncome"] / d["Revenue"] * 100) if d["NetIncome"] is not None and d["Revenue"] else None, "{:.2f}%"),
            ("R&D Intensity (Investment)", lambda d: (d["R&D"] / d["Revenue"] * 100) if d["R&D"] is not None and d["Revenue"] else None, "{:.2f}%"),
            ("Return on Assets (ROA)", lambda d: (d["NetIncome"] / d["Assets"] * 100) if d["NetIncome"] is not None and d["Assets"] else None, "{:.2f}%"),
            ("Debt to Assets Ratio (Solvency)", lambda d: (d["Liabilities"] / d["Assets"] * 100) if d["Liabilities"] is not None and d["Assets"] else None, "{:.2f}%"),
            ("Cash to Assets Ratio (Liquidity)", lambda d: (d["Cash"] / d["Assets"] * 100) if d["Cash"] is not None and d["Assets"] else None, "{:.2f}%"),
        ]

        for label, formula, fmt in ratio_defs:
            row = [label]
            for yr in sorted_years:
                val = formula(year_data[yr])
                if val is not None:
                    row.append(fmt.format(val))
                else:
                    row.append("N/A")
            report += "| " + " | ".join(row) + " |\n"
        return report

    elif analysis_type == "growth":
        report = f"# Year-over-Year (YoY) Growth for {company_name} ({ticker})\n\n"
        # To show growth, we need at least N years, comparing yr with yr-1
        growth_years = sorted_years[:-1] if len(sorted_years) > 1 else []
        if not growth_years:
            return "Need at least 2 years of data to perform YoY growth analysis."

        headers = ["Metric"] + [f"{y} YoY" for y in growth_years]
        report += "| " + " | ".join(headers) + " |\n"
        report += "| " + " | ".join(["---"] * len(headers)) + " |\n"

        growth_metrics = [
            ("Revenue Growth", "Revenue"),
            ("Gross Profit Growth", "GrossProfit"),
            ("R&D Expense Growth", "R&D"),
            ("Net Income Growth", "NetIncome"),
        ]

        for label, key in growth_metrics:
            row = [label]
            for yr in growth_years:
                val_curr = year_data[yr][key]
                # sorted_years is newest-first; the prior year is yr-1.
                val_prev = (year_data.get(yr - 1) or {}).get(key)
                if val_curr is not None and val_prev:
                    growth = ((val_curr - val_prev) / val_prev) * 100
                    row.append(f"{growth:+.2f}%")
                else:
                    row.append("N/A")
            report += "| " + " | ".join(row) + " |\n"
        return report



@tool('compare_company_strategy_and_risks')
async def _tool_compare_company_strategy_and_risks(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    effective_project = ctx.effective_project
    tickers = tool_args["tickers"]
    focus_list = tool_args.get("focus_areas", ["all"])

    ticker_texts = {}
    from models.provider import get_provider_for

    for ticker in tickers:
        ticker = ticker.strip().upper()
        try:
            from sources.adapters.sec_edgar import SecEdgarAdapter
            from sources.base import IngestRequest
            adapter = SecEdgarAdapter()

            ident = f"{ticker}/10-K"
            fetched = await adapter.fetch(IngestRequest(
                source_type="sec/edgar",
                identifier=ident,
                project_id=effective_project
            ))
            # Take first 40k chars of the markdown body (Business description and risks)
            ticker_texts[ticker] = fetched.text[:40000]
        except Exception as e:
            ticker_texts[ticker] = f"[Could not fetch 10-K filing from SEC EDGAR: {e}]"

    prompt_content = (
        "You are an expert financial analyst. Analyze and compare the corporate strategy, "
        "risks, and R&D patterns of the following tech vendors based on their annual reports (10-K).\n\n"
    )
    for tick, txt in ticker_texts.items():
        prompt_content += f"=== TICKER: {tick} ===\n{txt}\n\n"

    prompt_content += (
        f"Create a detailed comparative report in Markdown format. Focus areas: {', '.join(focus_list)}.\n"
        "Include:\n"
        "  1. Executive Summary comparison.\n"
        "  2. Strategic Alignment & R&D patterns (how each company is investing in future tech like AI or cloud).\n"
        "  3. Risk Assessment comparison (competitive, regulatory, operational).\n"
        "  4. Side-by-side comparison summary table."
    )

    provider = get_provider_for("summarize")
    prompt_messages = [
        {"role": "system", "content": "You are a senior investment analyst specializing in enterprise tech vendors. Return only the detailed markdown report body."},
        {"role": "user", "content": prompt_content}
    ]

    res_text = ""
    async for chunk in provider.chat(messages=prompt_messages, tools=[], stream=False):
        if isinstance(chunk, dict):
            res_text += chunk.get("content", "") or ""
        elif isinstance(chunk, str):
            res_text += chunk
    return res_text.strip()



@tool('analyze_earnings_call')
async def _tool_analyze_earnings_call(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    effective_project = ctx.effective_project
    rel_path = tool_args["file_path"]
    safe_path = _ws._safe_workspace_path(rel_path, effective_project)
    if not safe_path.exists():
        return f"Earnings transcript file not found at: {rel_path}"

    try:
        content = safe_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"Failed to read file: {e}"

    extract_guidance = tool_args.get("extract_guidance", True)

    prompt_content = (
        "You are an expert equity research analyst. Analyze this earnings call transcript:\n\n"
        f"{content[:50000]}\n\n"
        "Prepare a structured report in Markdown. Extract:\n"
        "  1. Executive highlights (main themes, strategic direction).\n"
        "  2. Financial performance notes (revenue beats/misses, margins).\n"
    )
    if extract_guidance:
        prompt_content += "  3. Forward-looking guidance, estimates, and growth projections.\n"
    prompt_content += (
        "  4. Q&A summary (main questions raised by analysts and answers from management).\n"
        "  5. Sentiment analysis (Executive confidence level)."
    )

    from models.provider import get_provider_for
    provider = get_provider_for("summarize")
    prompt_messages = [
        {"role": "system", "content": "You are a veteran Wall Street research analyst. Return only the detailed markdown briefing."},
        {"role": "user", "content": prompt_content}
    ]

    res_text = ""
    async for chunk in provider.chat(messages=prompt_messages, tools=[], stream=False):
        if isinstance(chunk, dict):
            res_text += chunk.get("content", "") or ""
        elif isinstance(chunk, str):
            res_text += chunk
    return res_text.strip()


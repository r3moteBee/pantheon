"""Web tools: search, fetch a page, and the Playwright browser_* tools."""
from __future__ import annotations

from typing import Any
import asyncio
import httpx
import logging
from agent.tools.registry import ToolContext, tool

logger = logging.getLogger(__name__)

SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information. Returns titles, URLs, and snippets for the top results. Snippets are often outdated: for a latest version, price or status, open the source (a releases/downloads listing for versions) with web_fetch before answering.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Read a web page (article text as markdown) without saving it. To keep a source — indexed, searchable and linked into the graph — use ingest_source instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "http(s) URL"},
                    "max_chars": {"type": "integer", "description": "Truncate the text (default 20000)"}
                },
                "required": ["url"]
            }
        }
    },
]


async def _web_fetch(url: str, max_chars: Any = None) -> str:
    """Fetch a public page through the SSRF guard and return readable markdown."""
    import re as _re
    from utils.net import safe_http_get
    url = (url or "").strip()
    if not _re.match(r"^https?://", url, _re.I):
        return "Error: web_fetch needs an http(s) URL."
    try:
        limit = max(1000, min(int(max_chars or 20000), 60000))
    except (TypeError, ValueError):
        limit = 20000
    try:
        resp = await safe_http_get(url, timeout=30)
    except Exception as e:
        return f"Error: web_fetch failed for {url}: {e}"
    if resp.status_code >= 400:
        return f"Error: web_fetch got HTTP {resp.status_code} from {url}"
    ctype = (resp.headers.get("content-type") or "").lower()
    if "pdf" in ctype:
        return f"{url} is a PDF — use ingest_source to read and keep it."
    if not any(t in ctype for t in ("html", "text", "json", "xml")) and ctype:
        return f"{url} returned {ctype}, not a readable page."
    body = resp.text
    title = ""
    if "html" in ctype or body.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        m = _re.search(r"<title[^>]*>(.*?)</title>", body, _re.I | _re.S)
        title = _re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
        try:
            import trafilatura
            from sources.util import html_to_markdown
            article = trafilatura.extract(body, output_format="html", include_links=True) or ""
            text = html_to_markdown(article) if article else html_to_markdown(body)
        except Exception:
            text = _re.sub(r"<[^>]+>", " ", body)
    else:
        text = body
    text = text.strip()
    total = len(text)
    if total > limit:
        text = text[:limit] + f"\n\n[… truncated {total - limit} of {total} chars — pass max_chars or use ingest_source for the full text]"
    head = f"# {title}\n" if title else ""
    return f"{head}Source: {url}\n\n{text or '(no readable text)'}"


async def _web_search(query: str) -> str:
    """Run a web search via the provider chain (Brave → SearXNG → DDG by default).

    Falls through to the next provider on exception, empty results, or
    quota/rate-limit exhaustion. Per-provider quotas, rate limits, and
    chain ordering are configured via the SearchProviderManager.
    """
    try:
        from agent.search_providers import get_search_manager
        return await get_search_manager().search(query)
    except Exception as e:
        logger.exception("search provider chain failed; falling back to DDG-only")
        return await _ddg_search(query)


async def _ddg_search(query: str) -> str:
    """Fallback: DuckDuckGo via HTML scrape (no API key needed)."""
    url = "https://html.duckduckgo.com/html/"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; AgentHarness/1.0)"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, data={"q": query, "b": ""}, headers=headers)
            resp.raise_for_status()
            import re
            results = re.findall(r'<a class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', resp.text, re.S)
            snippets = re.findall(r'<a class="result__snippet"[^>]*>(.*?)</a>', resp.text, re.S)
            clean_tag = re.compile(r'<[^>]+>')
            lines = []
            for i, (link, title) in enumerate(results[:5]):
                clean_title = clean_tag.sub('', title).strip()
                snippet = clean_tag.sub('', snippets[i]).strip() if i < len(snippets) else ""
                lines.append(f"{i+1}. {clean_title}\n   {link}\n   {snippet}")
            return "\n\n".join(lines) if lines else "No results found."
    except Exception as e:
        return f"Search failed: {e}"


@tool('web_search')
async def _tool_web_search(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    query = tool_args["query"]
    from agent.release_facts import release_facts
    results, facts = await asyncio.gather(_web_search(query), release_facts(query))
    if facts:
        # Release tables beat snippets for "latest version" questions (agent/release_facts.py).
        return f"{facts}\n\nSearch results (snippets may be outdated):\n{results}"
    return results



@tool('web_fetch')
async def _tool_web_fetch(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    return await _web_fetch(tool_args.get("url") or "", tool_args.get("max_chars"))



@tool(prefix='browser_')
async def _tool_browser_tools(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    effective_project = ctx.effective_project
    from agent.browser_tools import browser_enabled, execute_browser_tool
    if not browser_enabled():
        return "Browser tools are disabled. Set BROWSER_ENABLED=true in .env and install Playwright."
    return await execute_browser_tool(tool_name, tool_args, effective_project)


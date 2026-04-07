"""Playwright-backed browser tools for the agent.

Gated behind BROWSER_ENABLED=true in .env. Requires:
    pip install playwright
    playwright install chromium

Exposes a small, stateful browser session keyed by project_id so the agent
can navigate, read, interact, and screenshot pages across multiple tool calls.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PLAYWRIGHT = None
_BROWSER = None
_CONTEXTS: dict[str, Any] = {}  # project_id -> (context, page)
_LOCK = asyncio.Lock()


def browser_enabled() -> bool:
    return os.getenv("BROWSER_ENABLED", "false").lower() in ("1", "true", "yes")


async def _ensure_browser():
    global _PLAYWRIGHT, _BROWSER
    if _BROWSER is not None:
        return
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise RuntimeError(
            "Playwright not installed. Run: pip install playwright && playwright install chromium"
        ) from e
    _PLAYWRIGHT = await async_playwright().start()
    headless = os.getenv("BROWSER_HEADLESS", "true").lower() != "false"
    ws_url = os.getenv("BROWSER_WS_URL")  # optional remote CDP (Browserless/Browserbase)
    if ws_url:
        _BROWSER = await _PLAYWRIGHT.chromium.connect_over_cdp(ws_url)
        logger.info("Connected to remote browser at %s", ws_url)
    else:
        _BROWSER = await _PLAYWRIGHT.chromium.launch(headless=headless)
        logger.info("Launched local chromium (headless=%s)", headless)


async def _get_page(project_id: str):
    async with _LOCK:
        await _ensure_browser()
        if project_id not in _CONTEXTS:
            ctx = await _BROWSER.new_context(
                user_agent=os.getenv(
                    "BROWSER_USER_AGENT",
                    "Mozilla/5.0 (Pantheon Agent) Chrome/120 Safari/537.36",
                ),
                viewport={"width": 1280, "height": 900},
            )
            page = await ctx.new_page()
            _CONTEXTS[project_id] = (ctx, page)
        return _CONTEXTS[project_id][1]


async def shutdown():
    global _PLAYWRIGHT, _BROWSER
    for ctx, _ in _CONTEXTS.values():
        try:
            await ctx.close()
        except Exception:
            pass
    _CONTEXTS.clear()
    if _BROWSER:
        try:
            await _BROWSER.close()
        except Exception:
            pass
        _BROWSER = None
    if _PLAYWRIGHT:
        try:
            await _PLAYWRIGHT.stop()
        except Exception:
            pass
        _PLAYWRIGHT = None


# ───────── tool implementations ─────────

async def browser_open(url: str, project_id: str) -> str:
    page = await _get_page(project_id)
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    title = await page.title()
    return f"Opened {page.url}\nTitle: {title}"


async def browser_read(project_id: str, max_chars: int = 8000) -> str:
    page = await _get_page(project_id)
    # Prefer visible body text; fall back to innerText.
    text = await page.evaluate(
        """() => {
            const t = document.body ? document.body.innerText : '';
            return t.replace(/\\n{3,}/g, '\\n\\n').trim();
        }"""
    )
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n...[truncated, {len(text)-max_chars} more chars]"
    return text or "[page has no visible text]"


async def browser_click(selector: str, project_id: str) -> str:
    page = await _get_page(project_id)
    await page.click(selector, timeout=10000)
    return f"Clicked: {selector}"


async def browser_type(selector: str, text: str, project_id: str, submit: bool = False) -> str:
    page = await _get_page(project_id)
    await page.fill(selector, text, timeout=10000)
    if submit:
        await page.keyboard.press("Enter")
    return f"Typed into {selector}" + (" and pressed Enter" if submit else "")


async def browser_screenshot(project_id: str, rel_path: str = "screenshot.png") -> str:
    from agent.tools import _safe_workspace_path  # avoid circular import at module load
    page = await _get_page(project_id)
    safe = _safe_workspace_path(rel_path, project_id)
    safe.parent.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(safe), full_page=True)
    return f"Screenshot saved to workspace: {rel_path}"


async def browser_close(project_id: str) -> str:
    if project_id in _CONTEXTS:
        ctx, _ = _CONTEXTS.pop(project_id)
        try:
            await ctx.close()
        except Exception:
            pass
        return "Browser session closed."
    return "No active browser session."


# ───────── schemas ─────────

BROWSER_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "browser_open",
            "description": "Open a URL in a persistent headless browser session. Runs JavaScript, handles SPAs, and preserves cookies across calls. Use when web_fetch fails or when a site requires interaction.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "Absolute URL to open"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_read",
            "description": "Get the visible text of the currently loaded page from the browser session. Call after browser_open or after any interaction.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_chars": {"type": "integer", "description": "Max chars to return (default 8000)", "default": 8000}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "Click an element in the current page by CSS selector.",
            "parameters": {
                "type": "object",
                "properties": {"selector": {"type": "string", "description": "CSS selector of the element to click"}},
                "required": ["selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_type",
            "description": "Type text into an input/textarea by CSS selector. Optionally press Enter to submit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string"},
                    "text": {"type": "string"},
                    "submit": {"type": "boolean", "default": False},
                },
                "required": ["selector", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_screenshot",
            "description": "Save a full-page screenshot of the current browser page into the project workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rel_path": {"type": "string", "description": "Workspace-relative path (default: screenshot.png)", "default": "screenshot.png"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_close",
            "description": "Close the current project's browser session and free resources.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


async def execute_browser_tool(tool_name: str, args: dict[str, Any], project_id: str) -> str:
    try:
        if tool_name == "browser_open":
            return await browser_open(args["url"], project_id)
        if tool_name == "browser_read":
            return await browser_read(project_id, args.get("max_chars", 8000))
        if tool_name == "browser_click":
            return await browser_click(args["selector"], project_id)
        if tool_name == "browser_type":
            return await browser_type(args["selector"], args["text"], project_id, args.get("submit", False))
        if tool_name == "browser_screenshot":
            return await browser_screenshot(project_id, args.get("rel_path", "screenshot.png"))
        if tool_name == "browser_close":
            return await browser_close(project_id)
        return f"Unknown browser tool: {tool_name}"
    except Exception as e:
        logger.exception("Browser tool %s failed", tool_name)
        return f"Browser tool error ({tool_name}): {e}"

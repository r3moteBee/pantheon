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
        # Optional: use a system Chromium instead of Playwright's download.
        exe = os.getenv("BROWSER_EXECUTABLE_PATH") or None
        _BROWSER = await _PLAYWRIGHT.chromium.launch(headless=headless, executable_path=exe)
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
            # Every request any page in this context makes (navigations,
            # subresources, fetch/XHR, popups) goes through the SSRF guard.
            await ctx.route("**/*", _guard_route)
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


# ───────── SSRF guard ─────────
# Playwright can't pin DNS, so this checks each request's destination
# (cached briefly per host) and blocks non-public addresses. Route handlers
# don't see redirect hops, so tools also re-check the page's final URL
# before returning anything to the model.

_HOST_OK: dict[str, tuple[bool, float]] = {}
_HOST_TTL = 60.0


async def _url_allowed(url: str) -> bool:
    import time
    from urllib.parse import urlparse
    from utils.net import UnsafeURLError, check_public_url
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme in ("data", "blob", "about"):
        return True
    if scheme not in ("http", "https"):
        return False
    key = f"{scheme}://{parsed.hostname}:{parsed.port or ''}"
    hit = _HOST_OK.get(key)
    now = time.monotonic()
    if hit and now - hit[1] < _HOST_TTL:
        return hit[0]
    try:
        await check_public_url(url)
        ok = True
    except UnsafeURLError:
        ok = False
    _HOST_OK[key] = (ok, now)
    return ok


async def _guard_route(route) -> None:
    url = route.request.url
    if not await _url_allowed(url):
        logger.warning("browser: blocked request to non-public address: %s", url[:200])
        await route.abort("blockedbyclient")
        return
    if route.request.is_navigation_request() and url.startswith(("http://", "https://")):
        # Fetch navigations ourselves without following redirects, so a
        # redirect to an internal address is refused before any request
        # reaches it. A fulfilled 3xx makes the browser issue the next hop
        # as a new request, which comes back through this guard.
        try:
            resp = await route.fetch(max_redirects=0)
        except Exception as e:
            logger.debug("browser: guarded fetch failed (%s); aborting", e)
            await route.abort("failed")
            return
        location = resp.headers.get("location")
        if 300 <= resp.status < 400 and location:
            from urllib.parse import urljoin
            target = urljoin(url, location)
            if not await _url_allowed(target):
                logger.warning("browser: blocked redirect %s -> %s", url[:200], target[:200])
                await route.abort("blockedbyclient")
                return
        await route.fulfill(response=resp)
        return
    await route.continue_()


async def _page_is_safe(page) -> bool:
    """False (and the page is blanked) if a redirect or script navigation
    landed on a non-public address."""
    if await _url_allowed(page.url):
        return True
    logger.warning("browser: page ended on non-public URL %s — blanking", page.url[:200])
    try:
        await page.goto("about:blank")
    except Exception:
        pass
    return False


_UNSAFE_MSG = "Refused: the page navigated to a private/internal address (blocked for safety)."


# ───────── tool implementations ─────────

async def browser_open(url: str, project_id: str) -> str:
    # Blocks file://, localhost, LAN and metadata addresses: the URL here,
    # every request via _guard_route, and the final URL after redirects.
    from utils.net import UnsafeURLError, check_public_url
    try:
        await check_public_url(url)
    except UnsafeURLError as e:
        return f"browser_open refused: {e}"
    page = await _get_page(project_id)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        if "ERR_BLOCKED_BY_CLIENT" in str(e):
            return "browser_open refused: the page redirected to a private/internal address."
        raise
    if not await _page_is_safe(page):
        return _UNSAFE_MSG
    title = await page.title()
    return f"Opened {page.url}\nTitle: {title}"


async def browser_read(project_id: str, max_chars: int = 8000) -> str:
    page = await _get_page(project_id)
    if not await _page_is_safe(page):
        return _UNSAFE_MSG
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
    if not await _page_is_safe(page):
        return _UNSAFE_MSG
    return f"Clicked: {selector}"


async def browser_type(selector: str, text: str, project_id: str, submit: bool = False) -> str:
    page = await _get_page(project_id)
    await page.fill(selector, text, timeout=10000)
    if submit:
        await page.keyboard.press("Enter")
        if not await _page_is_safe(page):
            return _UNSAFE_MSG
    return f"Typed into {selector}" + (" and pressed Enter" if submit else "")


async def browser_screenshot(project_id: str, rel_path: str = "screenshot.png") -> str:
    from agent.tools.workspace import _safe_workspace_path  # avoid circular import at module load
    page = await _get_page(project_id)
    if not await _page_is_safe(page):
        return _UNSAFE_MSG
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

"""MCP OAuth 2.1 endpoints — start the flow + receive the callback.

Per MCP spec 2025-06-18 the client must run the authorization-code flow with
PKCE. Pantheon is a single-user local app, so the redirect target is the
FastAPI process itself at /api/mcp/oauth/callback. The callback URL is
registered via DCR for each MCP server the first time the user clicks
Authorize on that connection.
"""
from __future__ import annotations

import html
import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from mcp_client.manager import get_mcp_manager

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/mcp/connections/{name}/start-oauth")
async def start_oauth(name: str) -> dict:
    """Begin the OAuth dance: discover the AS, DCR-register, return auth URL.

    Frontend opens the returned `authorize_url` in a new tab. The user
    authorizes in their browser; the AS redirects back to /callback, which
    completes the exchange and persists tokens.
    """
    mgr = get_mcp_manager()
    try:
        return await mgr.start_oauth(name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.warning("OAuth start failed for '%s': %s", name, e)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/mcp/connections/{name}/revoke-oauth")
async def revoke_oauth(name: str) -> dict:
    """Wipe persisted OAuth tokens for a connection."""
    mgr = get_mcp_manager()
    return await mgr.revoke_oauth(name)


@router.get("/mcp/oauth/callback")
async def oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> HTMLResponse:
    """Receive the OAuth redirect from the authorization server.

    Renders a small HTML page so the user knows the flow finished — the
    page closes itself after a brief delay; the MCP Connections UI polls
    the connection list and picks up the new "ok" status automatically.
    """
    if error:
        msg = error_description or error
        return _result_page(
            title="Authorization failed",
            body=f"<p>The authorization server returned an error:</p>"
                 f"<pre>{html.escape(msg)}</pre>",
            ok=False,
        )

    if not code or not state:
        return _result_page(
            title="Authorization failed",
            body="<p>Missing <code>code</code> or <code>state</code> parameter "
                 "in the redirect.</p>",
            ok=False,
        )

    mgr = get_mcp_manager()
    try:
        result = await mgr.complete_oauth(code=code, state=state)
    except Exception as e:
        logger.warning("OAuth callback exchange failed: %s", e)
        return _result_page(
            title="Token exchange failed",
            body=f"<p>{html.escape(str(e))}</p>",
            ok=False,
        )

    status = result.get("status", "unknown")
    name = html.escape(result.get("name", ""))
    if status == "connected":
        body = (
            f"<p>MCP connection <b>{name}</b> is authorized and connected.</p>"
            f"<p>Discovered tools: {result.get('tools_count', 0)}</p>"
            "<p>You can close this tab.</p>"
        )
        return _result_page(title="Connected ✓", body=body, ok=True)

    body = (
        f"<p>Token saved for <b>{name}</b>, but the MCP connect step failed.</p>"
        f"<pre>{html.escape(result.get('error', ''))}</pre>"
        "<p>Check the MCP Connections panel — you may just need to retry.</p>"
    )
    return _result_page(title="Authorized (partial)", body=body, ok=False)


def _result_page(*, title: str, body: str, ok: bool) -> HTMLResponse:
    color = "#22c55e" if ok else "#ef4444"
    return HTMLResponse(
        f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
  body {{ background: #0f172a; color: #e5e7eb;
         font-family: ui-sans-serif, system-ui, sans-serif;
         display: flex; align-items: center; justify-content: center;
         min-height: 100vh; margin: 0; }}
  .card {{ max-width: 480px; background: #1f2937; border: 1px solid #374151;
          border-radius: 12px; padding: 28px; }}
  h1 {{ color: {color}; margin-top: 0; }}
  pre {{ background: #0b1220; padding: 12px; border-radius: 6px;
        overflow-x: auto; font-size: 12px; }}
</style></head>
<body><div class="card">
  <h1>{html.escape(title)}</h1>
  {body}
  <p style="opacity: 0.6; font-size: 12px;">
    Pantheon MCP OAuth callback.
  </p>
</div>
<script>setTimeout(() => {{ window.close(); }}, 4000);</script>
</body></html>"""
    )

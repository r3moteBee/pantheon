"""Minimal Pantheon MCP Registry — reference implementation.

A ~100-line FastAPI server that implements the Pantheon MCP Registry Protocol
v1.0 (see docs/mcp-registry-protocol.md). Returns two fake servers so
platform teams can see the exact shapes Pantheon expects.

Run:
    pip install fastapi uvicorn
    uvicorn server:app --reload --port 8787

Then point Pantheon at http://localhost:8787 in Settings → MCP → Registries.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

app = FastAPI(title="Minimal Pantheon MCP Registry")

# ── Fake catalog ────────────────────────────────────────────────────────────

SERVERS: dict[str, dict[str, Any]] = {
    "acme/jira-mcp": {
        "id": "acme/jira-mcp",
        "name": "Jira",
        "version": "2026-03-12",
        "description": "Read and write Jira issues",
        "author": "Acme Platform Team",
        "transport": "stdio",
        "tags": ["ticketing", "approved"],
        "approved": True,
        "updated_at": "2026-03-12T10:00:00Z",
        "readme_md": "## Jira MCP\n\nSearch and update Jira issues from Pantheon agents.",
        "homepage": "https://git.acme.internal/platform/jira-mcp",
        "license": "Apache-2.0",
        "install": {
            "method": "npm",
            "package": "@acme/jira-mcp",
            "version": "2.4.1",
            "command": "npx",
            "args": ["-y", "@acme/jira-mcp"],
        },
        "config_schema": {
            "type": "object",
            "required": ["JIRA_BASE_URL", "JIRA_TOKEN"],
            "properties": {
                "JIRA_BASE_URL": {"type": "string", "title": "Jira base URL"},
                "JIRA_TOKEN": {"type": "string", "title": "API token", "secret": True},
            },
        },
        "tools_preview": [
            {"name": "search_issues", "description": "Search Jira issues with JQL"},
            {"name": "create_issue", "description": "Create a new Jira issue"},
        ],
        "trust": {
            "approved_by": "Acme Security",
            "approved_at": "2026-03-10",
            "signature": None,
            "sha256": None,
        },
    },
    "acme/snowflake-mcp": {
        "id": "acme/snowflake-mcp",
        "name": "Snowflake",
        "version": "2026-02-28",
        "description": "Query the Acme Snowflake warehouse (read-only)",
        "author": "Acme Data Platform",
        "transport": "http",
        "tags": ["data", "warehouse", "approved"],
        "approved": True,
        "updated_at": "2026-02-28T09:00:00Z",
        "readme_md": "## Snowflake MCP (read-only)\n\nHosted endpoint; no local install.",
        "homepage": "https://data.acme.internal/mcp/snowflake",
        "license": "Proprietary",
        "install": {
            "method": "remote",
            "url": "https://mcp.acme.internal/snowflake",
        },
        "config_schema": {
            "type": "object",
            "required": ["SNOWFLAKE_ROLE"],
            "properties": {
                "SNOWFLAKE_ROLE": {
                    "type": "string",
                    "title": "Snowflake role",
                    "enum": ["ANALYST_RO", "ENGINEER_RO"],
                },
            },
        },
        "tools_preview": [
            {"name": "run_query", "description": "Run a read-only SQL query"},
        ],
        "trust": {
            "approved_by": "Acme Data Platform",
            "approved_at": "2026-02-28",
            "signature": None,
            "sha256": None,
        },
    },
}


def _listing(server: dict[str, Any]) -> dict[str, Any]:
    """Project a full ServerDetail down to the ServerListing shape."""
    keys = ("id", "name", "description", "author", "version", "transport",
            "tags", "approved", "updated_at")
    return {k: server.get(k) for k in keys}


# ── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/.well-known/pantheon-mcp-registry.json")
def discovery() -> dict[str, Any]:
    return {
        "protocol_version": "1.0",
        "name": "Minimal Example Registry",
        "description": "Reference implementation of the Pantheon MCP Registry Protocol.",
        "auth": {"type": "none"},
        "endpoints": {
            "search": "/v1/servers",
            "get": "/v1/servers/{id}",
        },
        "capabilities": {
            "search_filters": ["tag", "transport"],
            "pagination": "cursor",
            "signing": "none",
        },
        "contact": "example@localhost",
    }


@app.get("/v1/servers")
def search(
    q: str | None = None,
    tag: str | None = None,
    transport: str | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    results = list(SERVERS.values())
    if q:
        ql = q.lower()
        results = [
            s for s in results
            if ql in s["name"].lower() or ql in s["description"].lower()
        ]
    if tag:
        results = [s for s in results if tag in s.get("tags", [])]
    if transport:
        results = [s for s in results if s.get("transport") == transport]
    return {
        "results": [_listing(s) for s in results],
        "next_cursor": None,
        "total": len(results),
    }


@app.get("/v1/servers/{server_id:path}")
def get_server(server_id: str) -> dict[str, Any]:
    server = SERVERS.get(server_id)
    if not server:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Unknown server: {server_id}"},
        )
    return server

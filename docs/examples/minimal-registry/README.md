# Minimal Pantheon MCP Registry

A ~150-line FastAPI reference implementation of the
[Pantheon MCP Registry Protocol](../../mcp-registry-protocol.md) v1.0.

Returns two fake servers (Jira via `stdio`+npm, Snowflake via `remote`) so
platform teams can see the exact shapes Pantheon expects before adapting
this to their own internal catalog.

## Run

```bash
pip install fastapi uvicorn
uvicorn server:app --reload --port 8787
```

Then in Pantheon: **Settings → MCP → Registries → Add Registry** and enter
`http://localhost:8787`.

## What to change for your deployment

1. Replace the `SERVERS` dict with a query against your internal catalog
   (database, git repo, artifact registry, etc.).
2. Switch `auth.type` in the discovery doc to `bearer` or `mtls` and enforce
   it in a FastAPI dependency.
3. Add HTTPS (behind your normal reverse proxy / ingress).
4. If you sign your entries, emit `trust.signature` as a Sigstore bundle and
   set `capabilities.signing: "sigstore"` in the discovery document.

## Conformance

See the checklist in
[`../../mcp-registry-protocol.md`](../../mcp-registry-protocol.md) §9.

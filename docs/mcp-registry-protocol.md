# Pantheon MCP Registry Protocol

**Version:** 1.0
**Status:** Draft
**Audience:** Enterprise platform teams standing up an internal MCP server registry
that Pantheon agents can browse and install from.

---

## 1. Overview

Pantheon agents can browse MCP (Model Context Protocol) server registries and
install servers with one click. To support private / enterprise registries,
Pantheon speaks a small, stable JSON-over-HTTPS protocol. Any registry that
implements this protocol can be added to Pantheon with zero custom code — the
built-in `GenericRegistryAdapter` consumes it directly.

This document is the contract. If your registry returns the shapes described
here, Pantheon will:

1. discover it via a well-known URL,
2. search and paginate through your server catalog,
3. render install dialogs from your config schemas,
4. verify trust metadata (and signatures, if present),
5. launch or connect to the MCP server using the install metadata you return.

**Non-goals.** This protocol does not cover publishing, authentication to the
underlying MCP servers themselves, or tool invocation. It is strictly a
discovery + install metadata API.

---

## 2. Transport and authentication

- **Transport:** HTTPS only. Plain HTTP is rejected except for `localhost` in
  development mode.
- **Private networks:** RFC1918 addresses and `.internal` / `.corp` / `.lan`
  TLDs are permitted. Pantheon suppresses its usual "unfamiliar domain"
  warnings for registries that were explicitly added by an admin.
- **Content type:** `application/json; charset=utf-8`.
- **Authentication options** (the registry declares which it uses in the
  discovery document):
  - `none` — public registry, no credential.
  - `bearer` — `Authorization: Bearer <token>`. Pantheon stores the token in
    its vault, per registry.
  - `mtls` — mutual TLS. Pantheon presents a client certificate configured by
    the admin.
- **Rate limiting:** return standard `429 Too Many Requests` with a
  `Retry-After` header. Pantheon honors it.
- **CORS:** not required. Pantheon calls the registry from the backend, not
  the browser.

---

## 3. Discovery

Every registry MUST serve a discovery document at:

```
GET /.well-known/pantheon-mcp-registry.json
```

Example response:

```json
{
  "protocol_version": "1.0",
  "name": "Acme Internal MCP Registry",
  "description": "Internally approved MCP servers for Acme engineers",
  "auth": { "type": "bearer" },
  "endpoints": {
    "search": "/v1/servers",
    "get":    "/v1/servers/{id}",
    "icon":   "/v1/servers/{id}/icon"
  },
  "capabilities": {
    "search_filters": ["transport", "tag", "author"],
    "pagination": "cursor",
    "signing": "sigstore"
  },
  "contact": "mcp-platform@acme.com"
}
```

Required fields: `protocol_version`, `name`, `auth`, `endpoints.search`,
`endpoints.get`. Everything else is optional. Endpoint paths are resolved
relative to the discovery URL's origin. `{id}` is a URL-encoded server ID.

---

## 4. Search endpoint

```
GET {endpoints.search}?q=<query>&tag=<tag>&transport=<stdio|http|sse>&cursor=<opaque>
```

All query parameters are optional. The registry MAY ignore filters it doesn't
support. Response:

```json
{
  "results": [ /* ServerListing */ ],
  "next_cursor": "opaque-or-null",
  "total": 42
}
```

`total` is optional and may be `null` for registries that can't cheaply count.
`next_cursor` is `null` when there are no more results. Pantheon treats the
cursor as opaque and passes it back verbatim.

### 4.1 `ServerListing`

The minimum Pantheon needs to render a search result row. Keep this small —
detail information belongs in the `get` endpoint.

| Field         | Type     | Required | Notes |
|---------------|----------|----------|-------|
| `id`          | string   | yes      | Stable identifier, URL-safe. Used in the `get` endpoint. |
| `name`        | string   | yes      | Display name. |
| `description` | string   | yes      | One-line summary, ≤ 200 chars. |
| `author`      | string   | no       | Team or individual. |
| `version`     | string   | yes      | Date-based (`2026-03-12`) or semver. |
| `transport`   | enum     | yes      | `stdio` \| `http` \| `sse`. |
| `tags`        | string[] | no       | Free-form. |
| `approved`    | boolean  | no       | Enterprise trust signal; shown as a badge. |
| `updated_at`  | RFC3339  | no       | For "Updated N days ago" display. |

---

## 5. Get endpoint

```
GET {endpoints.get}    (with {id} substituted)
```

Returns a `ServerDetail`, which is everything Pantheon needs to install and
launch the server with no out-of-band steps.

```json
{
  "id": "acme/jira-mcp",
  "name": "Jira",
  "version": "2026-03-12",
  "description": "Read and write Jira issues",
  "readme_md": "## Usage\n...",
  "homepage": "https://git.acme.com/platform/jira-mcp",
  "license": "Apache-2.0",
  "transport": "stdio",
  "install": {
    "method": "npm",
    "package": "@acme/jira-mcp",
    "version": "2.4.1",
    "command": "npx",
    "args": ["-y", "@acme/jira-mcp"]
  },
  "config_schema": {
    "type": "object",
    "required": ["JIRA_BASE_URL", "JIRA_TOKEN"],
    "properties": {
      "JIRA_BASE_URL": { "type": "string", "title": "Jira base URL" },
      "JIRA_TOKEN":    { "type": "string", "title": "API token", "secret": true }
    }
  },
  "tools_preview": [
    { "name": "search_issues", "description": "Search Jira issues with JQL" }
  ],
  "trust": {
    "approved_by": "Acme Security",
    "approved_at": "2026-03-10",
    "signature": null,
    "sha256": "6f1e..."
  }
}
```

### 5.1 `install` block

The `method` field tells Pantheon how to launch the server. Supported values:

| Method   | Required sibling fields               | Behavior |
|----------|---------------------------------------|----------|
| `npm`    | `package`, `command`, `args`          | Run via `npx` / pinned npm. |
| `uvx`    | `package`, `command`, `args`          | Run via `uvx`. |
| `pipx`   | `package`, `command`, `args`          | Run via `pipx run`. |
| `docker` | `image`, optional `args`              | Run via `docker run`. |
| `binary` | `url`, `sha256`, `command`, `args`    | Download + verify + execute. |
| `remote` | `url`                                 | Connect to a hosted MCP endpoint. No local install. |

`remote` is usually what enterprises want: central ops, central logging,
central revocation. `binary` is the highest-risk method and Pantheon will
always show a prominent warning in the install dialog.

### 5.2 `config_schema`

A small subset of JSON Schema. Pantheon auto-generates the install dialog's
form from this.

- `type: "object"` at the root.
- Properties may be `string`, `number`, `boolean`, or `string` with an `enum`.
- No nested objects, no arrays. If you need structure, flatten it.
- `required: [...]` is honored.
- Per-property: `title`, `description`, `default`, and — importantly —
  `secret: true` on any field Pantheon should store in the vault rather than
  in plain config.

### 5.3 `trust` block

Optional but strongly recommended for enterprise deployments.

- `approved_by` / `approved_at` are displayed in the install dialog.
- `sha256` is the digest of the install payload (for `binary`) or of a
  canonical representation of the detail document (for others). Pantheon
  displays it; verification is optional unless a signature is also present.
- `signature` — if the discovery doc declared `signing: sigstore`, this is a
  Sigstore bundle. Pantheon verifies it before allowing install.

---

## 6. Icon endpoint (optional)

```
GET {endpoints.icon}    (with {id} substituted)
```

Returns `image/png` or `image/svg+xml`. Max 256 KiB. Pantheon caches icons for
24 hours. If the endpoint is omitted or returns 404, Pantheon uses a default
icon.

---

## 7. Versioning and updates

- The `version` string is opaque to Pantheon for comparison purposes; any
  change triggers an "Update available" badge in the installed-servers UI.
- Pantheon polls the `get` endpoint for installed servers at most once per
  hour per server.
- Registries SHOULD use date-based versions (`2026-03-12`, `2026-03-12.1`) to
  match Pantheon's own convention.
- Updates are never applied automatically. The user reviews the new detail
  document and explicitly accepts.

---

## 8. Error responses

Standard HTTP status codes. Error body:

```json
{ "error": "short_machine_code", "message": "Human-readable explanation" }
```

Pantheon surfaces `message` to the user verbatim. Do not include secrets or
internal paths.

---

## 9. Conformance checklist

A registry is conformant if it:

- [ ] Serves the discovery document at `/.well-known/pantheon-mcp-registry.json`
- [ ] Declares `protocol_version: "1.0"`
- [ ] Implements `search` with pagination via `next_cursor`
- [ ] Implements `get` returning a valid `ServerDetail`
- [ ] Returns at least one valid `install.method` for every server
- [ ] Honors `429` with `Retry-After` on overload
- [ ] Uses HTTPS (or localhost in dev)
- [ ] All `secret: true` config fields correspond to values that are safe to
      transmit once at install time and never logged

JSON Schemas for `ServerListing` and `ServerDetail` live alongside this doc at
`docs/examples/minimal-registry/schemas/` and can be used in registry CI.

---

## 10. Adding a registry to Pantheon

Admins add a registry via `pantheon.config.json`:

```json
{
  "mcp_registries": [
    {
      "url": "https://mcp-registry.acme.internal",
      "auth": { "type": "bearer", "token_ref": "vault:mcp_registry_acme" }
    }
  ]
}
```

…or via the Pantheon Settings → MCP → Registries UI. Pantheon fetches the
discovery document, validates `protocol_version`, and makes the registry
available in the MCP browser.

---

## 11. Reference implementation

A minimal FastAPI registry is provided at
[`docs/examples/minimal-registry/`](examples/minimal-registry/). It returns
two fake servers and is intended as a starting point for enterprise platform
teams — clone it, point it at your internal catalog, and you have a
conformant registry in under an hour.

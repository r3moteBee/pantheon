# Pantheon Skill Registry Protocol

**Version:** 1.0
**Status:** Draft
**Audience:** Enterprise platform teams standing up an internal skill registry
that Pantheon agents can browse and install from. Companion to
`docs/mcp-registry-protocol.md` — same shape, different payload.

---

## 1. Overview

Pantheon agents can browse skill registries and install skills with one click.
To support private / enterprise registries, Pantheon speaks a small, stable
JSON-over-HTTPS protocol. Any registry that implements this protocol can be
added to Pantheon with zero custom adapter code — the built-in
`GenericSkillRegistryAdapter` consumes it directly.

This document is the contract. If your registry returns the shapes described
here, Pantheon will:

1. discover it via a well-known URL,
2. search and paginate through your skill catalog,
3. download skill bundles as `.tar.gz` or `.zip`,
4. run the full security scanner pipeline before activation,
5. verify trust metadata (and signatures, if present).

**Skills vs MCP servers.** Skills are instructions + optional sandboxed
scripts; they live entirely inside Pantheon and run under the skill executor.
MCP servers are long-running processes with their own transport. The two have
separate registries on purpose. If you're publishing a long-running tool
server, see `docs/mcp-registry-protocol.md` instead.

**Non-goals.** This protocol does not cover publishing, skill execution, or
authentication to whatever the skill itself may call out to. It is strictly a
discovery + bundle-download API.

---

## 2. Transport and authentication

- **Transport:** HTTPS only. Plain HTTP is rejected except for `localhost` in
  development mode.
- **Private networks:** RFC1918 addresses and `.internal` / `.corp` / `.lan`
  TLDs are permitted. Pantheon suppresses its usual "unfamiliar domain"
  warnings for registries that were explicitly added by an admin.
- **Content type:** `application/json; charset=utf-8` for metadata endpoints;
  `application/gzip` or `application/zip` for bundle downloads.
- **Authentication options** (the registry declares which it uses in the
  discovery document):
  - `none` — public registry, no credential.
  - `bearer` — `Authorization: Bearer <token>`. Pantheon stores the token in
    its vault, per registry.
  - `mtls` — mutual TLS. Pantheon presents an admin-configured client cert.
- **Rate limiting:** return standard `429 Too Many Requests` with a
  `Retry-After` header. Pantheon honors it.
- **CORS:** not required. Pantheon calls the registry from the backend.

---

## 3. Discovery

Every registry MUST serve a discovery document at:

```
GET /.well-known/pantheon-skill-registry.json
```

Example response:

```json
{
  "protocol_version": "1.0",
  "name": "Acme Internal Skill Registry",
  "description": "Internally approved Pantheon skills for Acme employees",
  "auth": { "type": "bearer" },
  "endpoints": {
    "search":   "/v1/skills",
    "get":      "/v1/skills/{id}",
    "download": "/v1/skills/{id}/bundle",
    "icon":     "/v1/skills/{id}/icon"
  },
  "capabilities": {
    "search_filters": ["tag", "capability", "author"],
    "pagination": "cursor",
    "signing": "sigstore",
    "bundle_formats": ["tar.gz", "zip"]
  },
  "contact": "skills-platform@acme.com"
}
```

Required fields: `protocol_version`, `name`, `auth`, `endpoints.search`,
`endpoints.get`, `endpoints.download`. Endpoint paths are resolved relative
to the discovery URL's origin. `{id}` is a URL-encoded skill ID.

---

## 4. Search endpoint

```
GET {endpoints.search}?q=<query>&tag=<tag>&capability=<cap>&cursor=<opaque>
```

All query parameters are optional. Response:

```json
{
  "results": [ /* SkillListing */ ],
  "next_cursor": "opaque-or-null",
  "total": 42
}
```

`total` is optional. `next_cursor` is `null` when there are no more results.

### 4.1 `SkillListing`

The minimum Pantheon needs to render a search result row.

| Field         | Type     | Required | Notes |
|---------------|----------|----------|-------|
| `id`          | string   | yes      | Stable identifier, URL-safe. |
| `name`        | string   | yes      | Display name (also the installed skill name). |
| `description` | string   | yes      | One-line summary, ≤ 200 chars. |
| `author`      | string   | no       | Team or individual. |
| `version`     | string   | yes      | Date-based (`2026-04-08`) preferred. |
| `tags`        | string[] | no       | Free-form. |
| `capabilities`| string[] | no       | e.g. `network`, `file_read`, `file_write`, `memory_read`. Helps users filter. |
| `triggers_preview` | string[] | no  | A few sample trigger phrases. |
| `approved`    | boolean  | no       | Enterprise trust badge. |
| `updated_at`  | RFC3339  | no       | For "Updated N days ago" display. |

---

## 5. Get endpoint

```
GET {endpoints.get}    (with {id} substituted)
```

Returns a `SkillDetail` — full metadata for the install dialog.

```json
{
  "id": "acme/jql-helper",
  "name": "jql-helper",
  "version": "2026-04-08",
  "description": "Convert natural-language requests into Jira JQL queries",
  "author": "Acme Search Platform",
  "license": "Apache-2.0",
  "homepage": "https://git.acme.internal/skills/jql-helper",
  "readme_md": "## JQL Helper\n\nUse with the Jira MCP for end-to-end search.",
  "instructions_preview": "When the user describes what they're looking for in Jira...",
  "triggers": [
    "find tickets",
    "search jira",
    "what JQL would I use to..."
  ],
  "tags": ["jira", "search", "approved"],
  "capabilities_required": [],
  "parameters": [
    { "name": "project", "type": "string", "required": false, "description": "Jira project key" }
  ],
  "bundle": {
    "format": "tar.gz",
    "size_bytes": 4821,
    "sha256": "9f1c..."
  },
  "trust": {
    "approved_by": "Acme Security",
    "approved_at": "2026-04-01",
    "signature": null
  }
}
```

### 5.1 `bundle` block

| Field        | Type    | Required | Notes |
|--------------|---------|----------|-------|
| `format`     | enum    | yes      | `tar.gz` or `zip`. Must be one of the formats declared in `capabilities.bundle_formats`. |
| `size_bytes` | int     | yes      | Hard cap: 5 MiB. Pantheon refuses larger bundles by default; admins can raise the cap per-registry. |
| `sha256`     | string  | yes      | Hex digest of the bundle. Pantheon verifies it before extraction. |

### 5.2 `trust` block

Optional but recommended.

- `approved_by` / `approved_at` are displayed in the install dialog.
- `signature` — if the discovery doc declared `signing: sigstore`, this is a
  Sigstore bundle. Pantheon verifies it before extraction.

---

## 6. Download endpoint

```
GET {endpoints.download}    (with {id} substituted)
```

Returns the raw bundle bytes with `Content-Type: application/gzip` (for
`tar.gz`) or `application/zip` (for `zip`). Pantheon:

1. streams the bytes to a temp file (capped at the declared `bundle.size_bytes`),
2. verifies the SHA-256 against `bundle.sha256`,
3. extracts using the same `_safe_extract_zip` / `_safe_extract_tar` helpers
   as the local-upload adapter (zip-slip safe, symlinks rejected),
4. runs the full skill scanner (Layers 1-3),
5. installs into `data/skills/` or quarantines on scan failure.

Extracted bundle must contain either a `skill.json` (Pantheon native format)
or a `SKILL.md` (SkillsMP / SkillsLLM frontmatter format) at the bundle root.

---

## 7. Icon endpoint (optional)

```
GET {endpoints.icon}    (with {id} substituted)
```

Returns `image/png` or `image/svg+xml`. Max 256 KiB. Cached for 24 hours.

---

## 8. Versioning and updates

- The `version` string is opaque to Pantheon for comparison; any change
  triggers an "Update available" badge in the installed-skills UI.
- Pantheon polls `get` for installed skills at most once per hour per skill.
- Date-based versions (`2026-04-08`, `2026-04-08.1`) are preferred and match
  Pantheon's own convention.
- Updates are never applied automatically. The user reviews the new detail
  document, sees a diff against the currently installed version, and
  explicitly accepts. If the user has locally evolved the skill since
  install, the conflict resolution flow from Section 11.5 of
  `SKILLS_FEATURE_PLAN.md` applies (accept hub / keep local / AI-assisted
  merge).

---

## 9. Error responses

Standard HTTP status codes. Error body:

```json
{ "error": "short_machine_code", "message": "Human-readable explanation" }
```

Pantheon surfaces `message` to the user verbatim. Do not include secrets or
internal paths.

---

## 10. Conformance checklist

A registry is conformant if it:

- [ ] Serves the discovery document at `/.well-known/pantheon-skill-registry.json`
- [ ] Declares `protocol_version: "1.0"`
- [ ] Implements `search` with `next_cursor` pagination
- [ ] Implements `get` returning a valid `SkillDetail`
- [ ] Implements `download` returning a valid bundle in a declared format
- [ ] Returns a `bundle.sha256` that matches the actual download bytes
- [ ] Honors `429` with `Retry-After` on overload
- [ ] Uses HTTPS (or localhost in dev)
- [ ] Bundle root contains `skill.json` or `SKILL.md`

---

## 11. Adding a registry to Pantheon

Admins add a registry via `pantheon.config.json`:

```json
{
  "skill_registries": [
    {
      "url": "https://skills.acme.internal",
      "auth": { "type": "bearer", "token_ref": "vault:skill_registry_acme" }
    }
  ]
}
```

…or via the Pantheon Settings → Skills → Hubs UI. Pantheon fetches the
discovery document, validates `protocol_version`, and makes the registry
available in the skill importer's "Search Hubs" tab.

The built-in adapters (`GitHubAdapter`, `LocalUploadAdapter`,
`SkillMdAdapter`) remain available regardless of which registries are
configured. They're for one-off imports from sources that don't speak this
protocol; configured registries are for ongoing, browsable, trust-tagged
collections.

---

## 12. Reference implementation

A minimal FastAPI registry is provided at
[`docs/examples/minimal-skill-registry/`](examples/minimal-skill-registry/).
It serves a single skill bundled inline and is intended as a starting point
for enterprise platform teams — clone it, point it at your internal catalog,
and you have a conformant registry in under an hour.

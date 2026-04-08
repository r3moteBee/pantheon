"""Minimal Pantheon Skill Registry — reference implementation.

A ~150-line FastAPI server that implements the Pantheon Skill Registry
Protocol v1.0 (see docs/skill-registry-protocol.md). Serves a single
example skill bundled inline as a tar.gz so platform teams can see the
exact shapes Pantheon expects.

Run:
    pip install fastapi uvicorn
    uvicorn server:app --reload --port 8788

Then in Pantheon: Settings → Skills → Hubs → Add Hub
URL: http://localhost:8788
"""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

app = FastAPI(title="Minimal Pantheon Skill Registry")


# ── Bundle the example skill in-memory ──────────────────────────────────────

EXAMPLE_SKILL_JSON = {
    "name": "jql-helper",
    "description": "Convert natural-language requests into Jira JQL queries",
    "version": "2026-04-08",
    "author": "Acme Search Platform",
    "license": "Apache-2.0",
    "triggers": [
        "find tickets",
        "search jira",
        "what JQL would I use to",
    ],
    "tags": ["jira", "search", "approved"],
    "capabilities_required": [],
    "parameters": [
        {
            "name": "project",
            "type": "string",
            "required": False,
            "description": "Jira project key",
        }
    ],
}

EXAMPLE_INSTRUCTIONS_MD = """# JQL Helper

When the user describes what they're looking for in Jira, translate the
request into a JQL query and explain each clause briefly. If a Jira MCP is
connected, offer to run the query.
"""


def _build_bundle() -> bytes:
    """Return a tar.gz containing skill.json + instructions.md."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        skill_bytes = json.dumps(EXAMPLE_SKILL_JSON, indent=2).encode("utf-8")
        info = tarfile.TarInfo("skill.json")
        info.size = len(skill_bytes)
        tf.addfile(info, io.BytesIO(skill_bytes))

        instr_bytes = EXAMPLE_INSTRUCTIONS_MD.encode("utf-8")
        info = tarfile.TarInfo("instructions.md")
        info.size = len(instr_bytes)
        tf.addfile(info, io.BytesIO(instr_bytes))
    return buf.getvalue()


BUNDLE_BYTES = _build_bundle()
BUNDLE_SHA256 = hashlib.sha256(BUNDLE_BYTES).hexdigest()


# ── Catalog ─────────────────────────────────────────────────────────────────

SKILLS: dict[str, dict[str, Any]] = {
    "acme/jql-helper": {
        "id": "acme/jql-helper",
        "name": "jql-helper",
        "version": "2026-04-08",
        "description": "Convert natural-language requests into Jira JQL queries",
        "author": "Acme Search Platform",
        "license": "Apache-2.0",
        "homepage": "https://git.acme.internal/skills/jql-helper",
        "readme_md": "## JQL Helper\n\nUse with the Jira MCP for end-to-end search.",
        "instructions_preview": EXAMPLE_INSTRUCTIONS_MD[:200],
        "triggers": EXAMPLE_SKILL_JSON["triggers"],
        "tags": ["jira", "search", "approved"],
        "capabilities": [],
        "capabilities_required": [],
        "parameters": EXAMPLE_SKILL_JSON["parameters"],
        "approved": True,
        "updated_at": "2026-04-08T12:00:00Z",
        "bundle": {
            "format": "tar.gz",
            "size_bytes": len(BUNDLE_BYTES),
            "sha256": BUNDLE_SHA256,
        },
        "trust": {
            "approved_by": "Acme Security",
            "approved_at": "2026-04-01",
            "signature": None,
        },
    },
}


def _listing(skill: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "name", "description", "author", "version", "tags",
            "capabilities", "approved", "updated_at")
    out = {k: skill.get(k) for k in keys}
    out["triggers_preview"] = skill.get("triggers", [])[:3]
    return out


# ── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/.well-known/pantheon-skill-registry.json")
def discovery() -> dict[str, Any]:
    return {
        "protocol_version": "1.0",
        "name": "Minimal Example Skill Registry",
        "description": "Reference implementation of the Pantheon Skill Registry Protocol.",
        "auth": {"type": "none"},
        "endpoints": {
            "search": "/v1/skills",
            "get": "/v1/skills/{id}",
            "download": "/v1/skills/{id}/bundle",
        },
        "capabilities": {
            "search_filters": ["tag"],
            "pagination": "cursor",
            "signing": "none",
            "bundle_formats": ["tar.gz"],
        },
        "contact": "example@localhost",
    }


@app.get("/v1/skills")
def search(q: str | None = None, tag: str | None = None) -> dict[str, Any]:
    results = list(SKILLS.values())
    if q:
        ql = q.lower()
        results = [
            s for s in results
            if ql in s["name"].lower() or ql in s["description"].lower()
        ]
    if tag:
        results = [s for s in results if tag in s.get("tags", [])]
    return {
        "results": [_listing(s) for s in results],
        "next_cursor": None,
        "total": len(results),
    }


@app.get("/v1/skills/{skill_id:path}/bundle")
def download(skill_id: str):
    if skill_id not in SKILLS:
        raise HTTPException(404, {"error": "not_found", "message": skill_id})
    return Response(content=BUNDLE_BYTES, media_type="application/gzip")


@app.get("/v1/skills/{skill_id:path}")
def get_skill(skill_id: str) -> dict[str, Any]:
    skill = SKILLS.get(skill_id)
    if not skill:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Unknown skill: {skill_id}"},
        )
    return skill

"""Pydantic models for saved endpoints + role assignments."""
from __future__ import annotations
import re
from typing import Literal
from pydantic import BaseModel, Field, field_validator

# Allowed values — kept here as the source of truth for the API.
ROLES = ("chat", "prefill", "vision", "embed", "rerank")

ApiType = Literal["openai", "anthropic", "ollama", "custom"]
Role = Literal["chat", "prefill", "vision", "embed", "rerank"]


def _slugify_name(s: str) -> str:
    """Lowercase, alphanumeric + dashes, max 40 chars. Mirrors the
    convention used elsewhere (sources/util.py) but kept local to
    avoid the cross-package dependency."""
    out = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return out[:40]


class SavedEndpoint(BaseModel):
    """One configured upstream endpoint. Stored as an entry in the
    `llm_saved_endpoints` JSON array in the vault."""
    name: str = Field(..., min_length=1, max_length=40)
    base_url: str = Field(..., min_length=1)
    api_type: ApiType

    @field_validator("name", mode="before")
    @classmethod
    def _slugify(cls, v: str) -> str:
        slug = _slugify_name(v or "")
        if not slug:
            raise ValueError("name must contain at least one alphanumeric character")
        return slug

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return (v or "").rstrip("/")


class RoleAssignment(BaseModel):
    """A single role → endpoint + model binding."""
    role: Role
    endpoint: str  # endpoint name; empty string means "unassigned"
    model: str  # may be empty when role is unassigned


class EndpointWithKey(SavedEndpoint):
    """Used for create/update — carries the API key in the request body."""
    api_key: str | None = None


class EndpointPublic(SavedEndpoint):
    """What we return from GET endpoints — never includes the api_key."""
    api_key_set: bool


class RoleMappingPayload(BaseModel):
    """PUT /api/llm/roles body — full role map at once."""
    roles: list[RoleAssignment]


# ── Task-class routing ────────────────────────────────────────────────
# Each LLM call site declares what KIND of work it is; each task class
# maps to an ordered list of endpoint+model entries (primary, then
# fallbacks tried on 429/5xx/network errors). Replaces the 5 fixed roles.

TASK_CLASSES: dict[str, dict] = {
    "agent": {
        "label": "Agent (tool use)",
        "description": "Chat, autonomous/scheduled jobs and bots — the tool-calling agent loop.",
        "requires": "tools", "inherits": None, "max_entries": 5,
    },
    "code": {
        "label": "Coding",
        "description": "coding_task jobs, and chat turns with code/tracebacks (chat router). Empty = use Agent.",
        "requires": "tools", "inherits": "agent", "max_entries": 5,
    },
    "quick": {
        "label": "Quick chat",
        "description": "Chat router: short conversational turns with no tool intent. Use a fast tool-capable model. Empty = never routed here.",
        "requires": "tools", "inherits": "agent", "max_entries": 5,
    },
    "long_context": {
        "label": "Long context",
        "description": "Chat router: turns whose history won't fit the Agent model's window. Needs a larger context window. Empty = never routed here.",
        "requires": "tools", "inherits": "agent", "max_entries": 5,
    },
    "extract": {
        "label": "Structured extraction",
        "description": "JSON extraction: ingest topics, memory entities, skill scans. Empty = use Summarize, then Agent.",
        "requires": None, "inherits": "summarize", "max_entries": 5,
    },
    "summarize": {
        "label": "Summarize / write",
        "description": "Consolidation, notes, reports. Empty = use Agent.",
        "requires": None, "inherits": "agent", "max_entries": 5,
    },
    "vision": {
        "label": "Vision",
        "description": "Image understanding (uploaded images, OCR assist). Optional.",
        "requires": "vision", "inherits": None, "max_entries": 5,
    },
    "image_gen": {
        "label": "Image generation",
        "description": "generate_image tool — OpenAI-compatible /images/generations. Optional.",
        "requires": "image_gen", "inherits": None, "max_entries": 5,
    },
    "embed": {
        "label": "Embeddings",
        "description": "Semantic memory. Exactly one model — switching invalidates stored vectors, so no fallback.",
        "requires": "embedding", "inherits": None, "max_entries": 1,
    },
    "rerank": {
        "label": "Reranker",
        "description": "Re-orders retrieval results. Optional.",
        "requires": None, "inherits": None, "max_entries": 1,
    },
}

TaskClass = Literal["agent", "code", "quick", "long_context", "extract", "summarize", "vision", "image_gen", "embed", "rerank"]

# Legacy role -> task class (for resolve_role() back-compat and migration).
ROLE_TO_CLASS = {"chat": "agent", "prefill": "summarize", "vision": "vision",
                 "embed": "embed", "rerank": "rerank"}


class RouteEntry(BaseModel):
    endpoint: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)


class RoutesPayload(BaseModel):
    """PUT /api/llm/routes — full routing table. Classes left out are cleared."""
    routes: dict[TaskClass, list[RouteEntry]]


class ModelProfile(BaseModel):
    """What a model can do. Seeded from llm_config.known_models, editable."""
    context_window: int | None = None
    tools: bool = False
    vision: bool = False
    image_gen: bool = False
    embedding: bool = False
    tier: Literal["fast", "standard", "frontier"] = "standard"
    notes: str = ""
    # user = edited in the UI; endpoint = advertised by the model server's
    # /models (or Ollama /api/show); known = guessed from the model name.
    source: Literal["known", "user", "endpoint", "unknown"] = "unknown"


class ProfilesPayload(BaseModel):
    """PUT /api/llm/profiles — user edits keyed "endpoint/model"."""
    profiles: dict[str, ModelProfile]

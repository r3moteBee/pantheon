"""Pydantic models for saved endpoints + role assignments."""
from __future__ import annotations
import re
from typing import Literal
from pydantic import BaseModel, Field, field_validator

# Allowed values — kept here as the source of truth so the API and
# frontend can read the same enums.
API_TYPES = ("openai", "anthropic", "ollama", "custom")
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

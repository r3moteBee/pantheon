"""Vault-backed channel→project mapping store.

All mappings are persisted as a JSON blob in the secrets vault under the key
``messaging_channel_mappings``.  The default project is stored under
``messaging_default_project``.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

from messaging.models import ChannelMapping

logger = logging.getLogger(__name__)

_DEFAULT_PROJECT = "default"


class ChannelStore:
    """CRUD for channel→project mappings backed by the Pantheon vault."""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _vault():  # noqa: ANN205
        from secrets.vault import get_vault
        return get_vault()

    def _load_mappings(self) -> dict[str, dict[str, Any]]:
        """Return the raw mapping dict ``{channel_id: {...}}``."""
        raw = self._vault().get_secret("messaging_channel_mappings")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Corrupt messaging_channel_mappings in vault, resetting.")
            return {}

    def _save_mappings(self, data: dict[str, dict[str, Any]]) -> None:
        self._vault().set_secret("messaging_channel_mappings", json.dumps(data))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_mappings(self) -> list[ChannelMapping]:
        """Return all stored channel→project mappings."""
        raw = self._load_mappings()
        result: list[ChannelMapping] = []
        for cid, info in raw.items():
            result.append(ChannelMapping(
                channel_id=cid,
                platform=info.get("platform", cid.split(":")[0] if ":" in cid else "unknown"),
                channel_name=info.get("channel_name", ""),
                project_id=info.get("project_id", _DEFAULT_PROJECT),
            ))
        return result

    def get_mapping(self, channel_id: str) -> ChannelMapping | None:
        """Return the mapping for a single channel, or ``None``."""
        raw = self._load_mappings()
        info = raw.get(channel_id)
        if info is None:
            return None
        return ChannelMapping(
            channel_id=channel_id,
            platform=info.get("platform", channel_id.split(":")[0] if ":" in channel_id else "unknown"),
            channel_name=info.get("channel_name", ""),
            project_id=info.get("project_id", _DEFAULT_PROJECT),
        )

    def set_mapping(
        self,
        channel_id: str,
        project_id: str,
        *,
        platform: str = "",
        channel_name: str = "",
    ) -> None:
        """Create or update a channel→project mapping."""
        raw = self._load_mappings()
        existing = raw.get(channel_id, {})
        raw[channel_id] = {
            "platform": platform or existing.get("platform", channel_id.split(":")[0] if ":" in channel_id else "unknown"),
            "channel_name": channel_name or existing.get("channel_name", ""),
            "project_id": project_id,
        }
        self._save_mappings(raw)
        logger.info("Channel mapping set: %s → %s", channel_id, project_id)

    def remove_mapping(self, channel_id: str) -> bool:
        """Remove a mapping.  Returns True if it existed."""
        raw = self._load_mappings()
        if channel_id not in raw:
            return False
        del raw[channel_id]
        self._save_mappings(raw)
        logger.info("Channel mapping removed: %s", channel_id)
        return True

    def bulk_update(self, mappings: list[dict[str, str]]) -> None:
        """Replace / merge a batch of mappings.

        Each item must have ``channel_id`` and ``project_id``;
        ``platform`` and ``channel_name`` are optional.
        """
        raw = self._load_mappings()
        for m in mappings:
            cid = m["channel_id"]
            existing = raw.get(cid, {})
            raw[cid] = {
                "platform": m.get("platform", existing.get("platform", "")),
                "channel_name": m.get("channel_name", existing.get("channel_name", "")),
                "project_id": m["project_id"],
            }
        self._save_mappings(raw)

    # ------------------------------------------------------------------
    # Default project
    # ------------------------------------------------------------------

    def get_default_project(self) -> str:
        return self._vault().get_secret("messaging_default_project") or _DEFAULT_PROJECT

    def set_default_project(self, project_id: str) -> None:
        self._vault().set_secret("messaging_default_project", project_id)
        logger.info("Default messaging project set to: %s", project_id)

    # ------------------------------------------------------------------
    # Resolution (used at message-receive time)
    # ------------------------------------------------------------------

    def resolve(self, platform: str, raw_channel_id: str) -> str:
        """Return the project ID for a channel, falling back to the default."""
        prefixed = f"{platform}:{raw_channel_id}"
        raw = self._load_mappings()
        info = raw.get(prefixed)
        if info and info.get("project_id"):
            return info["project_id"]
        return self.get_default_project()


@lru_cache()
def get_channel_store() -> ChannelStore:
    """Return the singleton :class:`ChannelStore`."""
    return ChannelStore()

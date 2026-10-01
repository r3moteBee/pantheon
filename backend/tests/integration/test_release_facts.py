"""web_search appends endoflife.date release tables for version questions."""
from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from agent.release_facts import format_cycles, match_products, release_facts  # noqa: E402

PRODUCTS = ["python", "nodejs", "postgresql", "go", "linux", "amazon-linux", "home-assistant", "docker-engine", "kubernetes"]


def test_products_are_matched_by_their_names_and_common_aliases():
    assert match_products("latest stable version of Python", PRODUCTS) == ["python"]
    assert match_products("Node.js LTS latest version 2026", PRODUCTS) == ["nodejs"]
    assert match_products("latest Go (golang) release", PRODUCTS) == ["go"]
    assert match_products("latest stable Linux kernel version", PRODUCTS) == ["linux"]
    assert match_products("is home assistant 2026.10 out yet", PRODUCTS) == ["home-assistant"]
    assert match_products("postgres latest version", PRODUCTS) == ["postgresql"]
    assert match_products("amazon linux latest", PRODUCTS) == ["amazon-linux"]
    assert match_products("best pizza in Lisbon", PRODUCTS) == []


def test_lts_only_counts_once_its_date_has_passed():
    future = (date.today() + timedelta(days=20)).isoformat()
    past = (date.today() - timedelta(days=400)).isoformat()
    cycles = [{"cycle": "26", "latest": "26.10.0", "lts": future, "eol": "2029-04-30"},
              {"cycle": "25", "latest": "25.9.0", "lts": False, "eol": past},
              {"cycle": "24", "latest": "24.21.0", "lts": past, "eol": "2028-04-30"}]
    out = format_cycles("nodejs", cycles)
    assert "newest release: 26.10.0" in out and "newest LTS: 24.21.0" in out
    assert "cycle 25: latest 25.9.0 (end of life)" in out


@pytest.mark.asyncio
async def test_only_version_questions_trigger_a_lookup():
    import agent.release_facts as rf
    get = AsyncMock(side_effect=lambda path, ttl: PRODUCTS if path == "all.json" else [{"cycle": "18", "latest": "18.6"}])
    with patch.object(rf, "_get_json", get):
        assert "newest release: 18.6" in await release_facts("latest stable version of PostgreSQL")
        assert await release_facts("PostgreSQL tutorial for beginners") == ""
    with patch.object(rf, "_get_json", AsyncMock(side_effect=OSError("offline"))):
        assert await release_facts("latest postgres version") == ""        # failures add nothing


@pytest.mark.asyncio
async def test_web_search_puts_release_data_before_the_snippets():
    from agent.tools import web
    with patch.object(web, "_web_search", AsyncMock(return_value="1. Old blog\n   https://x\n   PostgreSQL 19 released")), \
         patch("agent.release_facts.release_facts", AsyncMock(return_value="Release data from endoflife.date/postgresql ...")):
        out = await web._tool_web_search(None, "web_search", {"query": "latest postgres"})
    assert out.index("Release data") < out.index("Old blog") and "snippets may be outdated" in out

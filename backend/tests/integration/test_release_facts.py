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


# ── GitHub releases ──────────────────────────────────────────────────────────

from agent.release_facts import format_github, github_release_facts, repo_candidates  # noqa: E402

RESULTS = """1. Releases · ollama/ollama
   https://github.com/ollama/ollama/releases
   v0.35.1-rc0 ...
2. Ollama blog
   https://ollama.com/blog
3. some fork
   https://github.com/someone/unrelated-tool"""


def test_repo_candidates_come_from_result_links_named_in_the_query():
    assert repo_candidates("Ollama latest stable version 2026", RESULTS) == ["ollama/ollama"]
    assert repo_candidates("Uptime Kuma latest version", "https://github.com/louislam/uptime-kuma") == ["louislam/uptime-kuma"]
    assert repo_candidates("Home Assistant latest", "https://github.com/home-assistant/core/releases") == ["home-assistant/core"]
    assert repo_candidates("Ollama latest", "https://github.com/topics/llm") == []


def test_prereleases_are_never_the_answer():
    rel = [{"tag_name": "v0.35.1-rc0", "prerelease": True, "published_at": "2026-09-29T20:14:22Z"},
           {"tag_name": "v0.35.0", "prerelease": False, "published_at": "2026-09-28T21:23:22Z"},
           {"tag_name": "v0.40.0-rc0", "prerelease": True, "published_at": "2026-09-25T03:31:52Z"},
           {"tag_name": "v0.34.4", "prerelease": False, "published_at": "2026-09-23T02:24:43Z"},
           {"tag_name": "v0.36.0", "prerelease": False, "draft": True, "published_at": None}]
    out = format_github("ollama/ollama", rel)
    assert "newest stable release: v0.35.0 (published 2026-09-28)" in out
    assert "newer pre-releases (NOT stable): v0.35.1-rc0" in out and "v0.40.0-rc0" not in out.split("NOT stable")[1].split("\n")[0]
    assert "v0.36.0" not in out


@pytest.mark.asyncio
async def test_github_lookup_falls_back_to_repo_search_and_never_raises():
    import agent.release_facts as rf
    async def gh(path, ttl):
        if path.startswith("search/"):
            return {"items": [{"name": "jellyfin", "owner": {"login": "jellyfin"}, "stargazers_count": 40000}]}
        return [{"tag_name": "v12.1", "prerelease": False, "published_at": "2026-09-15T01:23:53Z"}]
    with patch.object(rf, "_gh_json", gh):
        assert "newest stable release: v12.1" in await github_release_facts("Jellyfin latest stable version", "no links here")
        assert await github_release_facts("Jellyfin installation guide", "") == ""          # no version intent
    with patch.object(rf, "_gh_json", AsyncMock(side_effect=OSError("rate limited"))):
        assert await github_release_facts("Jellyfin latest version", "") == ""

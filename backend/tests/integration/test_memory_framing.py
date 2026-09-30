"""Recalled memory is labelled by provenance, and never overrides tools for current facts."""
from __future__ import annotations

from agent.prompts import render_memory_section


def test_nothing_recalled_renders_nothing():
    assert render_memory_section(None) == ""
    assert render_memory_section([{"tier": "semantic", "content": "  "}]) == ""


def test_items_are_labelled_by_provenance():
    out = render_memory_section([
        {"tier": "semantic", "content": "User's dog is called Rufus"},
        {"tier": "episodic", "content": "[user] our Q3 budget is 42,000 euros"},
        {"tier": "episodic", "content": "[assistant] The latest Python is 3.12.7"},
        {"tier": "graph", "content": "[graph:vendor] Acme"},
    ])
    assert "[note] User's dog is called Rufus" in out
    assert "[user said] our Q3 budget is 42,000 euros" in out
    assert "[your earlier reply] The latest Python is 3.12.7" in out
    assert "[graph] [graph:vendor] Acme" in out


def test_guidance_keeps_corpus_authority_but_not_over_tools():
    out = render_memory_section([{"tier": "semantic", "content": "x"}])
    assert "authoritative" in out                       # corpus-grounded research keeps working
    assert "use your tools even when a memory" in out   # current facts still go to tools
    assert "primary source" not in out.lower()          # the old blanket instruction is gone
    assert "only use web search" not in out.lower()

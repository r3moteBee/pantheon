"""Personas collapsed to soul presets: applying keeps Key Commitments,
Reset drops a project override, and the one-shot migration reverts
untouched auto-applied Pan overrides."""
from __future__ import annotations

import json
import os
import tempfile

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))


@pytest.fixture
def pdirs(monkeypatch, tmp_path):
    from agent import personality as pers
    s = pers.settings.model_copy(update={"data_dir": tmp_path / "data"})
    monkeypatch.setattr(pers, "settings", s)
    (tmp_path / "data" / "personality").mkdir(parents=True)
    return pers, tmp_path / "data"


def _bundled_soul():
    from agent.personality import _load_template
    return _load_template("soul.md")


def test_key_commitments_come_from_the_global_soul(pdirs):
    pers, data = pdirs
    kc = pers.key_commitments()
    assert kc.startswith("## Key Commitments") and "never fabricate" in kc
    assert "keeper" not in kc and "You are Pan" not in kc
    # A user-edited global soul wins.
    (data / "personality" / "soul.md").write_text("# Me\n\n## Key Commitments\n\n- Cite every source.\n\nOutro.")
    assert pers.key_commitments() == "## Key Commitments\n\n- Cite every source."
    out = pers.with_commitments("# The Soul of Hermes\n\nFast.")
    assert out.endswith("- Cite every source.\n") and out.startswith("# The Soul of Hermes")
    assert pers.with_commitments(out) == out


def test_apply_keeps_commitments_and_reset_follows_global(pdirs, monkeypatch):
    import asyncio
    pers, data = pdirs
    from api import personas, personality as papi
    monkeypatch.setattr(personas, "settings", pers.settings)
    (data / "db").mkdir()
    (data / "db" / "projects.json").write_text(json.dumps({"p1": {"name": "P1"}}))
    asyncio.run(personas.apply_persona("hermes", "p1"))
    soul = (data / "projects" / "p1" / "personality" / "soul.md").read_text()
    assert "Hermes" in soul and "## Key Commitments" in soul
    assert json.loads((data / "db" / "projects.json").read_text())["p1"]["persona_id"] == "hermes"

    asyncio.run(papi.reset_personality(project_id="p1"))
    assert not (data / "projects" / "p1" / "personality" / "soul.md").exists()
    assert "persona_id" not in json.loads((data / "db" / "projects.json").read_text())["p1"]


def test_migration_reverts_untouched_pan_and_repairs_presets(pdirs):
    pers, data = pdirs
    bundled = _bundled_soul()
    sec = pers._section(bundled, "## Key Commitments")
    pan = bundled.replace(sec, "").replace("\n\n\n", "\n\n")      # what "Pan" wrote
    hermes = "# The Soul of Hermes\n\nMove fast, cite sources."
    edited = pan + "\n\nAlways answer in French."

    def put(pid, text):
        d = data / "projects" / pid / "personality"
        d.mkdir(parents=True)
        (d / "soul.md").write_text(text)
        return d / "soul.md"

    a, b, c = put("a", pan), put("b", hermes), put("c", edited)
    projects = {"a": {"persona_id": "pan"}, "b": {"persona_id": "hermes"}, "c": {"persona_id": "pan"}}
    report = pers.migrate_persona_overrides(projects, {"hermes": hermes})
    assert report == {"reverted": ["a"], "commitments_added": ["b"]}
    assert not a.exists() and "persona_id" not in projects["a"]
    assert "## Key Commitments" in b.read_text()
    assert c.read_text() == edited and projects["c"]["persona_id"] == "pan"   # user edits untouched


def test_pan_is_no_longer_a_preset():
    from api.personas import bundled_preset_souls
    ids = bundled_preset_souls()
    assert "pan" not in ids and "hermes" in ids


def test_system_prompt_has_no_custom_soul_path():
    import inspect
    from agent.prompts import build_system_prompt
    assert "custom_soul" not in inspect.signature(build_system_prompt).parameters

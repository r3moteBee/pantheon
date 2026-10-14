"""Settings → About you: profile text, reply style per channel, writing voices.

Run: pytest backend/tests/integration/test_about_you.py -v
"""
from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))
os.environ.setdefault("AUTH_PASSWORD", "")


@pytest.fixture
def profile_db(monkeypatch, tmp_path):
    """A fresh user_profile.db per test."""
    import config
    real = config.get_settings()
    fake = real.model_copy(update={"data_dir": tmp_path / "data"})
    monkeypatch.setattr(config, "get_settings", lambda: fake)
    from utils import user_profile
    user_profile._ready.clear()
    return user_profile


@pytest.fixture
def client(profile_db):
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app)


# ── Settings + reply style ───────────────────────────────────────────────────

def test_defaults_add_nothing_to_the_prompt(profile_db):
    up = profile_db
    p = up.get_profile()
    assert p == {"about_you": "",
                 "reply_length": {"global": "default", "channels": {}},
                 "formatting": {"global": "auto", "channels": {}}}
    assert up.render_profile_section() == ""
    # Web chat renders Markdown and the length is the agent's own: nothing to say.
    assert up.render_reply_style("web") == ""


def test_auto_formatting_is_plain_where_markdown_is_not_rendered(profile_db):
    up = profile_db
    for ch in ("telegram", "slack", "matrix"):
        assert up.reply_style(ch) == {"length": "default", "plain": True}
        text = up.render_reply_style(ch)
        assert "PLAIN TEXT" in text and up.CHANNEL_LABELS[ch] in text
    for ch in ("web", "discord", "mattermost"):
        assert up.reply_style(ch)["plain"] is False
    assert up.reply_style(None) is None
    assert up.render_reply_style(None) == ""


def test_channel_overrides_win_over_global(profile_db):
    up = profile_db
    up.update_profile({
        "reply_length": {"global": "detailed", "channels": {"telegram": "short", "slack": ""}},
        "formatting": {"global": "plain", "channels": {"discord": "markdown"}},
    })
    assert up.reply_style("telegram") == {"length": "short", "plain": True}
    assert up.reply_style("slack") == {"length": "detailed", "plain": True}
    assert up.reply_style("discord") == {"length": "detailed", "plain": False}
    # the web chat always renders Markdown: formatting doesn't apply there
    assert up.reply_style("web") == {"length": "detailed", "plain": False}
    assert "PLAIN TEXT" not in up.render_reply_style("web")
    tg = up.render_reply_style("telegram")
    assert "SHORT" in tg and "PLAIN TEXT" in tg
    assert "DETAILED" in up.render_reply_style("discord")
    # an empty channel value means "same as default" and is not stored
    assert up.get_profile()["reply_length"]["channels"] == {"telegram": "short"}


def test_formatting_markdown_overrides_a_plain_text_channel(profile_db):
    up = profile_db
    up.update_profile({"formatting": {"global": "auto", "channels": {"telegram": "markdown"}}})
    assert up.reply_style("telegram")["plain"] is False


@pytest.mark.parametrize("changes", [
    {"reply_length": {"global": "huge"}},
    {"formatting": {"global": "auto", "channels": {"email": "plain"}}},
    {"formatting": {"global": "auto", "channels": {"telegram": "bold"}}},
    {"formatting": {"global": "auto", "channels": {"web": "plain"}}},
    {"about_you": "x" * 4001},
    {"about_you": 5},
    {"favourite_colour": "blue"},
])
def test_invalid_settings_are_rejected(profile_db, changes):
    with pytest.raises(profile_db.ProfileError):
        profile_db.update_profile(changes)
    assert profile_db.get_profile()["about_you"] == ""


def test_partial_update_keeps_other_settings(profile_db):
    up = profile_db
    up.update_profile({"reply_length": "short"})          # plain string = global
    up.update_profile({"about_you": "  I run a bakery.  "})
    p = up.get_profile()
    assert p["about_you"] == "I run a bakery."
    assert p["reply_length"]["global"] == "short"


def test_about_you_and_voices_render_in_the_profile_section(profile_db):
    up = profile_db
    up.update_profile({"about_you": "Network engineer in Ohio. Skip the basics."})
    text = up.render_profile_section()
    assert text.startswith("## About the user") and "Skip the basics." in text
    assert "## Writing voices" not in text
    up.create_voice("Blog", is_default=True)
    up.create_voice("Work email")
    text = up.render_profile_section()
    assert '"Blog" (default), "Work email"' in text
    assert 'writing_voice(action="get")' in text


# ── System prompt ────────────────────────────────────────────────────────────

def test_system_prompt_places_profile_and_reply_style(profile_db):
    from agent.prompts import build_system_prompt
    profile_db.update_profile({"about_you": "I keep bees.", "reply_length": "short"})
    web = build_system_prompt(project_id="default", project_name="Default", channel="web")
    assert "## About the user" in web and "I keep bees." in web
    # About the user sits right after the agent guide, before the project section
    assert web.index("## About the user") < web.index("## Active Project")
    # reply style is the last section of the prompt
    assert web.rstrip().endswith(profile_db.REPLY_LENGTH_TEXT["short"])
    tg = build_system_prompt(project_id="default", channel="telegram")
    assert "PLAIN TEXT (Telegram)" in tg
    job = build_system_prompt(project_id="default", channel=None)
    assert "I keep bees." in job and "## Reply style" not in job


def test_system_prompt_survives_a_broken_profile_store(profile_db):
    from agent.prompts import build_system_prompt
    with patch("utils.user_profile.get_profile", side_effect=RuntimeError("db gone")):
        text = build_system_prompt(project_id="default", channel="web")
    assert "## Recalled memory" in text and "## About the user" not in text


@pytest.mark.asyncio
async def test_agent_core_passes_its_channel_to_the_prompt(profile_db):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from agent.core import AgentCore
    captured = {}

    def fake_prompt(**kw):
        captured.update(kw)
        return "sys"

    class Prov:
        model = "m"
        async def chat(self, messages, tools=None, stream=True):
            yield {"type": "text_delta", "content": "hi"}
            yield {"type": "done"}

    mem = SimpleNamespace(recall=AsyncMock(return_value=[]))
    agent = AgentCore(provider=Prov(), memory_manager=mem, project_id="p", session_id="s", channel="telegram")
    with patch("agent.core.build_system_prompt", side_effect=fake_prompt):
        async for _ in agent.chat("hello", stream=True):
            pass
    assert captured["channel"] == "telegram"


def test_every_agent_entry_point_names_its_channel():
    """Chat and each messaging adapter must say where its replies go, or the
    reply style silently never applies there."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    assert 'channel="web"' in (root / "api" / "chat.py").read_text()
    for name in ("telegram", "discord", "slack", "matrix", "mattermost"):
        src = (root / "messaging" / "adapters" / f"{name}.py").read_text()
        assert f'channel="{name}"' in src, name


# ── Plain text for chat apps that show raw Markdown ──────────────────────────

def test_markdown_to_plain():
    from utils.plain_text import markdown_to_plain
    md = (
        "# ZFS vs Btrfs\n\n"
        "| Feature | ZFS | Btrfs |\n|---|---|---|\n| **Integrity** | every block | metadata |\n\n"
        "---\n"
        "**ZFS** is *great*, see [the docs](https://openzfs.org) or https://x.y/a_b_c.\n"
        "* item with snake_case_name\n"
        "```bash\nzpool create tank   # **kept**\n```\n"
        "> quoted _text_"
    )
    out = markdown_to_plain(md)
    assert out.startswith("ZFS vs Btrfs:")
    assert "- Integrity — ZFS: every block; Btrfs: metadata" in out
    assert "ZFS is great, see the docs (https://openzfs.org) or https://x.y/a_b_c." in out
    assert "- item with snake_case_name" in out
    assert "zpool create tank   # **kept**" in out and "```" not in out
    assert "quoted text" in out
    assert "|" not in out.replace("# **kept**", "") and "#" not in out.replace("# **kept**", "")
    assert markdown_to_plain("") == ""


@pytest.mark.asyncio
async def test_bot_replies_are_converted_to_plain_text(profile_db):
    from agent.core import AgentCore

    async def fake_chat(self, message, stream=True):
        yield {"type": "done", "full_response": "## Answer\n**Yes** - use [ZFS](https://openzfs.org)."}

    with patch.object(AgentCore, "chat", fake_chat):
        tg = await AgentCore(provider=None, channel="telegram").run_autonomous("q")
        dc = await AgentCore(provider=None, channel="discord").run_autonomous("q")
        job = await AgentCore(provider=None).run_autonomous("q")
        profile_db.update_profile({"formatting": {"global": "auto", "channels": {"telegram": "markdown"}}})
        tg_md = await AgentCore(provider=None, channel="telegram").run_autonomous("q")
    assert tg == "Answer:\nYes - use ZFS (https://openzfs.org)."
    assert dc.startswith("## Answer") and job.startswith("## Answer") and tg_md.startswith("## Answer")


def test_reply_style_note(profile_db):
    up = profile_db
    assert up.reply_style_note("web") == "" and up.reply_style_note(None) == ""
    assert "PLAIN TEXT" in up.reply_style_note("telegram")
    up.update_profile({"reply_length": "short"})
    note = up.reply_style_note("web")
    assert note.startswith("[Pantheon note]") and "SHORT" in note and "PLAIN" not in note


# ── Writing voices ───────────────────────────────────────────────────────────

def test_voice_crud_and_single_default(profile_db):
    up = profile_db
    a = up.create_voice("Blog", "Dry humour. Never use: delve.", is_default=True)
    b = up.create_voice("Newsletter", is_default=True)
    voices = {v["name"]: v for v in up.list_voices()}
    assert voices["Newsletter"]["is_default"] and not voices["Blog"]["is_default"]
    with pytest.raises(up.ProfileError):
        up.create_voice("blog")                      # names are case-insensitive unique
    with pytest.raises(up.ProfileError):
        up.update_voice(b["id"], {"name": "BLOG"})
    up.update_voice(a["id"], {"is_default": True, "notes": "Updated"})
    assert [v["name"] for v in up.list_voices() if v["is_default"]] == ["Blog"]
    assert up.get_voice(a["id"])["notes"] == "Updated"
    up.delete_voice(a["id"])
    with pytest.raises(up.NotFound):
        up.get_voice(a["id"])


def test_samples_cascade_and_word_counts(profile_db):
    up = profile_db
    v = up.create_voice("Blog")
    s = up.add_sample(v["id"], "", "one two  three\nfour", "https://example.com/post")
    assert s["word_count"] == 4 and s["title"] == "one two three four"
    assert up.get_voice(v["id"])["total_words"] == 4
    with pytest.raises(up.ProfileError):
        up.add_sample(v["id"], "t", "   ")
    with pytest.raises(up.ProfileError):
        up.add_sample(v["id"], "t", "text", "javascript:alert(1)")
    up.delete_voice(v["id"])
    with up._db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM writing_voice_samples").fetchone()[0] == 0


def test_resolve_voice_rules(profile_db):
    up = profile_db
    voice, msg = up.resolve_voice(None)
    assert voice is None and "Settings → About you" in msg
    up.create_voice("Blog")
    assert up.resolve_voice(None)[0]["name"] == "Blog"            # the only voice
    up.create_voice("Work email")
    voice, msg = up.resolve_voice(None)
    assert voice is None and "none is the default" in msg         # ambiguous: ask
    assert up.resolve_voice("work")[0]["name"] == "Work email"    # unique partial match
    voice, msg = up.resolve_voice("speech")
    assert voice is None and "Ask the user" in msg and '"Blog"' in msg
    up.create_voice("Speech", is_default=True)
    assert up.resolve_voice(None)[0]["name"] == "Speech"


@pytest.mark.asyncio
async def test_writing_voice_tool(profile_db):
    from agent.tools import execute_tool
    up = profile_db
    assert "no writing voices" in await execute_tool("writing_voice", {"action": "list"}, None)
    v = up.create_voice("Blog", "Short paragraphs.\nNever use: leverage.", is_default=True)
    long = "word " * 600
    up.add_sample(v["id"], "Old post", "the oldest one")
    for i in range(3):
        up.add_sample(v["id"], f"Post {i}", long)
    out = await execute_tool("writing_voice", {}, None)               # action defaults to get
    assert out.startswith("# Writing voice: Blog")
    assert "> Never use: leverage." in out
    assert "3 newest of 4" in out and 'Other samples: "Old post"' in out
    assert "[…]" in out                                               # long samples are excerpted
    assert "Never copy sentences" in out
    full = await execute_tool("writing_voice", {"action": "sample", "name": "blog", "sample": "old"}, None)
    assert "the oldest one" in full and "[…]" not in full
    listing = await execute_tool("writing_voice", {"action": "list"}, None)
    assert '"Blog" (default): 4 samples' in listing
    assert "Ask the user" in await execute_tool("writing_voice", {"name": "Speech"}, None)


def test_drafting_requests_read_the_voice_first(profile_db):
    up = profile_db
    msg = "Write a short post about moving my DNS to Pi-hole."
    assert up.drafting_voice_args(msg) is None                    # no voices: nothing to read
    up.create_voice("Blog", is_default=True)
    up.create_voice("Work email")
    assert up.drafting_voice_args(msg) == {"action": "get"}       # the default voice
    # a voice named in the request is the one to use ("blog post" names the Blog voice)
    assert up.drafting_voice_args("Write a blog post about Pi-hole") == {"action": "get", "name": "Blog"}
    assert up.drafting_voice_args("Draft an email to Sam in my work email voice") == \
        {"action": "get", "name": "Work email"}
    assert up.drafting_voice_args("Can you put together a LinkedIn announcement for the launch?")
    for not_drafting in ("What is a VLAN?", "Summarize this blog post", "Who wrote the letter to the editor?",
                         "write_file failed again", ""):
        assert up.drafting_voice_args(not_drafting) is None, not_drafting


@pytest.mark.asyncio
async def test_agent_reads_the_voice_before_round_one(profile_db):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from agent.core import AgentCore
    v = profile_db.create_voice("Blog", "Dry humour.", is_default=True)
    profile_db.add_sample(v["id"], "Post", "It worked. It was boring.")
    seen = []

    class Prov:
        model = "m"
        async def chat(self, messages, tools=None, stream=True, **kw):
            seen.append(messages)
            yield {"type": "text_delta", "content": "Here it is."}
            yield {"type": "done"}

    agent = AgentCore(provider=Prov(), memory_manager=SimpleNamespace(recall=AsyncMock(return_value=[])),
                      project_id="p", session_id="s", channel="web")
    events = [e async for e in agent.chat("Write a short post about my new NAS", stream=True)]
    calls = [e for e in events if e["type"] == "tool_call" and e["name"] == "writing_voice"]
    assert len(calls) == 1 and calls[0]["args"] == {"action": "get"}
    tool_msgs = [m for m in seen[0] if m.get("role") == "tool"]
    assert any("# Writing voice: Blog" in m["content"] and "It worked." in m["content"] for m in tool_msgs)


def test_writing_voice_tool_is_offered():
    from agent.tools import TOOL_SCHEMAS
    assert "writing_voice" in {t["function"]["name"] for t in TOOL_SCHEMAS}


# ── API ──────────────────────────────────────────────────────────────────────

def test_about_you_api_round_trip(client):
    r = client.get("/api/about-you")
    assert r.status_code == 200
    body = r.json()
    assert [c["id"] for c in body["channels"]][0] == "web"
    assert body["limits"]["about_you"] == 4000
    r = client.put("/api/about-you", json={"about_you": "Hi", "formatting": {"global": "plain", "channels": {}}})
    assert r.status_code == 200 and r.json()["formatting"]["global"] == "plain"
    assert client.put("/api/about-you", json={"reply_length": {"global": "epic"}}).status_code == 400


def test_writing_voices_api(client):
    r = client.post("/api/writing-voices", json={"name": "Blog", "notes": "n"})
    assert r.status_code == 201
    vid = r.json()["id"]
    assert client.post("/api/writing-voices", json={"name": "blog"}).status_code == 400
    r = client.post(f"/api/writing-voices/{vid}/samples", json={"title": "A", "content": "some text here"})
    assert r.status_code == 201
    sid = r.json()["id"]
    v = client.get(f"/api/writing-voices/{vid}").json()
    assert v["sample_count"] == 1 and v["samples"][0]["preview"] == "some text here"
    assert "content" not in v["samples"][0]
    assert client.get(f"/api/writing-voices/{vid}/samples/{sid}").json()["content"] == "some text here"
    assert client.patch(f"/api/writing-voices/{vid}", json={"is_default": True}).json()["is_default"] is True
    assert client.patch(f"/api/writing-voices/{vid}", json={"colour": "red"}).status_code == 400
    assert client.delete(f"/api/writing-voices/{vid}/samples/{sid}").status_code == 204
    assert client.delete(f"/api/writing-voices/{vid}").status_code == 204
    assert client.get(f"/api/writing-voices/{vid}").status_code == 404
    assert client.post("/api/writing-voices/nope/samples", json={"content": "x"}).status_code == 404

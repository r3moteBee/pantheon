"""About you: the user's own profile — the one read/write path.

Pantheon runs one instance per person, so this is global (not per project):
  about_you      — free text the user writes about themselves (system prompt)
  reply_length   — short|default|detailed, with per-channel overrides
  formatting     — auto|markdown|plain, with per-channel overrides
                   (auto = Markdown where the channel renders it)
  writing voices — named writing styles: notes + pasted samples, read by the
                   ``writing_voice`` tool when the agent drafts for the user

Everything lives in ``data/db/user_profile.db``. Settings are JSON values in
``profile_settings``; channel-overridable ones are stored as
``{"global": v, "channels": {"telegram": v}}``.

Prompt text is rendered here (``render_profile_section``,
``render_reply_style``) so build_system_prompt only places it. Both are
stable between turns (they change only when the user edits Settings), so
they don't break the prompt-prefix cache.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Where replies are delivered. "web" is the chat UI; the rest are the
# messaging adapters (messaging/adapters/). Background jobs have no channel.
CHANNELS = ("web", "telegram", "discord", "slack", "matrix", "mattermost")
CHANNEL_LABELS = {
    "web": "Web chat", "telegram": "Telegram", "discord": "Discord",
    "slack": "Slack", "matrix": "Matrix", "mattermost": "Mattermost",
}
# Whether the channel shows Markdown as formatting. The adapters send plain
# text: Telegram (no parse_mode) and Matrix (no formatted_body) show the raw
# symbols, and Slack's mrkdwn is not Markdown (no headers, *bold*, no tables).
RENDERS_MARKDOWN = {
    "web": True, "discord": True, "mattermost": True,
    "slack": False, "telegram": False, "matrix": False,
}

REPLY_LENGTHS = ("short", "default", "detailed")
FORMATTINGS = ("auto", "markdown", "plain")
CHANNEL_SETTINGS = {
    "reply_length": {"default": "default", "choices": REPLY_LENGTHS},
    "formatting": {"default": "auto", "choices": FORMATTINGS},
}

ABOUT_MAX_CHARS = 4000
VOICE_NAME_MAX = 64
VOICE_NOTES_MAX = 4000
SAMPLE_TITLE_MAX = 200
SAMPLE_MAX_CHARS = 100_000
MAX_VOICES = 20
MAX_SAMPLES_PER_VOICE = 30
# What the writing_voice tool shows by default: the newest samples, excerpted.
VOICE_SAMPLE_COUNT = 3
VOICE_EXCERPT_CHARS = 1500
SAMPLE_FULL_MAX_CHARS = 20_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profile_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS writing_voices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    notes TEXT NOT NULL DEFAULT '',
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS writing_voices_one_default
    ON writing_voices(is_default) WHERE is_default = 1;
CREATE TABLE IF NOT EXISTS writing_voice_samples (
    id TEXT PRIMARY KEY,
    voice_id TEXT NOT NULL REFERENCES writing_voices(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source_url TEXT,
    word_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS writing_voice_samples_voice
    ON writing_voice_samples(voice_id, created_at DESC);
"""

_ready: set[str] = set()


class ProfileError(ValueError):
    """Invalid input (the API turns it into a 400)."""


class NotFound(LookupError):
    """Unknown voice or sample (the API turns it into a 404)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> str:
    from config import get_settings
    s = get_settings()
    s.db_dir.mkdir(parents=True, exist_ok=True)
    return str(s.db_dir / "user_profile.db")


def _connect() -> sqlite3.Connection:
    from db_utils import apply_sqlite_pragmas
    path = _db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    apply_sqlite_pragmas(conn)
    if path not in _ready:
        conn.executescript(_SCHEMA)
        conn.commit()
        _ready.add(path)
    return conn


@contextmanager
def _db():
    """A connection that commits on success, rolls back on error, and is closed."""
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


# ── Settings ────────────────────────────────────────────────────────────────

def _read_settings() -> dict[str, Any]:
    with _db() as conn:
        rows = conn.execute("SELECT key, value FROM profile_settings").fetchall()
    out = {}
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"])
        except (TypeError, ValueError):
            logger.warning("user_profile: unreadable value for %s", r["key"])
    return out


def _channel_setting(stored: Any, key: str) -> dict[str, Any]:
    """{"global": v, "channels": {...}} from whatever is stored (dropping invalid values)."""
    spec = CHANNEL_SETTINGS[key]
    if not isinstance(stored, dict):
        stored = {"global": stored}
    g = stored.get("global")
    channels = stored.get("channels") if isinstance(stored.get("channels"), dict) else {}
    return {
        "global": g if g in spec["choices"] else spec["default"],
        "channels": {c: v for c, v in channels.items() if c in CHANNELS and v in spec["choices"]},
    }


def get_profile() -> dict[str, Any]:
    """The About you settings, normalised (defaults filled in)."""
    s = _read_settings()
    about = s.get("about_you")
    return {
        "about_you": about if isinstance(about, str) else "",
        "reply_length": _channel_setting(s.get("reply_length"), "reply_length"),
        "formatting": _channel_setting(s.get("formatting"), "formatting"),
    }


def _validate_channel_setting(key: str, value: Any) -> dict[str, Any]:
    spec = CHANNEL_SETTINGS[key]
    if isinstance(value, str):
        value = {"global": value}
    if not isinstance(value, dict):
        raise ProfileError(f"{key} must be an object with 'global' and 'channels'")
    g = value.get("global", spec["default"])
    if g not in spec["choices"]:
        raise ProfileError(f"{key}: '{g}' is not one of {', '.join(spec['choices'])}")
    channels = value.get("channels") or {}
    if not isinstance(channels, dict):
        raise ProfileError(f"{key}.channels must be an object")
    clean = {}
    for c, v in channels.items():
        if c not in CHANNELS:
            raise ProfileError(f"{key}: unknown channel '{c}'")
        if v is None or v == "":
            continue  # "same as default"
        if v not in spec["choices"]:
            raise ProfileError(f"{key}.{c}: '{v}' is not one of {', '.join(spec['choices'])}")
        clean[c] = v
    return {"global": g, "channels": clean}


def update_profile(changes: dict[str, Any]) -> dict[str, Any]:
    """Apply a partial update (only the keys given) and return the new profile."""
    writes: dict[str, Any] = {}
    if "about_you" in changes:
        about = changes["about_you"] or ""
        if not isinstance(about, str):
            raise ProfileError("about_you must be text")
        about = about.strip()
        if len(about) > ABOUT_MAX_CHARS:
            raise ProfileError(f"About you is limited to {ABOUT_MAX_CHARS} characters")
        writes["about_you"] = about
    for key in CHANNEL_SETTINGS:
        if key in changes and changes[key] is not None:
            writes[key] = _validate_channel_setting(key, changes[key])
    unknown = set(changes) - {"about_you", *CHANNEL_SETTINGS}
    if unknown:
        raise ProfileError(f"unknown setting(s): {', '.join(sorted(unknown))}")
    if writes:
        with _db() as conn:
            for k, v in writes.items():
                conn.execute(
                    "INSERT INTO profile_settings(key, value, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                    (k, json.dumps(v), _now()),
                )
    return get_profile()


def reply_style(channel: str | None, profile: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """{"length", "plain"} for replies on ``channel``; None for background work
    (no channel), where nobody reads a reply as a chat message."""
    if not channel:
        return None
    p = profile or get_profile()
    length = p["reply_length"]["channels"].get(channel, p["reply_length"]["global"])
    fmt = p["formatting"]["channels"].get(channel, p["formatting"]["global"])
    if fmt == "auto":
        plain = not RENDERS_MARKDOWN.get(channel, True)
    else:
        plain = fmt == "plain"
    return {"length": length, "plain": plain}


# ── Prompt text ─────────────────────────────────────────────────────────────

REPLY_LENGTH_TEXT = {
    "short": (
        "The user prefers SHORT replies. Answer in at most 3-4 sentences: no preamble, no restating "
        "the question, no recap at the end. Use a list only when the answer really is a list. If a "
        "complete answer needs more, give the short version and offer to go into detail. (This is "
        "about chat replies: documents, artifacts and drafts the user asks for keep their full length.)"
    ),
    "detailed": (
        "The user prefers DETAILED replies. Explain your reasoning, include the relevant context, "
        "trade-offs and examples, and use headings or lists where they make a long answer easier to read."
    ),
}
PLAIN_TEXT_RULE = (
    "Replies here are shown as PLAIN TEXT ({label}): Markdown is not rendered, so the symbols would "
    "appear as-is. Do not use Markdown — no # headers, no **bold** or *italics*, no tables, no "
    "[text](url) links (write the URL itself). Use short paragraphs and line breaks; for a list, "
    "put each item on its own line starting with \"- \" or \"1.\". Code blocks only when the user asks for code."
)


def render_reply_style(channel: str | None, profile: dict[str, Any] | None = None) -> str:
    """The "## Reply style" section for ``channel`` ("" when there is nothing to say)."""
    style = reply_style(channel, profile)
    if not style:
        return ""
    parts = []
    if style["length"] in REPLY_LENGTH_TEXT:
        parts.append(REPLY_LENGTH_TEXT[style["length"]])
    if style["plain"]:
        parts.append(PLAIN_TEXT_RULE.format(label=CHANNEL_LABELS.get(channel, channel)))
    if not parts:
        return ""
    return "## Reply style\n" + "\n\n".join(parts)


def render_profile_section(profile: dict[str, Any] | None = None) -> str:
    """The "## About the user" / "## Writing voices" sections ("" when both are empty)."""
    p = profile or get_profile()
    out = []
    if p["about_you"]:
        out.append(
            "## About the user\n"
            "The user wrote this about themselves so you know who you are helping. Use it to tailor "
            "your answers (examples, level of detail, what they already know); don't recite it back "
            "unless they ask.\n\n" + p["about_you"]
        )
    try:
        voices = list_voices()
    except Exception:
        logger.warning("user_profile: could not list writing voices", exc_info=True)
        voices = []
    if voices:
        names = ", ".join(f"\"{v['name']}\"" + (" (default)" if v["is_default"] else "") for v in voices)
        out.append(
            "## Writing voices\n"
            f"The user has saved writing voices: {names}. Before drafting anything that will go out under "
            "the user's name (a post, email, letter, article, bio, speech), call "
            "`writing_voice(action=\"get\")` — with `name` when the user names a voice — and write in that "
            "voice. Not needed for ordinary chat replies or research notes."
        )
    return "\n\n".join(out)


# ── Writing voices ──────────────────────────────────────────────────────────

def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def _clean_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ProfileError("A voice needs a name")
    name = " ".join(name.split())
    if len(name) > VOICE_NAME_MAX:
        raise ProfileError(f"Voice names are limited to {VOICE_NAME_MAX} characters")
    return name


def _clean_notes(notes: Any) -> str:
    notes = (notes or "")
    if not isinstance(notes, str):
        raise ProfileError("notes must be text")
    notes = notes.strip()
    if len(notes) > VOICE_NOTES_MAX:
        raise ProfileError(f"Voice notes are limited to {VOICE_NOTES_MAX} characters")
    return notes


def _voice_row(conn: sqlite3.Connection, r: sqlite3.Row) -> dict[str, Any]:
    stats = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(word_count), 0) AS words FROM writing_voice_samples WHERE voice_id = ?",
        (r["id"],),
    ).fetchone()
    return {
        "id": r["id"], "name": r["name"], "notes": r["notes"], "is_default": bool(r["is_default"]),
        "sample_count": stats["n"], "total_words": stats["words"],
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


def list_voices() -> list[dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM writing_voices ORDER BY is_default DESC, name COLLATE NOCASE").fetchall()
        return [_voice_row(conn, r) for r in rows]


def get_voice(voice_id: str, with_samples: bool = True) -> dict[str, Any]:
    with _db() as conn:
        r = conn.execute("SELECT * FROM writing_voices WHERE id = ?", (voice_id,)).fetchone()
        if not r:
            raise NotFound("No such writing voice")
        voice = _voice_row(conn, r)
        if with_samples:
            voice["samples"] = [
                {"id": s["id"], "title": s["title"], "source_url": s["source_url"],
                 "word_count": s["word_count"], "created_at": s["created_at"],
                 "preview": " ".join(s["content"].split())[:240]}
                for s in conn.execute(
                    "SELECT * FROM writing_voice_samples WHERE voice_id = ? ORDER BY created_at DESC, rowid DESC",
                    (voice_id,))
            ]
        return voice


def create_voice(name: Any, notes: Any = "", is_default: bool = False) -> dict[str, Any]:
    name, notes = _clean_name(name), _clean_notes(notes)
    vid = uuid.uuid4().hex
    now = _now()
    with _db() as conn:
        if conn.execute("SELECT COUNT(*) FROM writing_voices").fetchone()[0] >= MAX_VOICES:
            raise ProfileError(f"You can keep up to {MAX_VOICES} writing voices")
        if conn.execute("SELECT 1 FROM writing_voices WHERE name = ?", (name,)).fetchone():
            raise ProfileError(f"There is already a voice called \"{name}\"")
        if is_default:
            conn.execute("UPDATE writing_voices SET is_default = 0 WHERE is_default = 1")
        conn.execute(
            "INSERT INTO writing_voices(id, name, notes, is_default, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (vid, name, notes, 1 if is_default else 0, now, now),
        )
    return get_voice(vid)


def update_voice(voice_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    unknown = set(changes) - {"name", "notes", "is_default"}
    if unknown:
        raise ProfileError(f"unknown field(s): {', '.join(sorted(unknown))}")
    with _db() as conn:
        if not conn.execute("SELECT 1 FROM writing_voices WHERE id = ?", (voice_id,)).fetchone():
            raise NotFound("No such writing voice")
        sets, args = [], []
        if "name" in changes:
            name = _clean_name(changes["name"])
            clash = conn.execute("SELECT 1 FROM writing_voices WHERE name = ? AND id != ?", (name, voice_id)).fetchone()
            if clash:
                raise ProfileError(f"There is already a voice called \"{name}\"")
            sets.append("name = ?")
            args.append(name)
        if "notes" in changes:
            sets.append("notes = ?")
            args.append(_clean_notes(changes["notes"]))
        if "is_default" in changes:
            if changes["is_default"]:
                conn.execute("UPDATE writing_voices SET is_default = 0 WHERE is_default = 1 AND id != ?", (voice_id,))
            sets.append("is_default = ?")
            args.append(1 if changes["is_default"] else 0)
        if sets:
            sets.append("updated_at = ?")
            args += [_now(), voice_id]
            conn.execute(f"UPDATE writing_voices SET {', '.join(sets)} WHERE id = ?", args)
    return get_voice(voice_id)


def delete_voice(voice_id: str) -> None:
    with _db() as conn:
        cur = conn.execute("DELETE FROM writing_voices WHERE id = ?", (voice_id,))
        if cur.rowcount == 0:
            raise NotFound("No such writing voice")


def add_sample(voice_id: str, title: Any, content: Any, source_url: Any = None) -> dict[str, Any]:
    if not isinstance(content, str) or not content.strip():
        raise ProfileError("A sample needs some text")
    content = content.strip()
    if len(content) > SAMPLE_MAX_CHARS:
        raise ProfileError(f"Samples are limited to {SAMPLE_MAX_CHARS:,} characters")
    title = " ".join((title or "").split()) if isinstance(title, str) else ""
    if not title:
        title = " ".join(content.split()[:8])[:SAMPLE_TITLE_MAX]
    if len(title) > SAMPLE_TITLE_MAX:
        raise ProfileError(f"Sample titles are limited to {SAMPLE_TITLE_MAX} characters")
    if source_url is not None and not isinstance(source_url, str):
        raise ProfileError("source_url must be text")
    source_url = (source_url or "").strip() or None
    if source_url and not re.match(r"^https?://", source_url, re.I):
        raise ProfileError("The source link must start with http:// or https://")
    sid = uuid.uuid4().hex
    now = _now()
    with _db() as conn:
        if not conn.execute("SELECT 1 FROM writing_voices WHERE id = ?", (voice_id,)).fetchone():
            raise NotFound("No such writing voice")
        n = conn.execute("SELECT COUNT(*) FROM writing_voice_samples WHERE voice_id = ?", (voice_id,)).fetchone()[0]
        if n >= MAX_SAMPLES_PER_VOICE:
            raise ProfileError(f"A voice can hold up to {MAX_SAMPLES_PER_VOICE} samples")
        conn.execute(
            "INSERT INTO writing_voice_samples(id, voice_id, title, content, source_url, word_count, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, voice_id, title, content, source_url, _word_count(content), now),
        )
        conn.execute("UPDATE writing_voices SET updated_at = ? WHERE id = ?", (now, voice_id))
    return get_sample(voice_id, sid)


def get_sample(voice_id: str, sample_id: str) -> dict[str, Any]:
    with _db() as conn:
        s = conn.execute(
            "SELECT * FROM writing_voice_samples WHERE id = ? AND voice_id = ?", (sample_id, voice_id)).fetchone()
    if not s:
        raise NotFound("No such sample")
    return {k: s[k] for k in ("id", "voice_id", "title", "content", "source_url", "word_count", "created_at")}


def delete_sample(voice_id: str, sample_id: str) -> None:
    with _db() as conn:
        cur = conn.execute(
            "DELETE FROM writing_voice_samples WHERE id = ? AND voice_id = ?", (sample_id, voice_id))
        if cur.rowcount == 0:
            raise NotFound("No such sample")
        conn.execute("UPDATE writing_voices SET updated_at = ? WHERE id = ?", (_now(), voice_id))


def _samples_for_prompt(voice_id: str) -> list[sqlite3.Row]:
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM writing_voice_samples WHERE voice_id = ? ORDER BY created_at DESC, rowid DESC",
            (voice_id,)).fetchall()


def resolve_voice(name: str | None) -> tuple[dict[str, Any] | None, str]:
    """(voice, "") or (None, a message for the agent) — the named voice
    (exact, else a unique partial match), else the default, else the only one."""
    voices = list_voices()
    if not voices:
        return None, ("The user has no writing voices yet. Write in a natural style, and mention that they can "
                      "add one (with samples of their writing) in Settings → About you.")
    available = ", ".join(f"\"{v['name']}\"" for v in voices)
    if name and name.strip():
        want = name.strip().lower()
        exact = [v for v in voices if v["name"].lower() == want]
        if exact:
            return exact[0], ""
        partial = [v for v in voices if want in v["name"].lower()]
        if len(partial) == 1:
            return partial[0], ""
        return None, (f"No writing voice matches \"{name}\". Available voices: {available}. Ask the user which "
                      "one they meant - don't pick one yourself.")
    default = [v for v in voices if v["is_default"]]
    if default:
        return default[0], ""
    if len(voices) == 1:
        return voices[0], ""
    return None, (f"The user has several writing voices and none is the default: {available}. Ask which one to "
                  "use (or call again with name=...).")


VOICE_GUIDANCE = (
    "How to write in this voice:\n"
    "- Imitate HOW the user writes — sentence length and rhythm, vocabulary, structure, openings and endings, "
    "idiom, punctuation habits — not WHAT the samples are about. Their topics and facts are not material for "
    "the new piece.\n"
    "- Never copy sentences or distinctive phrases from the samples.\n"
    "- Anything the notes say to avoid is absolute, including in titles and headings.\n"
    "- The voice wins over generic structure: if the samples don't use headings, bullet lists or a summary "
    "paragraph, don't add them.\n"
    "- Excerpts end mid-text; call writing_voice(action=\"sample\", name=..., sample=\"<title>\") to read a whole one."
)


def render_voice_for_agent(voice: dict[str, Any]) -> str:
    """What writing_voice(action="get") returns: notes, newest sample excerpts, guidance."""
    lines = [f"# Writing voice: {voice['name']}"]
    if voice["notes"]:
        lines.append("\nThe user's notes on this voice (including anything to avoid):\n"
                     + "\n".join("> " + ln for ln in voice["notes"].splitlines()))
    samples = _samples_for_prompt(voice["id"])
    if samples:
        shown = samples[:VOICE_SAMPLE_COUNT]
        lines.append(f"\nSamples of the user's writing ({len(shown)} newest of {len(samples)}):")
        for s in shown:
            text = s["content"]
            excerpt = text if len(text) <= VOICE_EXCERPT_CHARS else text[:VOICE_EXCERPT_CHARS].rstrip() + " […]"
            lines.append(f"\n## \"{s['title']}\" ({s['word_count']} words)\n{excerpt}")
        if len(samples) > len(shown):
            others = ", ".join(f"\"{s['title']}\"" for s in samples[len(shown):])
            lines.append(f"\nOther samples: {others}.")
    else:
        lines.append("\nThis voice has no samples yet, so follow the notes alone.")
    lines.append("\n" + VOICE_GUIDANCE)
    return "\n".join(lines)


def find_sample(voice: dict[str, Any], sample: str | None) -> tuple[dict[str, Any] | None, str]:
    """A sample of ``voice`` by title (exact, else unique partial) or id; the newest when ``sample`` is empty."""
    rows = _samples_for_prompt(voice["id"])
    if not rows:
        return None, f"The voice \"{voice['name']}\" has no samples."
    if not sample or not sample.strip():
        return dict(rows[0]), ""
    want = sample.strip().lower()
    for r in rows:
        if r["id"] == sample.strip() or r["title"].lower() == want:
            return dict(r), ""
    partial = [r for r in rows if want in r["title"].lower()]
    if len(partial) == 1:
        return dict(partial[0]), ""
    titles = ", ".join(f"\"{r['title']}\"" for r in rows)
    return None, f"No sample of \"{voice['name']}\" matches \"{sample}\". Its samples: {titles}."

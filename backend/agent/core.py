"""Core agent loop with tool dispatch and streaming."""
from __future__ import annotations
import asyncio
import base64
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator

from agent.personality import get_full_personality
from agent.prompts import build_system_prompt, render_turn_context
from agent.tools import HOST_EXEC_TOOLS, execute_tool, get_all_tool_schemas
from agent.text_tool_calls import might_be_tool_call, recover as recover_tool_calls
from agent import tool_results
from config import get_settings
from models.provider import ModelProvider

logger = logging.getLogger(__name__)
settings = get_settings()

MAX_TOOL_ITERATIONS = int(__import__('os').getenv('MAX_TOOL_ITERATIONS', '100'))

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# Max image size to inline as base64 (5 MB)
_MAX_IMAGE_SIZE = 5 * 1024 * 1024




def _build_recent_jobs_block(project_id: str | None) -> str:
    """Build the system-prompt block that lists recent background-job
    activity for the active project. Empty string when nothing recent.

    Phase H.5 — fixes the confabulation case where the agent previously
    started a long-running task, the worker died silently, and the next
    chat turn pattern-matched against chat history to claim progress.
    With this block the agent reads the actual job state from the store
    every turn.
    """
    if not project_id:
        return ""
    try:
        from datetime import timedelta
        from jobs.store import get_store
        runs = get_store().list(
            project_id=project_id,
            statuses=["queued", "running", "failed", "stalled", "completed", "cancelled"],
            started_within=timedelta(hours=24),
            include_system=False,
            limit=5,
        )
    except Exception:
        return ""
    if not runs:
        return ""

    lines = ["RECENT BACKGROUND JOB ACTIVITY (last 24h, this project):"]
    for r in runs:
        rid = (r.get("id") or "")[:8]
        rtype = r.get("job_type") or "?"
        title = (r.get("title") or "").strip() or "(untitled)"
        status = r.get("status") or "?"
        progress = r.get("progress") or ""
        error = r.get("error") or ""

        when = ""
        for col in ("completed_at", "started_at", "created_at"):
            v = r.get(col)
            if v:
                when = v[:16].replace("T", " ")
                break

        line = f"- [{rtype}] #{rid} {status.upper()} ({when}) — {title!r}"
        if status in {"failed", "stalled", "cancelled"} and error:
            line += f"\n    Error: {error[:200]}"
        elif status == "running" and progress:
            line += f"\n    Progress: {progress[:200]}"
        elif r.get("pr_url"):
            line += f"\n    PR: {r['pr_url']}"
        lines.append(line)

    lines.append(
        "\nIMPORTANT: If the user asks about a task you started earlier "
        "in this conversation, call get_job_status(job_id=...) or "
        "list_recent_jobs() to read its current state — do NOT guess at "
        "progress from chat history. Failed and stalled jobs are NOT "
        "still running; tell the user so and offer to retry."
    )
    return "\n".join(lines)




def _build_available_skills_block(project_id: str) -> str:
    """Render an 'Available skills' block listing installed skills
    with their trigger phrases. Helps the agent prefer /skill-name
    invocation over create_task or start_coding_task when a user
    request matches a registered trigger."""
    try:
        from skills.registry import get_skill_registry
    except Exception:
        return ""
    try:
        registry = get_skill_registry()
        skills = registry.list_for_project(project_id) or []
    except Exception:
        return ""
    if not skills:
        return ""
    lines = [
        "## Available skills (installed in this project)",
        "",
        "When the user's request matches one of the trigger phrases "
        "below, INVOKE THE SKILL. The skill\'s instructions will run "
        "as part of your normal turn — you do NOT need create_task "
        "or start_coding_task. Either:",
        "  • follow the skill\'s instructions inline this turn, OR",
        "  • tell the user you\'re running the skill and proceed with "
        "    the steps it lays out, citing the skill name in your reply.",
        "",
        "Skills are NOT scheduled tasks and are NOT coding tasks. They "
        "are reusable recipes the user has already approved.",
        "",
    ]
    for sk in skills[:30]:
        try:
            name = sk.name
            desc = (sk.manifest.description or "").strip().splitlines()[0][:160]
            triggers = ", ".join(f"\"{t}\"" for t in (sk.triggers or [])[:6])
        except Exception:
            continue
        lines.append(f"- **/{name}** — {desc}")
        if triggers:
            lines.append(f"    triggers: {triggers}")
    if len(skills) > 30:
        lines.append(f"\n_... and {len(skills) - 30} more skills_")
    return "\n".join(lines)

class AgentCore:
    """The main agent loop."""

    def __init__(
        self,
        provider: ModelProvider,
        project_id: str = "default",
        project_name: str | None = None,
        session_id: str | None = None,
        memory_manager: Any = None,
        skill_context: str | None = None,
        active_skill_name: str | None = None,
        host_exec: bool = False,
        interactive: bool = False,
    ):
        self.provider = provider
        # True only for turns a person drives from the web UI.
        self.interactive = interactive
        # Host-exec tools (shell/code/git) are opt-in per construction site.
        # Default False so any new caller is safe by default.
        self.host_exec = host_exec
        self.project_id = project_id
        self.project_name = project_name
        self.session_id = session_id or str(uuid.uuid4())
        self.memory_manager = memory_manager
        self.skill_context = skill_context
        self.active_skill_name = active_skill_name
        self.working_memory: list[dict[str, str]] = []

    @classmethod
    async def from_session(
        cls,
        *,
        session_id: str,
        project_id: str = "default",
        provider,
        memory_manager=None,
        skill_context: str | None = None,
        active_skill_name: str | None = None,
        message_limit: int = 200,
        host_exec: bool = False,
        interactive: bool = False,
    ) -> "AgentCore":
        """Build an AgentCore instance with working_memory pre-populated
        from the messages table for the given session_id. Used when
        the user resumes an old chat: instead of starting fresh, we
        replay the literal message history into the agent's context.

        Tool-call results from prior turns are NOT replayed as separate
        protocol messages — they're folded into the assistant message
        that triggered them. Most LLM providers accept a clean
        [user, assistant, ...] history without tool roles on resume.
        """
        from memory.episodic import EpisodicMemory
        self = cls(
            provider=provider,
            project_id=project_id,
            session_id=session_id,
            memory_manager=memory_manager,
            skill_context=skill_context,
            active_skill_name=active_skill_name,
            host_exec=host_exec,
            interactive=interactive,
        )
        ep = EpisodicMemory()
        history = await ep.get_history(session_id=session_id, limit=message_limit)
        for m in history:
            role = m.get("role")
            content = (m.get("content") or "").strip()
            if not content:
                continue
            if role in ("user", "assistant", "system"):
                self.working_memory.append({"role": role, "content": content})
        return self

    def _add_working_message(self, role: str, content: Any) -> None:
        """Add message to working memory."""
        self.working_memory.append({"role": role, "content": content})

    def _build_user_content(self, message: str) -> str | list[dict]:
        """Build multimodal content blocks if the message references images.

        Two supported reference forms:
          1. New (artifact-backed):
             "[image: <path> (artifact:<artifact_id>)]"
             — bytes loaded from ArtifactStore._load_blob
          2. Legacy (workspace-backed, pre-2026.05.19):
             "uploads/<filename>.png"
             — bytes loaded from workspace/uploads/

        Returns the original string when no images are found, otherwise a
        list of content blocks (text + image_url).
        """
        image_blocks: list[dict] = []

        # ── Form 1: artifact references ────────────────────────────────
        artifact_pattern = re.compile(
            r"\[image:[^\]]*?\(artifact:([a-zA-Z0-9_\-]+)\)\]",
            re.IGNORECASE,
        )
        seen_artifacts: set[str] = set()
        artifact_store = None
        for match in artifact_pattern.finditer(message):
            artifact_id = match.group(1)
            if artifact_id in seen_artifacts:
                continue
            seen_artifacts.add(artifact_id)
            try:
                if artifact_store is None:
                    from artifacts.store import get_store as _gs
                    artifact_store = _gs()
                a = artifact_store.get(artifact_id)
                if not a or not a.get("blob_path"):
                    logger.debug("Artifact %s not found or has no blob", artifact_id)
                    continue
                ct = (a.get("content_type") or "").lower()
                # Vision models accept these MIME types; SVG and exotic types are skipped.
                if ct not in {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"}:
                    logger.debug("Artifact %s content_type %r not vision-compatible", artifact_id, ct)
                    continue
                if (a.get("size_bytes") or 0) > _MAX_IMAGE_SIZE:
                    logger.debug("Artifact %s exceeds inline size (%d bytes)",
                                 artifact_id, a.get("size_bytes") or 0)
                    continue
                raw = artifact_store._load_blob(a["blob_path"])
                b64 = base64.b64encode(raw).decode("utf-8")
                image_blocks.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{a['content_type']};base64,{b64}"},
                })
                logger.info("Inlined artifact image %s (%d KB)", artifact_id, len(raw) // 1024)
            except Exception as e:
                logger.warning("Failed to inline artifact %s: %s", artifact_id, e)

        # ── Form 2: legacy workspace uploads ──────────────────────────
        workspace_pattern = re.compile(
            r"uploads/(.+?\.(?:png|jpe?g|gif|webp|bmp))",
            re.IGNORECASE,
        )
        if self.project_id and self.project_id != "default":
            base = settings.projects_dir / self.project_id / "workspace"
        else:
            base = settings.workspace_dir
        seen_files: set[str] = set()
        for match in workspace_pattern.finditer(message):
            filename = match.group(1)
            if filename in seen_files:
                continue
            seen_files.add(filename)
            candidate = base / "uploads" / filename
            if candidate.exists() and candidate.stat().st_size <= _MAX_IMAGE_SIZE:
                try:
                    raw = candidate.read_bytes()
                    b64 = base64.b64encode(raw).decode("utf-8")
                    ext = candidate.suffix.lower().lstrip(".")
                    mime = f"image/{ext}" if ext != "jpg" else "image/jpeg"
                    image_blocks.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    })
                    logger.info("Inlined workspace image %s (%d KB)", candidate.name, len(raw) // 1024)
                except Exception as e:
                    logger.warning("Failed to inline workspace image %s: %s", filename, e)

        if not image_blocks:
            return message

        return [{"type": "text", "text": message}, *image_blocks]

    def _in_context_texts(self, user_message: str) -> set[str]:
        """Texts the model will already see this turn (the session so far and
        the new message; chat saves the message to episodic before the agent
        runs, so recall would otherwise return the question itself)."""
        texts = {user_message.strip()}
        for m in self.working_memory:
            if isinstance(m.get("content"), str):
                texts.add(m["content"].strip())
        return texts

    def _get_working_messages(self) -> list[dict[str, str]]:
        """Get working memory messages."""
        return self.working_memory.copy()

    async def chat(
        self,
        user_message: str,
        stream: bool = True,
        max_iterations: int | None = None,
        reanchor_text: str | None = None,
        reanchor_every: int = 15,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Process a user message and yield streaming events.

        Event types:
          {"type": "text_delta", "content": "..."}
          {"type": "tool_call", "name": "...", "args": {...}}
          {"type": "tool_result", "name": "...", "result": "..."}
          {"type": "done", "full_response": "..."}
          {"type": "error", "message": "..."}
        """
        try:
            # Conversation behaviour: the project's overrides, else the
            # global values (utils.chat_settings — the one read path).
            try:
                from utils.chat_settings import effective as _chat_settings
                _cs = _chat_settings(self.project_id or "default")
            except Exception:
                logger.warning("chat settings unavailable — using defaults", exc_info=True)
                _cs = {"tone_weight": "balanced", "context_focus": "balanced", "memory_recall": True}
            _personality_weight = _cs["tone_weight"]
            _context_focus = _cs["context_focus"]

            # Pre-recall relevant memories to inject into system prompt context
            recalled_memories = None
            try:
                if _cs["memory_recall"] and self.memory_manager:
                    mgr = self.memory_manager
                    # Rerank has its own shorter budget inside recall
                    # (memory.manager.rerank_timeout) so a slow reranker
                    # cannot run this out and cost the turn every memory.
                    recall_budget = get_settings().pre_recall_timeout_seconds
                    try:
                        results = await asyncio.wait_for(
                            mgr.recall(
                                query=user_message,
                                tiers=["semantic", "episodic", "graph"],
                                project_id=self.project_id or "default",
                                limit_per_tier=5,
                                context_focus=_context_focus,
                                in_context=self._in_context_texts(user_message),
                            ),
                            timeout=recall_budget,
                        )
                    except asyncio.TimeoutError:
                        results = None
                        logger.warning(
                            "Pre-recall exceeded %.1fs, answering without memory context "
                            "(PRE_RECALL_TIMEOUT_SECONDS)", recall_budget,
                        )
                    if results:
                        recalled_memories = results
                        logger.debug("Pre-recalled %d memories for context", len(results))
                        summary_lines = [f"[{r.get('tier','?')}] {r.get('content','')[:120]}" for r in results]
                        yield {
                            "type": "tool_call",
                            "name": "context_loaded",
                            "args": {"sources": len(results), "tiers": list({r.get("tier") for r in results})},
                        }
                        yield {
                            "type": "tool_result",
                            "name": "context_loaded",
                            "result": "\n\n".join(summary_lines),
                        }
                else:
                    logger.debug("Memory pre-recall disabled or no memory_manager available")
            except Exception as e:
                logger.warning("Failed to pre-recall memories: %s", e)

            # Build system prompt (inject skill instructions if a skill is active)
            system_prompt = build_system_prompt(
                project_id=self.project_id,
                project_name=self.project_name,
                extra_context=self.skill_context,
                personality_weight=_personality_weight,
                host_exec=self.host_exec,
            )

            # Phase H.5 — append a "recent background jobs" block so the
            # agent doesn't confabulate progress against dead tasks.
            # (appended after the skills block below: it changes with job
            # state, the skills list almost never — keep the stable part first)
            try:
                jobs_block = _build_recent_jobs_block(self.project_id)
            except Exception as e:
                jobs_block = ""
                logger.debug("recent-jobs block injection failed: %s", e)

            # H7p — append an "Available skills" block so the agent
            # knows what callable skills are installed even when
            # auto-discovery is off. When the user's message matches
            # a trigger, the agent should invoke that skill rather
            # than fall back to create_task / start_coding_task.
            try:
                skills_block = _build_available_skills_block(self.project_id)
                if skills_block:
                    system_prompt = system_prompt + "\n\n" + skills_block
            except Exception as e:
                logger.debug("available-skills block injection failed: %s", e)
            if jobs_block:
                system_prompt = system_prompt + "\n\n" + jobs_block

            # Get conversation history from working memory
            history = self._get_working_messages()

            # Add current user message — inline images for vision models
            user_content = self._build_user_content(user_message)
            # Per-turn context (time + recalled memory) rides in front of the
            # new message, after the history — see prompts.render_turn_context.
            turn_context = render_turn_context(recalled_memories)
            if isinstance(user_content, list):
                user_content = [{"type": "text", "text": turn_context}] + user_content
            else:
                user_content = turn_context + user_content
            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(history)
            messages.append({"role": "user", "content": user_content})

            # Store plain text version in working memory (not base64 blobs)
            self._add_working_message("user", user_message)

            full_response = ""
            iterations = 0
            iteration_limit = max_iterations or MAX_TOOL_ITERATIONS

            # Resolve all available tools (built-in + MCP)
            all_tools = get_all_tool_schemas()
            if not self.host_exec:
                all_tools = [
                    t for t in all_tools
                    if t.get("function", {}).get("name") not in HOST_EXEC_TOOLS
                ]

            tool_names = {t.get("function", {}).get("name") for t in all_tools}

            # Opt-in reasoning for the agent-class model (settings.agent_thinking).
            # Only the agent class: a code/quick route may be a model where
            # thinking is slow or meaningless.
            agent_extra = None
            if get_settings().agent_thinking and getattr(self.provider, "task_class", "agent") == "agent":
                agent_extra = {"chat_template_kwargs": {"enable_thinking": True}}
            # Only sent when set, so the call is unchanged for every other provider.
            extra_kw = {"extra_body": agent_extra} if agent_extra else {}

            while iterations < iteration_limit:
                iterations += 1
                self._progress()
                tool_calls_this_round: list[dict] = []
                current_text = ""
                round_reasoning = ""

                if stream:
                    # Streaming mode. A reply that starts like a textual tool
                    # call ("{", "```", "<tool_call") is held back until the
                    # round ends so it can be recovered as a real call
                    # instead of shown to the user (agent/text_tool_calls.py).
                    stream_error = False
                    hold: bool | None = None
                    held = ""
                    async for chunk in self.provider.chat(
                        messages=messages,
                        tools=all_tools,
                        stream=True,
                        **extra_kw,
                    ):
                        self._progress()
                        if chunk["type"] == "text_delta":
                            current_text += chunk["content"]
                            if hold is False:
                                yield chunk
                                continue
                            held += chunk["content"]
                            hold = might_be_tool_call(held)
                            if hold is False:
                                yield {"type": "text_delta", "content": held}
                                held = ""
                        elif chunk["type"] == "tool_call":
                            tool_calls_this_round.append(chunk)
                            yield chunk
                        elif chunk["type"] == "error":
                            yield chunk
                            stream_error = True
                        elif chunk["type"] == "done":
                            round_reasoning = chunk.get("reasoning") or ""
                    if held and not stream_error and not tool_calls_this_round:
                        recovered = recover_tool_calls(held, tool_names)
                        if recovered:
                            logger.info("Recovered %d tool call(s) the model wrote as text: %s",
                                        len(recovered), [c["name"] for c in recovered])
                            tool_calls_this_round = recovered
                            current_text = ""
                            for tc in recovered:
                                yield tc
                            held = ""
                    if held:
                        yield {"type": "text_delta", "content": held}
                    if stream_error:
                        break
                else:
                    # Non-streaming mode
                    response = await self.provider.chat_complete(
                        messages=messages,
                        tools=all_tools,
                        **extra_kw,
                    )
                    current_text = response.get("content", "")
                    round_reasoning = response.get("reasoning") or ""
                    tool_calls_this_round = response.get("tool_calls", [])
                    if current_text and not tool_calls_this_round:
                        recovered = recover_tool_calls(current_text, tool_names)
                        if recovered:
                            logger.info("Recovered %d tool call(s) the model wrote as text: %s",
                                        len(recovered), [c["name"] for c in recovered])
                            tool_calls_this_round = recovered
                            current_text = ""
                    if current_text:
                        yield {"type": "text_delta", "content": current_text}
                    # Streaming mode yields tool_call chunks as they arrive.
                    # Mirror that here so consumers see the same event
                    # stream — without this, the job handler's tool counter
                    # and plan-step progress matching never fire for
                    # autonomous tasks (observed: tool_calls_observed=1 on
                    # a 500-tool-call run).
                    for tc in tool_calls_this_round:
                        yield {"type": "tool_call", "name": tc.get("name"),
                               "args": tc.get("args", {}), "id": tc.get("id")}

                if (not tool_calls_this_round and not (current_text or "").strip()
                        and round_reasoning.strip()):
                    # A thinking model ended its turn with the answer inside its
                    # reasoning and no reply text (seen ~1 in 10 with a 9B model).
                    final = await self._finalize_from_reasoning(messages, round_reasoning, agent_extra,
                                                                 tools=all_tools)
                    if final:
                        current_text = final
                        yield {"type": "text_delta", "content": final}

                if current_text:
                    full_response = current_text

                if not tool_calls_this_round:
                    # No tool calls, we're done
                    break

                # Add assistant message with tool calls to conversation
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": current_text or "",
                    "tool_calls": [
                        {
                            "id": tc.get("id", str(uuid.uuid4())),
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc.get("args", {})),
                            },
                        }
                        for tc in tool_calls_this_round
                    ],
                }
                messages.append(assistant_msg)

                # Execute each tool call
                for tc in tool_calls_this_round:
                    tool_name = tc["name"]
                    tool_args = tc.get("args", {})
                    tool_id = tc.get("id", str(uuid.uuid4()))

                    logger.info(f"Executing tool: {tool_name} with args: {tool_args}")
                    # Find the most recent assistant text for save_last_response
                    last_assistant_text = ""
                    for _m in reversed(messages):
                        if _m.get("role") == "assistant" and _m.get("content"):
                            last_assistant_text = _m["content"]
                            break
                    if tc.get("args_error"):
                        result = (
                            f"Error: {tool_name} was not run — its {tc['args_error']}. "
                            "Call it again with the arguments as a single JSON object."
                        )
                    else:
                        result = await execute_tool(
                            tool_name=tool_name,
                            tool_args=tool_args,
                            memory_manager=self.memory_manager,
                            project_id=self.project_id,
                            session_id=self.session_id,
                            last_assistant_text=last_assistant_text,
                            interactive=self.interactive,
                            host_exec=self.host_exec,
                        )
                    self._progress()
                    yield {"type": "tool_result", "name": tool_name, "result": result, "tool_id": tool_id,
                           "is_error": tool_results.is_error(result)}

                    # The UI gets the full result; the model gets it capped
                    # (TOOL_RESULT_MAX_CHARS) so one huge reply can't blow the
                    # context window.
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": tool_results.cap(result),
                    })

                # Re-anchor: long tool loops bury the original instructions
                # under accumulated tool output and models drift from them
                # (observed: a merge task abandoning its push-every-5 and
                # hunk-only-edits rules mid-run). Periodically re-inject the
                # instructions at full strength.
                if reanchor_text and iterations % reanchor_every == 0:
                    messages.append({
                        "role": "user",
                        "content": (
                            f"[Pantheon harness re-anchor — iteration "
                            f"{iterations} of {iteration_limit}]\n"
                            "Re-read your original instructions before "
                            "continuing:\n\n"
                            f"{reanchor_text}\n\n"
                            "State in one line which step you are on, then "
                            "continue. If you have drifted from these "
                            "instructions or their rules, correct course now."
                        ),
                    })
                if iteration_limit - iterations == 10:
                    messages.append({
                        "role": "user",
                        "content": (
                            "[Pantheon harness notice] Only 10 iterations "
                            "remain in your budget. Bring the work to a safe "
                            "stopping point, update your task ledger (if you "
                            "keep one), and write your final summary — a "
                            "truncated run with a current ledger is "
                            "resumable; one without is not."
                        ),
                    })

            # Save assistant response
            if full_response:
                self._add_working_message("assistant", full_response)

            # Distinguish natural completion (model stopped calling tools)
            # from hitting the iteration cap mid-work — callers must not
            # present a truncated run as a finished one.
            truncated = (iterations >= iteration_limit
                         and bool(tool_calls_this_round))
            if truncated:
                logger.warning(
                    "Agent loop truncated at iteration cap (%d) with tool "
                    "calls still pending — work is incomplete.", iteration_limit,
                )
            yield {"type": "done", "full_response": full_response,
                   "iterations": iterations, "truncated": truncated}

        except Exception as e:
            logger.error(f"Agent error: {e}", exc_info=True)
            yield {"type": "error", "message": str(e)}

    @staticmethod
    def _progress() -> None:
        """Tell the job stall watchdog (if running under one) we're alive."""
        from utils.progress import report_progress
        report_progress()

    async def _finalize_from_reasoning(self, messages: list[dict], reasoning: str,
                                       agent_extra: dict | None, tools: list[dict] | None = None) -> str:
        """Ask once more for the reply itself: thinking off, tool calls off, with
        the model's own reasoning handed back as working notes. Returns '' on
        failure so the caller falls back to the old behaviour.

        The round's tools are sent again with tool_choice "none" rather than
        dropped: templates render tools at the top of the prompt, so dropping
        them made this call recompute the whole prompt (~5K tokens, 3 s on a
        9B model) instead of reusing the round's KV cache."""
        notes = reasoning.strip()[-8000:]
        msgs = messages + [
            {"role": "assistant", "content": "(my working notes)\n" + notes},
            {"role": "user", "content": "Write your reply to my request above now, based on your "
                                        "working notes. Reply directly; do not call tools."},
        ]
        extra = {"chat_template_kwargs": {"enable_thinking": False}} if agent_extra else {}
        if tools:
            extra["tool_choice"] = "none"
        try:
            kw = {"extra_body": extra} if extra else {}
            r = await self.provider.chat_complete(messages=msgs, tools=tools or None, **kw)
            text = (r.get("content") or "").strip()
            logger.info("Finalized an empty thinking-mode reply (%d chars)", len(text))
            return text
        except Exception as e:
            logger.warning("Finalize round failed: %s", e)
            return ""

    async def run_autonomous(self, task_description: str) -> str:
        """Run a task autonomously (no streaming, returns final response).

        Raises RuntimeError when the loop ends with an error event and no
        ``done`` — returning "" there made jobs report success on LLM
        failures.
        """
        full_response = ""
        error: str | None = None
        got_done = False
        async for event in self.chat(task_description, stream=False):
            if event["type"] == "done":
                got_done = True
                full_response = event.get("full_response", "")
            elif event["type"] == "error":
                error = event.get("message") or "unknown agent error"
        if error and not got_done:
            raise RuntimeError(f"Agent error: {error}")
        return full_response

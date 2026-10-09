"""Skills and self-knowledge: create_skill, get_self_documentation."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import logging
from agent.tools.registry import ToolContext, tool

logger = logging.getLogger(__name__)

SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_self_documentation",
            "description": (
                "Pantheon's own documentation: skills, configuration, architecture, deployment status. Use for "
                "questions about yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_skill",
            "description": (
                "Create a reusable skill the user runs with /name - not a scheduled task. instructions is the "
                "full markdown recipe: the exact tool per step plus any schemas, paths or thresholds already "
                "agreed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Slug (lowercase, hyphens)."
                    },
                    "description": {
                        "type": "string",
                        "description": "One line."
                    },
                    "triggers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Content-bearing phrases that suggest it."
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "instructions": {
                        "type": "string"
                    }
                },
                "required": ["name", "description", "instructions"]
            }
        }
    },
]


@tool('get_self_documentation')
async def _tool_get_self_documentation(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    try:
        from utils.self_doc import generate_self_doc
        return generate_self_doc()
    except Exception as e:
        return f"get_self_documentation failed: {e}"



@tool('create_skill')
async def _tool_create_skill(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    from skills import editor as _skill_ed
    import json as _json, re as _re

    name = (tool_args.get("name") or "").strip().lower()
    if not _re.match(r"^[a-z0-9][a-z0-9_-]{1,63}$", name):
        return (
            "create_skill rejected: name must be lowercase "
            "slug (a-z, 0-9, hyphen/underscore), 2-64 chars."
        )
    description = (tool_args.get("description") or "").strip()
    instructions = (tool_args.get("instructions") or "").strip()
    triggers = tool_args.get("triggers") or []
    tags = tool_args.get("tags") or []
    if not description or not instructions:
        return "create_skill rejected: description and instructions are required."

    try:
        target = _skill_ed.create_blank_skill(name, description)
    except FileExistsError:
        return (
            f"Skill {name!r} already exists. Pick a different "
            f"name or update the existing skill via the Skills tab."
        )
    except Exception as e:
        return f"create_skill failed: {e}"

    # Update the scaffold with the actual triggers/tags/instructions.
    try:
        manifest_path = target / "skill.json"
        manifest = _json.loads(manifest_path.read_text("utf-8"))
        if triggers:
            manifest["triggers"] = [str(x) for x in triggers]
        if tags:
            manifest["tags"] = [str(x) for x in tags if str(x).strip()]
        manifest_path.write_text(_json.dumps(manifest, indent=2), "utf-8")
        (target / "instructions.md").write_text(instructions, "utf-8")
    except Exception as e:
        return f"create_skill: scaffold created but failed to fill manifest/instructions: {e}"

    # Refresh the registry so it picks up the new skill (which runs
    # the static scan), then the full scan: text the model wrote may
    # carry injected instructions, and it becomes system prompt.
    scan_note = ""
    try:
        from skills.registry import reload_skill_registry
        reg = reload_skill_registry()
        skill = reg.get(name)
        if skill is not None:
            from skills.scanner import scan_skill
            result = await scan_skill(Path(skill.skill_dir), skill.manifest, skill.instructions)
            skill.manifest.security_scan = result
            reg.save_scan_result(name, result)
            if not result.passed:
                issues = "; ".join(f.message for f in result.findings
                                   if f.severity.value in ("critical", "warning"))[:400]
                return (f"Skill {name!r} was created but FAILED its security scan and is "
                        f"blocked: {issues}. Rewrite the instructions as plain workflow "
                        f"steps, or the user can review it in the Skills tab.")
            scan_note = f"  security scan: passed (risk {result.risk_score})\n"
    except Exception as e:
        logger.warning("create_skill scan failed for %s: %s", name, e)

    return (
        f"Skill {name!r} created and registered.\n"
        f"{scan_note}"
        f"  triggers: {triggers or '(none — auto-suggest disabled)'}\n"
        f"  tags: {tags or '(none)'}\n\n"
        f"Invoke it later with `/{name}` in any chat. Edit it "
        f"in the Skills tab if you want to refine the "
        f"instructions or add triggers."
    )


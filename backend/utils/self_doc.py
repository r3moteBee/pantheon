"""Self-documentation utility for Pantheon.

Gathers architecture, deployment, configuration, database, and skill metadata 
and formats it into a structured Markdown document.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import sqlite3
import sys
from pathlib import Path
import subprocess
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)

def _resolve_app_version() -> str:
    """Read version from frontend/package.json or VERSION file."""
    root_dir = Path(__file__).resolve().parent.parent.parent
    pkg_json_path = root_dir / "frontend" / "package.json"
    if pkg_json_path.exists():
        try:
            return json.loads(pkg_json_path.read_text(encoding="utf-8"))["version"]
        except Exception:
            pass
    version_file = root_dir / "VERSION"
    if version_file.exists():
        try:
            return version_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return "unknown"

def _get_git_info() -> dict[str, str]:
    """Retrieve Git repository status."""
    root_dir = Path(__file__).resolve().parent.parent.parent
    info = {"branch": "unknown", "commit": "unknown", "date": "unknown"}
    if not (root_dir / ".git").exists():
        return info
    
    try:
        # Branch
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(root_dir),
            text=True,
            stderr=subprocess.DEVNULL
        ).strip()
        info["branch"] = branch
        
        # Commit & Date
        commit_log = subprocess.check_output(
            ["git", "log", "-1", "--format=%h - %s (%ad)", "--date=short"],
            cwd=str(root_dir),
            text=True,
            stderr=subprocess.DEVNULL
        ).strip()
        if commit_log:
            info["commit"] = commit_log
    except Exception:
        pass
    return info

def _get_binary_version(cmd: str) -> str:
    """Get version of a CLI utility (like node or npm)."""
    try:
        return subprocess.check_output([cmd, "-v"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "not installed"

def _get_db_stats(db_path: Path) -> dict[str, Any]:
    """Get tables and row counts for a SQLite database."""
    stats = {"size_kb": 0.0, "tables": {}}
    if not db_path.exists():
        return stats
    
    stats["size_kb"] = round(db_path.stat().st_size / 1024, 2)
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall() if not row[0].startswith("sqlite_")]
        for table in tables:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table};")
                stats["tables"][table] = cursor.fetchone()[0]
            except Exception:
                pass
        conn.close()
    except Exception as e:
        logger.warning("Failed to query DB stats for %s: %s", db_path.name, e)
    return stats

def _get_chroma_stats(settings: Any) -> dict[str, Any]:
    """Query ChromaDB stats."""
    stats = {"status": "unknown", "collections": []}
    try:
        import chromadb
        if settings.chroma_host:
            client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
            stats["status"] = "connected (HTTP)"
        else:
            chroma_path = settings.data_dir / "chroma"
            client = chromadb.PersistentClient(path=str(chroma_path))
            stats["status"] = f"connected (local persistent at {chroma_path})"
        
        collections = client.list_collections()
        for col in collections:
            col_info = {"name": col.name, "count": col.count()}
            stats["collections"].append(col_info)
    except Exception as e:
        stats["status"] = f"unavailable: {e}"
    return stats

def _mask_value(key: str, val: Any) -> Any:
    """Mask credentials and sensitive strings."""
    if val is None:
        return None
    sensitive_keys = {
        "key", "secret", "password", "token", "auth", "master", "private"
    }
    key_lower = key.lower()
    if any(sk in key_lower for sk in sensitive_keys):
        val_str = str(val)
        if len(val_str) > 8:
            return f"{val_str[:4]}...{val_str[-4:]}"
        return "********"
    return val

def generate_self_doc() -> str:
    """Compile the Pantheon system documentation in Markdown format."""
    settings = get_settings()
    app_version = _resolve_app_version()
    git_info = _get_git_info()
    
    # 1. System Environment
    sys_env = {
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": sys.version.split()[0],
        "python_path": sys.executable,
        "node": _get_binary_version("node"),
        "npm": _get_binary_version("npm"),
    }
    
    # 2. Database Stats
    dbs = {
        "episodic": _get_db_stats(Path(settings.episodic_db_path)),
        "graph": _get_db_stats(Path(settings.graph_db_path)),
        "vault": _get_db_stats(Path(settings.vault_db_path)),
        "scheduler": _get_db_stats(Path(settings.scheduler_db_path)),
    }
    
    # Chroma
    chroma = _get_chroma_stats(settings)
    
    # Projects Metadata
    projects_file = Path(settings.db_dir) / "projects.json"
    project_count = 0
    if projects_file.exists():
        try:
            projects_data = json.loads(projects_file.read_text(encoding="utf-8"))
            project_count = len(projects_data)
        except Exception:
            pass

    # 3+4. Task-class routing (source of truth for which model serves what)
    routing_rows: list[dict] = []
    routing_warnings: list[str] = []
    endpoints = []
    try:
        from llm_config.models import TASK_CLASSES
        from llm_config.store import get_profile, get_routes, list_endpoints, route_warnings
        endpoints = [
            {"name": ep.name, "base_url": ep.base_url, "api_type": ep.api_type}
            for ep in list_endpoints()
        ]
        routes = get_routes()
        for cls, meta in TASK_CLASSES.items():
            entries = routes.get(cls) or []
            if not entries:
                parent = meta.get("inherits")
                routing_rows.append({
                    "class": cls, "position": "—",
                    "endpoint": "", "model": "",
                    "note": ("not set — chat router never picks it" if cls in ("quick", "long_context")
                             else f"not set — uses {parent}" if parent else "NOT CONFIGURED"),
                })
                continue
            for i, e in enumerate(entries):
                prof = get_profile(e["endpoint"], e["model"])
                caps = [c for c in ("tools", "vision", "image_gen", "embedding") if getattr(prof, c)]
                routing_rows.append({
                    "class": cls, "position": "primary" if i == 0 else f"fallback {i}",
                    "endpoint": e["endpoint"], "model": e["model"],
                    "note": f"{prof.tier}; {', '.join(caps) or 'no special caps'}",
                })
        routing_warnings = route_warnings(routes)
    except Exception as e:
        logger.warning("Could not read LLM routing: %s", e)

    recent_llm_errors: list[str] = []
    try:
        from llm_config.usage import summary as _usage_summary
        for row in _usage_summary(24).get("rows", []):
            if row.get("errors"):
                recent_llm_errors.append(
                    f"{row['task_class']} {row['endpoint']}/{row['model']}: "
                    f"{row['errors']} error(s) in 24h — last: {row.get('last_error') or '?'}"
                )
    except Exception as e:
        logger.debug("Could not read LLM usage: %s", e)

    # 5. Live Search Chain (from SearchProviderManager)
    search_chain = []
    try:
        from agent.search_providers import get_search_manager
        search_info = get_search_manager().get_usage()
        for prov in search_info.get("providers", []):
            search_chain.append({
                "name": prov["name"],
                "type": prov["type"],
                "url": prov["url"],
                "enabled": prov["enabled"],
                "api_key_set": prov["api_key_set"],
                "daily_used": prov["daily_used"],
                "daily_limit": prov["daily_limit"],
            })
    except Exception as e:
        logger.warning("Could not read search providers: %s", e)

    # 6. Connected MCP Connections (from MCPManager)
    mcp_connections = []
    try:
        from mcp_client.manager import get_mcp_manager
        mcp_connections = get_mcp_manager().list_connections()
    except Exception as e:
        logger.warning("Could not read MCP connections: %s", e)
        
    # 7. Registered Skills
    skills_list = []
    try:
        from skills.registry import get_skill_registry
        registry = get_skill_registry()
        registry.ensure_loaded()
        for skill in registry.list_all():
            skills_list.append({
                "name": skill.manifest.name,
                "description": skill.manifest.description,
                "triggers": skill.manifest.triggers or [],
                "requires_mcp": skill.manifest.requires_mcp or [],
                "is_bundled": skill.is_bundled,
            })
    except Exception as e:
        logger.warning("Could not list skills: %s", e)

    # 8. Env Config Settings (Masked)
    masked_settings = {}
    if hasattr(settings, "model_fields"):
        for field in settings.model_fields:
            val = getattr(settings, field)
            masked_settings[field] = _mask_value(field, val)
    else:
        # Pydantic v1 fallback
        for field in settings.__fields__:
            val = getattr(settings, field)
            masked_settings[field] = _mask_value(field, val)

    # Compile the Markdown output
    md = []
    md.append("# 🏛️ Pantheon Self-Documentation System")
    md.append("This document contains the dynamic self-documentation of the running Pantheon instance, generated in real-time.")
    
    # Section: Deployment Status
    md.append("\n## 🚀 Current Deployment & System Environment")
    md.append(f"- **Pantheon Version**: `{app_version}`")
    md.append(f"- **Git Branch**: `{git_info['branch']}`")
    md.append(f"- **Git Last Commit**: `{git_info['commit']}`")
    md.append(f"- **Operating System**: `{sys_env['os']}`")
    md.append(f"- **Python Version**: `{sys_env['python']}`")
    md.append(f"- **Python Path**: `{sys_env['python_path']}`")
    md.append(f"- **Node.js**: `{sys_env['node']}`")
    md.append(f"- **npm**: `{sys_env['npm']}`")
    
    # Section: Architecture & Storage
    md.append("\n## 📂 System Storage & Memory Architecture")
    md.append(f"Pantheon resolves all runtime state under its configured data directory: `{settings.data_dir}`.")
    md.append(f"\n- **Metadata Projects**: `{project_count}` projects registered in `projects.json`.")
    
    md.append("\n### SQLite Databases Status")
    for db_name, db_info in dbs.items():
        md.append(f"#### `{db_name}` Database")
        md.append(f"- File Size: `{db_info['size_kb']} KB`")
        if db_info["tables"]:
            md.append("- Tables & Row Counts:")
            for tbl, rows in db_info["tables"].items():
                md.append(f"  - `{tbl}`: {rows} rows")
        else:
            md.append("- Status: `No database file or empty`")
            
    md.append("\n### ChromaDB Vector Memory")
    md.append(f"- Status: `{chroma['status']}`")
    if chroma["collections"]:
        md.append("- Collections:")
        for col in chroma["collections"]:
            md.append(f"  - `{col['name']}`: {col['count']} vectors")
    else:
        md.append("- Collections: `No collections active`")

    # Section: Active Configuration State
    md.append("\n## ⚙️ Configuration State & LLM Settings")
    
    md.append("\n### Model Routing (task classes)")
    md.append(
        "Every LLM call declares a task class; each class uses its primary model and falls "
        "back down the list on errors. Classes: agent (chat/jobs/bots), code, quick and "
        "long_context (chat router only), extract (JSON extraction), summarize, vision, "
        "image_gen (the generate_image tool), embed, rerank. There are no 'chat'/'prefill' "
        "roles anymore — those were migrated to agent and summarize. Configure in "
        "Settings → Model routing."
    )
    try:
        from llm_config import router as _chat_router
        _rc = _chat_router.get_config()
        md.append(
            f"\n**Chat router:** {'on' if _rc['enabled'] else 'off'}"
            f" (classifier {'on' if _rc['classifier'] else 'off'}, quick ≤ {_rc['quick_max_chars']} chars). "
            "Each interactive chat turn picks a class before the tool loop starts: /model pin → "
            "image the agent model can't see → context too long → skill's model_class → "
            "code/traceback → recent code turns → short no-tool message (quick) → agent. A rule "
            "only fires when its class has its own tool-capable model. The user can pin a "
            "conversation with `/model <agent|quick|code|long_context|vision|auto>` or the model "
            "picker next to the chat input; each reply shows which model answered."
        )
        from llm_config import tuning as _tuning
        _recs = [r for r in _tuning.recommendations(24 * 7) if r["id"] != "more-data"]
        if _recs:
            md.append("\n**Routing tuning suggestions (last 7 days, from outcomes of routed turns; "
                      "the user applies them in Settings → Routing tuning):**")
            for r in _recs:
                md.append(f"- [{r['severity']}] {r['title']}: {r['detail']}")
    except Exception:
        pass
    if routing_rows:
        md.append("| Class | Position | Endpoint | Model | Profile / note |")
        md.append("| --- | --- | --- | --- | --- |")
        for r in routing_rows:
            md.append(f"| `{r['class']}` | {r['position']} | `{r['endpoint'] or '—'}` | "
                      f"`{r['model'] or '—'}` | {r['note']} |")
    else:
        md.append("*Routing table unavailable.*")
    if routing_warnings:
        md.append("\n**Routing warnings:**")
        for w in routing_warnings:
            md.append(f"- {w}")
    if recent_llm_errors:
        md.append("\n**LLM errors in the last 24h (from the call log):**")
        for w in recent_llm_errors:
            md.append(f"- {w}")

    md.append("\n### Configured LLM Endpoints")
    if endpoints:
        md.append("| Endpoint Name | API Type | Base URL |")
        md.append("| --- | --- | --- |")
        for ep in endpoints:
            md.append(f"| `{ep['name']}` | `{ep['api_type']}` | `{ep['base_url']}` |")
    else:
        md.append("*No saved endpoints configured in Vault.*")
        
    md.append("\n### Active Web Search Chain (Live Status)")
    md.append("The web search manager runs searches using the following chain of providers in order:")
    if search_chain:
        md.append("| Provider | Type | Enabled | API Key Set | Base URL | Daily Usage |")
        md.append("| --- | --- | --- | --- | --- | --- |")
        for p in search_chain:
            k_set = "Yes" if p["api_key_set"] else "No"
            enabled = "Yes" if p["enabled"] else "No"
            daily_usage = f"{p['daily_used']} / {p['daily_limit']}" if p["daily_limit"] > 0 else f"{p['daily_used']} / unlimited"
            md.append(f"| `{p['name']}` | `{p['type']}` | {enabled} | {k_set} | `{p['url']}` | `{daily_usage}` |")
    else:
        md.append("*No search providers configured.*")

    md.append("\n### Masked Environment Settings")
    md.append("| Configuration Key | Value |")
    md.append("| --- | --- |")
    for key, val in sorted(masked_settings.items()):
        md.append(f"| `{key}` | `{val}` |")

    # Section: Connected MCP Servers
    md.append("\n## 🔌 Connected MCP Connections")
    md.append("Model Context Protocol (MCP) servers connected to the manager:")
    if mcp_connections:
        md.append("| Server Name | URL | Enabled | Connected | Tools Count | Auth Type |")
        md.append("| --- | --- | --- | --- | --- | --- |")
        for conn in mcp_connections:
            enabled = "Yes" if conn["enabled"] else "No"
            connected = "Yes" if conn["connected"] else "No"
            md.append(f"| `{conn['name']}` | `{conn['url']}` | {enabled} | {connected} | {conn['tools_count']} | `{conn['auth_type']}` |")
    else:
        md.append("*No MCP connections configured.*")

    # Section: Skills Registry
    md.append("\n## 🧠 Registered Prompt Skills")
    md.append("Skills are modular prompt templates configured via `skill.json` and `instructions.md` that guide the agent's behavior.")
    if skills_list:
        md.append("| Skill Slug | Type | Description | Triggers | Required MCP Tools |")
        md.append("| --- | --- | --- | --- | --- |")
        for skill in skills_list:
            s_type = "Bundled" if skill["is_bundled"] else "User-Defined"
            triggers = ", ".join(f"`{t}`" for t in skill["triggers"]) if skill["triggers"] else "*None*"
            mcp = ", ".join(f"`{m}`" for m in skill["requires_mcp"]) if skill["requires_mcp"] else "*None*"
            md.append(f"| `{skill['name']}` | {s_type} | {skill['description']} | {triggers} | {mcp} |")
    else:
        md.append("*No skills currently loaded in registry.*")
        
    # 9. Messaging Integrations
    md.append("\n## 📡 Messaging Integrations")
    md.append("Configured external messaging platforms mapped to Pantheon projects:")
    try:
        from messaging.gateway import get_messaging_gateway
        gw = get_messaging_gateway()
        # generate_self_doc is sync; gw.status() is async (needs running
        # state). Report what's knowable synchronously.
        adapters = list(getattr(gw, "_adapters", {}).items())
        if adapters:
            md.append("| Platform | Display Name | Configured |")
            md.append("| --- | --- | --- |")
            for name, a in adapters:
                conf = "Yes" if a.is_configured() else "No"
                md.append(f"| `{name}` | {a.display_name} | {conf} |")
        else:
            md.append("*No messaging adapters loaded.*")
    except Exception as e:
        logger.warning("Could not list messaging adapters: %s", e)

    return "\n".join(md)

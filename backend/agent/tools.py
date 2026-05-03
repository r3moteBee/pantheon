"""Built-in tool definitions for the agent.

MCP integration: When external MCP servers are connected, their tools
are appended to TOOL_SCHEMAS dynamically via get_all_tool_schemas().
Tool calls prefixed with mcp_ are routed through the MCP manager.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def get_all_tool_schemas(project_id: str | None = None) -> list[dict[str, Any]]:
    """Return built-in tools + browser tools (if enabled) + any MCP-provided tools.

    project_id is accepted for back-compat but ignored — MCP servers are
    enabled globally now (per-project enablement was removed).
    """
    schemas = list(TOOL_SCHEMAS)
    try:
        from agent.browser_tools import browser_enabled, BROWSER_TOOL_SCHEMAS
        if browser_enabled():
            schemas.extend(BROWSER_TOOL_SCHEMAS)
    except Exception as e:
        logger.debug("Browser tools unavailable: %s", e)
    try:
        from mcp_client.manager import get_mcp_manager
        mgr = get_mcp_manager()
        mcp_schemas = mgr.get_all_tool_schemas() or []
        if mcp_schemas:
            schemas.extend(mcp_schemas)
            logger.debug("Added %d MCP tools to agent schema", len(mcp_schemas))
    except Exception as e:
        logger.debug("No MCP tools available: %s", e)
    return schemas


# Tool schemas (OpenAI function calling format)
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Store information in memory for future recall. Choose the tier based on importance: 'working' for temporary context, 'episodic' for conversation facts, 'semantic' for key insights/knowledge, 'graph' for relationships between concepts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The information to remember"},
                    "tier": {
                        "type": "string",
                        "enum": ["working", "episodic", "semantic"],
                        "description": "Memory tier to store in"
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Optional metadata tags",
                        "additionalProperties": True
                    }
                },
                "required": ["content", "tier"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": (
                "Search the project's memory across episodic "
                "(past chats), semantic (indexed artifacts and "
                "workspace files), and graph (linked concepts) "
                "tiers. ARTIFACTS are first-class memory: any "
                "transcript, note, or document saved via "
                "save_to_artifact is indexed into semantic + "
                "graph and turns up here. Always try this BEFORE "
                "telling the user you can't find something — "
                "the artifact may already be indexed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for"},
                    "tiers": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["episodic", "semantic", "graph"]},
                        "description": "Which memory tiers to search (default: all)"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_graph_node",
            "description": "Create a node in the associative graph memory to represent a concept, person, project, or fact.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_type": {
                        "type": "string",
                        "enum": ["concept", "person", "project", "event", "fact"],
                        "description": "Type of the node"
                    },
                    "label": {"type": "string", "description": "Human-readable name for the node"},
                    "metadata": {
                        "type": "object",
                        "description": "Additional properties for this node",
                        "additionalProperties": True
                    }
                },
                "required": ["node_type", "label"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "link_concepts",
            "description": "Create a relationship edge between two nodes in the associative graph memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_a_label": {"type": "string", "description": "Label of the first node"},
                    "node_b_label": {"type": "string", "description": "Label of the second node"},
                    "relationship": {"type": "string", "description": "Description of the relationship (e.g., 'works on', 'is related to', 'caused by')"}
                },
                "required": ["node_a_label", "node_b_label", "relationship"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a transient file from the workspace SCRATCH AREA "
                "(data/workspace/, used by sandbox runs). DOES NOT read "
                "artifacts. To read a saved artifact (notes, transcripts, "
                "code, chat exports, anything created via save_to_artifact "
                "or save_last_response), use `read_artifact` instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to the file within the workspace"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write a transient file to the workspace SCRATCH AREA "
                "(data/workspace/). NOT for durable content — to save "
                "anything the user might want to keep, search, or open "
                "later (notes, transcripts, code, reports, etc), use "
                "`save_to_artifact` instead. Workspace files are not "
                "indexed into memory and may be cleaned up."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to the file within the workspace"},
                    "content": {"type": "string", "description": "Content to write to the file"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_workspace_files",
            "description": (
                "List files in the workspace SCRATCH AREA only "
                "(data/workspace/). DOES NOT list artifacts. To verify "
                "or browse saved artifacts (everything created via "
                "save_to_artifact, save_last_response, save_chat_as_artifact, or scheduled-task output sinks), use `list_artifacts` "
                "with the appropriate path_prefix. Example for "
                "verifying a folder of saved transcripts: "
                "list_artifacts(path_prefix='NBJ/')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Subdirectory path to list (default: root workspace)", "default": ""}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information. Returns titles, URLs, and snippets for the top results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": (
                "Schedule an autonomous task. By default the schedule is "
                "created in PAUSED state with plan_status='proposed' so "
                "the user can REVIEW and APPROVE the plan before it runs. "
                "The task only fires after they click Approve in the Tasks "
                "tab.\n\n"
                "BEFORE calling this tool you should:\n"
                "  1. Survey what tools you actually have available — MCP "
                "     tools (mcp_*), skills, github_*, save_to_artifact, "
                "     web_search — and reference SPECIFIC ONES by NAME in "
                "     the plan. Don't write 'fetch the transcript'; write "
                "     'fetch via mcp_SubDownload_fetch_transcript'.\n"
                "  2. If the user mentioned a specific tool / skill / MCP, "
                "     anchor the relevant step on that exact name.\n"
                "  3. If a step needs a tool you don't have, write that "
                "     into the plan explicitly so the user can correct you "
                "     before approval — DO NOT pretend you have it.\n\n"
                "Use skip_review=true ONLY when the user explicitly said "
                "'just do it' or 'no need to review'. Default to plan "
                "review for any multi-step or non-trivial work."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Short recognizable label shown in the Tasks list "
                            "(3-7 words, imperative). Examples: 'Daily PR digest', "
                            "'SUSE blog research'. DO NOT use 'task', 'job', "
                            "or 'reminder'."
                        )
                    },
                    "description": {
                        "type": "string",
                        "description": "Detailed task description — what the agent should do."
                    },
                    "schedule": {
                        "type": "string",
                        "description": (
                            "ONE-SHOT: 'now' / 'delay:N' (run once in N "
                            "minutes — e.g. delay:2). RECURRING: 'interval:N' "
                            "(every N minutes, forever — e.g. interval:60 = "
                            "hourly); cron expression like '0 9 * * *' = "
                            "daily at 9am. 'in 2 minutes' is delay:2 NOT "
                            "interval:120."
                        )
                    },
                    "plan": {
                        "type": "string",
                        "description": (
                            "REQUIRED. Markdown plan with numbered steps. "
                            "For each step name the EXACT tool you intend to "
                            "use. Example:\n"
                            "  1. Resolve channel via "
                            "     `mcp_SubDownload_resolve_channel` (query="
                            "     \"Nate B Jones\").\n"
                            "  2. Fetch latest 3 videos via "
                            "     `mcp_SubDownload_get_channel_latest_videos`.\n"
                            "  3. For each, fetch transcript via "
                            "     `mcp_SubDownload_fetch_transcript`.\n"
                            "  4. Save each via `save_to_artifact` with path "
                            "     NBJ/{date}-{title}.md.\n"
                            "  5. Use the topic-extractor skill to add 3-5 "
                            "     tags to each artifact via `update_artifact`.\n"
                            "If the user mentions a specific skill or MCP, "
                            "wire it in by exact name."
                        )
                    },
                    "skip_review": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Default false. Set true ONLY when the user "
                            "explicitly says they don't want to review the "
                            "plan before it runs."
                        )
                    }
                },
                "required": ["name", "description", "schedule", "plan"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_telegram",
            "description": "Send a message to the operator via Telegram. Use for important updates, task completions, or when you need human input on a long-running task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Message to send"}
                },
                "required": ["message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "index_workspace",
            "description": "Index workspace files into semantic memory and knowledge graph. Makes file contents searchable via recall. Supports Markdown (with YAML frontmatter), text, CSV, PDF, and code files. Can index a single file or entire directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to a file or directory in the workspace. Empty string indexes the entire workspace.",
                        "default": ""
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Re-index even if file hasn't changed (default: false)",
                        "default": False
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_skill",
            "description": (
                "Create a reusable, callable SKILL — NOT a scheduled "
                "task. Use this when the user says 'create a skill', "
                "'make this reusable', 'turn this workflow into a "
                "skill', 'I want to do this again later', or describes "
                "a multi-step procedure they\'ll want to run multiple "
                "times. After creation the user can invoke the skill "
                "with `/skill-name` in any chat, and it injects the "
                "instructions into the agent\'s system prompt for that "
                "turn. Distinct from create_task (which schedules an "
                "AUTONOMOUS RUN). Skills are reusable; tasks fire on "
                "a schedule and produce job runs.\n\n"
                "The instructions field should be the full markdown "
                "workflow definition the agent will follow each time "
                "the skill fires — list exact tool names per step "
                "(e.g. mcp_SubDownload_search_youtube, save_to_artifact, "
                "create_graph_node) so the agent knows what to call. "
                "Include any schemas, output contracts, or thresholds "
                "the user already agreed to."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Slug-form name (lowercase, hyphens, no "
                            "spaces). Used as the /skill-name invocation."
                        )
                    },
                    "description": {
                        "type": "string",
                        "description": "One-line description shown in the skills list."
                    },
                    "triggers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Phrases that should auto-suggest this skill. "
                            "Pick content-bearing phrases, not stopwords. "
                            "Example: ['fetch youtube transcript', "
                            "'ingest video into graph']."
                        )
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Topical tags for filtering."
                    },
                    "instructions": {
                        "type": "string",
                        "description": (
                            "Full markdown workflow definition. The "
                            "agent reads this when the skill is "
                            "invoked. Be specific about tool names, "
                            "parameter contracts, and output paths."
                        )
                    }
                },
                "required": ["name", "description", "instructions"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "index_artifact",
            "description": (
                "Index one artifact, or every artifact under a path "
                "prefix, into semantic memory + knowledge graph. New "
                "artifacts are auto-indexed on save; use this to "
                "backfill artifacts saved before that landed, to "
                "force-reindex after editing tags, or to bulk-ingest "
                "a folder like NBJ/. Provide either id (one artifact) "
                "or path_prefix (folder) — pass a bare folder name like "
                "'NBJ/' (the project slug is added automatically)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Single artifact id to index."
                    },
                    "path_prefix": {
                        "type": "string",
                        "description": "Folder prefix; index every text artifact whose path starts with this prefix. Pass bare folder name like 'NBJ/'."
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Re-index even if content hash is unchanged (default false).",
                        "default": False
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_transcript_artifact",
            "description": (
                "Server-side fetch + save for YouTube transcripts. "
                "Use this INSTEAD OF copying the transcript text into "
                "save_to_artifact's content field — that pattern gets "
                "the transcript silently truncated when the LLM "
                "abbreviates long strings with \"...\".\n\n"
                "Pass the video_id, the desired artifact path "
                "(bare folder; project slug auto-prepended), and the "
                "YAML frontmatter as a string. The backend will:\n"
                "  1. Call mcp_SubDownload_fetch_transcript(video_id) "
                "internally.\n"
                "  2. Stitch '---\\n{frontmatter}\\n---\\n\\n"
                "{transcript}' as the artifact body.\n"
                "  3. Save via the artifact store (with auto-suffix "
                "for path collisions and graph indexing).\n\n"
                "Use for every YouTube transcript ingest in skills "
                "like /content-ingest-graph. The agent supplies "
                "structure (frontmatter, path, title); the server "
                "supplies the verbatim transcript content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "video_id": {
                        "type": "string",
                        "description": "YouTube video id (e.g. dQw4w9WgXcQ)."
                    },
                    "path": {
                        "type": "string",
                        "description": "Artifact path. Pass a bare folder/file path like 'youtube-transcripts/{date}/{channel}/{video_id}-{slug}.md'. Project slug is prepended automatically."
                    },
                    "frontmatter": {
                        "type": "string",
                        "description": "YAML frontmatter as a string, WITHOUT the surrounding '---' fences. The backend wraps it. Include source.type, video_title, channel_name, topics[], etc. per the skill spec."
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional artifact title shown in the Artifacts list."
                    }
                },
                "required": ["video_id", "path", "frontmatter"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_last_response",
            "description": "Save conversation history to a file in the project workspace. By default saves your immediately preceding assistant message verbatim. Can also summarize, expand via research, or apply a custom transform across the last N messages. Use this whenever the user says 'save this', 'remember that observation', 'write a note about the last N messages', 'summarize the above and save it', etc. — do NOT ask the user to restate content that is already in the conversation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative file path, e.g. 'ANALYSIS/2026-04-07-ai-maturity.md'"},
                    "history_count": {"type": "integer", "description": "How many of the most recent messages (both user and assistant) to include as source material. 1 = just the last assistant reply (default). Use a larger number when the user references 'the last few messages' or 'the conversation so far'.", "default": 1},
                    "mode": {
                        "type": "string",
                        "enum": ["verbatim", "summarize", "research", "custom"],
                        "description": "verbatim = save source material as-is (default for history_count=1). summarize = condense into key points. research = run web_search/recall to expand on the source material and produce a researched note. custom = apply the instructions in custom_prompt to the source material.",
                        "default": "verbatim"
                    },
                    "custom_prompt": {"type": "string", "description": "Required when mode='custom'. Instructions for how to transform the source messages (e.g. 'extract action items', 'rewrite as a formal brief')."},
                    "title": {"type": "string", "description": "Optional title for YAML frontmatter (markdown only)"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional list of tags for YAML frontmatter"},
                    "prepend_header": {"type": "boolean", "description": "If true, prepend a title+date header to the content (default true for .md files)", "default": True}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "show_file",
            "description": "Display a file inline in the chat UI. Supports images (png/jpg/gif/svg/webp), PDFs, HTML, markdown, and text files. Use this instead of read_file when the user asks to 'show', 'display', or 'view' a file. The file will be rendered as a visual preview in the chat. IMPORTANT: Only call this ONCE per file — a single successful call displays the file. Never retry or call again for the same file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to the file within the workspace"},
                    "caption": {"type": "string", "description": "Optional caption to display below the file"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "download_file",
            "description": "Download a file from a URL and save it to the workspace. Use this when the user asks you to download, fetch, or save a file from the internet (PDFs, images, documents, data files, etc.). The file is saved to the specified path in the workspace and can then be viewed with show_file or read with read_file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to download the file from"},
                    "path": {"type": "string", "description": "Relative path in workspace to save the file (e.g., 'documents/report.pdf'). Directories are created automatically."},
                    "filename": {"type": "string", "description": "Optional filename override. If omitted, derived from the URL or Content-Disposition header."}
                },
                "required": ["url", "path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_job_status",
            "description": (
                "Get the current state of a background job. Pass either "
                "a job UUID (full id from list_recent_jobs) OR a "
                "schedule_id (the short 8-char hex id returned by "
                "create_task — refers to the schedule, not a single "
                "run). When given a schedule_id, this returns the most "
                "recent run of that schedule. Use this when the user "
                "asks about a task you started earlier — READ THE "
                "ACTUAL STATUS before claiming the job did/didn't run. "
                "Returns status, progress text, error, result, "
                "session_id, artifact_id, and pr_url where applicable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"}
                },
                "required": ["job_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_recent_jobs",
            "description": (
                "List recent background jobs for the active project. Use "
                "this when the user asks 'what are you working on' / "
                "'is anything still running' / 'did that finish'. Filter "
                "by status when needed. By default omits system job types "
                "(extraction, file_indexing) so the list is user-relevant."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["queued","running","completed","failed","cancelled","stalled"],
                        "description": "Optional status filter."
                    },
                    "include_system": {
                        "type": "boolean",
                        "description": "Include extraction/file_indexing rows. Default false.",
                        "default": False
                    },
                    "limit": {"type": "integer", "default": 10}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "consolidate_memory",
            "description": "Run memory consolidation: summarize the current session, extract entities/facts/relationships from recent conversation, and store them in semantic and graph memory. Use at the end of a productive conversation or when the user asks you to remember what was discussed.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "code_execute",
            "description": (
                "Execute a code snippet in an isolated sandbox and return its "
                "stdout, stderr, and exit code. Use this to test code, run "
                "computations, validate logic, generate data, or prototype "
                "before committing. Supports Python, Node, and Bash. The "
                "sandbox has a default 30-second timeout and 256 MB memory "
                "limit. Output is truncated at 1 MB. In subprocess mode the "
                "snippet runs on the host with no filesystem isolation; use "
                "Firecracker mode (PANTHEON_SANDBOX=firecracker) for real "
                "isolation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "enum": ["python", "node", "javascript", "bash"],
                        "description": "Runtime for the snippet."
                    },
                    "code": {
                        "type": "string",
                        "description": "The code to execute."
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional filename for the script (e.g. 'analysis.py'). Defaults are language-appropriate."
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Optional timeout override (1-300 seconds, default 30)."
                    }
                },
                "required": ["language", "code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_list_connections",
            "description": "List GitHub connections (repos linked to this project). Returns connection ids, repos, and default branches. Use this to discover which repos the agent can act on.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_read_file",
            "description": "Read a file from a connected GitHub repository. Returns the file content. Use to understand existing code before making changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "connection_id": {"type": "string", "description": "ID from github_list_connections. If omitted, the default connection for the active project is used."},
                    "path": {"type": "string", "description": "Path within the repo, e.g. 'src/main.py'"},
                    "ref": {"type": "string", "description": "Optional branch or commit sha. Defaults to the default branch."}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_list_directory",
            "description": "List files and folders at a path in a connected GitHub repo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "connection_id": {"type": "string"},
                    "path": {"type": "string", "description": "Directory path; '' for repo root.", "default": ""},
                    "ref": {"type": "string"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_create_branch",
            "description": "Create a new branch off the default branch (or a named base branch) in a connected GitHub repo. Use before making changes the user will review via PR.",
            "parameters": {
                "type": "object",
                "properties": {
                    "connection_id": {"type": "string"},
                    "new_branch": {"type": "string"},
                    "base_branch": {"type": "string", "description": "Optional base branch; defaults to repo default."}
                },
                "required": ["new_branch"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_write_files",
            "description": "Atomically commit one or more files to a branch via the GitHub Trees API. Use after github_create_branch to land changes the user will review.",
            "parameters": {
                "type": "object",
                "properties": {
                    "connection_id": {"type": "string"},
                    "branch": {"type": "string"},
                    "message": {"type": "string", "description": "Commit message"},
                    "files": {
                        "type": "array",
                        "description": "Array of {path, content}. All files commit in a single atomic commit.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"}
                            },
                            "required": ["path", "content"]
                        }
                    }
                },
                "required": ["branch", "message", "files"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_create_pr",
            "description": "Open a pull request from one branch into another in a connected GitHub repo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "connection_id": {"type": "string"},
                    "title": {"type": "string"},
                    "head": {"type": "string", "description": "Branch with the changes"},
                    "base": {"type": "string", "description": "Branch to merge into; defaults to repo default."},
                    "body": {"type": "string", "description": "PR body / description"},
                    "draft": {"type": "boolean", "default": False}
                },
                "required": ["title", "head"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_merge_pr",
            "description": "Merge a previously-opened pull request. Use only when the user explicitly approves merging.",
            "parameters": {
                "type": "object",
                "properties": {
                    "connection_id": {"type": "string"},
                    "pr_number": {"type": "integer"},
                    "merge_method": {"type": "string", "enum": ["merge", "squash", "rebase"], "default": "squash"}
                },
                "required": ["pr_number"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "start_coding_task",
            "description": (
                "STRICT SCOPE: Use ONLY when the user explicitly asks "
                "you to AUTHOR CODE in their GitHub repo — write a "
                "function, fix a bug, add a feature, refactor, open a "
                "PR, etc. The background agent will branch off the "
                "bound repo, edit files, commit, and open a pull "
                "request.\n\n"
                "Do NOT use this tool for: data ingestion, running a "
                "workflow/skill, fetching transcripts, indexing "
                "artifacts, building a graph, summarizing, research, "
                "search, or any non-code task — even if the user says "
                "'do this in the background'. For those, run the steps "
                "inline this turn or invoke the matching skill (see "
                "'Available skills'). For scheduled fire-and-forget "
                "runs, use create_task.\n\n"
                "Triggers that DO match: 'fix bug X', 'implement "
                "feature Y', 'open a PR that adds Z', 'refactor module "
                "M'. Triggers that DO NOT match: 'fetch X', 'ingest "
                "Y', 'summarize Z', 'index W', 'research V'.\n\n"
                "BEFORE calling, use github_list_directory + "
                "github_read_file to build a coding_context string. "
                "Returns a job_id; track via get_job_status."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_description": {
                        "type": "string",
                        "description": "Detailed description of what to build/fix/change."
                    },
                    "title": {
                        "type": "string",
                        "description": "Short title shown in the Tasks UI."
                    },
                    "coding_context": {
                        "type": "string",
                        "description": "Project context — tech stack, file layout, conventions."
                    },
                    "branch_name": {
                        "type": "string",
                        "description": "Optional explicit branch name; auto-generated otherwise."
                    },
                    "base_branch": {
                        "type": "string",
                        "description": "Optional override for the base branch; defaults to bound repo's default."
                    }
                },
                "required": ["task_description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_to_artifact",
            "description": (
                "Save content into the user's Pantheon artifact store. THIS IS THE ONLY tool that persists anything into `list_artifacts` / `read_artifact` / project-scoped recall. MCP `save_*` / `*_save_to_library` / `*_upload_file` tools are NOT equivalents — they save to external services that Pantheon cannot see. Always use `save_to_artifact` when a skill says \"save as artifact\" or when the user asks you to save something for later use in this project. Pass a bare path like 'youtube-transcripts/foo.md' — the project slug is prepended automatically. UNIQUE-path collisions are auto-resolved by suffixing -1, -2, etc. Returns the saved artifact id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Logical path inside the project artifact tree."},
                    "content": {"type": "string", "description": "Full content body."},
                    "content_type": {"type": "string", "description": "Mime-ish type. Defaults to text/markdown.", "default": "text/markdown"},
                    "title": {"type": "string", "description": "Optional human title."},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags."}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_artifact",
            "description": "Replace the content of an existing artifact, creating a new version. Use when the user asks to revise an existing note/file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Artifact id."},
                    "content": {"type": "string"},
                    "edit_summary": {"type": "string", "description": "Optional commit-message-style note."}
                },
                "required": ["id", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_artifact",
            "description": "Read an artifact by id or by path. Use to surface previously saved content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "path": {"type": "string", "description": "Logical path; alternative to id."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_artifacts",
            "description": (
                "List artifacts in the active project. To find files in a folder use path_prefix with a BARE folder name like 'NBJ/' — do NOT include the project slug yourself; the tool prepends it for you (so 'NBJ/' and 'default-project/NBJ/' both work). To find a single file use read_artifact with id or path."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string"},
                    "content_type": {"type": "string"},
                    "path_prefix": {"type": "string"},
                    "search": {"type": "string"},
                    "limit": {"type": "integer", "default": 20}
                },
                "required": []
            }
        }
    }
]


async def execute_tool(
    tool_name: str,
    tool_args: dict[str, Any],
    memory_manager: Any,
    project_id: str | None = None,
    session_id: str | None = None,
    last_assistant_text: str = "",
) -> str:
    """Execute a tool call and return the result as a string."""
    try:
        effective_project = project_id or "default"

        # Route MCP tool calls through the MCP manager
        if tool_name.startswith("mcp_"):
            try:
                from mcp_client.manager import get_mcp_manager
                mgr = get_mcp_manager()
                return await mgr.execute_tool(tool_name, tool_args)
            except Exception as e:
                logger.error("MCP tool dispatch failed for '%s': %s", tool_name, e)
                return f"MCP tool error: {e}"

        if tool_name == "remember":
            from memory.manager import create_memory_manager
            mgr = create_memory_manager(project_id=effective_project)
            tier = tool_args.get("tier", "semantic")
            content = tool_args["content"]
            metadata = tool_args.get("metadata", {})
            ref = await mgr.remember(content=content, tier=tier, metadata=metadata)
            return f"Stored in {tier} memory: {content[:100]} ({ref})"

        elif tool_name == "recall":
            from memory.manager import create_memory_manager
            mgr = create_memory_manager(project_id=effective_project)
            tiers = tool_args.get("tiers", ["semantic", "episodic", "graph"])
            query = tool_args["query"]
            results = await mgr.recall(query=query, tiers=tiers, project_id=effective_project)
            if not results:
                return "No memories found."

            def _format(r):
                tier = r.get("tier", "?")
                content = (r.get("content") or "")[:400]
                meta = r.get("metadata") or {}
                # Detect artifact-origin chunks — index_text writes these.
                artifact_id = meta.get("fm_artifact_id")
                artifact_path = meta.get("fm_artifact_path") or meta.get("source_path") or ""
                if artifact_id or (isinstance(artifact_path, str) and artifact_path.startswith("artifact://")):
                    label = artifact_path.split("artifact://", 1)[-1].split("/", 1)[-1] if "artifact://" in artifact_path else artifact_path
                    tags = meta.get("fm_tags") or ""
                    tag_part = f" tags=[{tags}]" if tags else ""
                    return (
                        f"[{tier}/artifact] {content}\n"
                        f"  ↳ source: {label}"
                        f"  id={artifact_id or '?'}{tag_part}"
                    )
                # Workspace-file chunk
                src_file = meta.get("source_file") or meta.get("source_path")
                if src_file and tier == "semantic":
                    return f"[{tier}/file:{src_file}] {content}"
                # Episodic
                if tier == "episodic":
                    sid = meta.get("session_id") or "?"
                    ts = meta.get("timestamp") or ""
                    return f"[{tier} session={sid[:8]} {ts}] {content}"
                return f"[{tier}] {content}"

            lines = [_format(r) for r in results[:10]]
            return "\n\n".join(lines)

        elif tool_name == "create_graph_node":
            from memory.graph import GraphMemory
            graph = GraphMemory(project_id=effective_project)
            label = tool_args["label"]
            node_type = tool_args.get("node_type", "concept")
            metadata = tool_args.get("metadata", {})
            node_id = await graph.add_node(node_type=node_type, label=label, metadata=metadata)
            return f"Graph node created: {label} (type: {node_type}, id: {node_id})"

        elif tool_name == "link_concepts":
            from memory.graph import GraphMemory
            graph = GraphMemory(project_id=effective_project)
            node_a = tool_args["node_a_label"]
            node_b = tool_args["node_b_label"]
            relationship = tool_args["relationship"]
            result = await graph.add_edge_by_label(label_a=node_a, label_b=node_b, relationship=relationship)
            return result

        elif tool_name == "show_file":
            safe_path = _safe_workspace_path(tool_args["path"], project_id)
            if not safe_path.exists():
                return f"File not found: {tool_args['path']}"
            rel_path = tool_args["path"]
            caption = tool_args.get("caption", "")
            suffix = safe_path.suffix.lower()
            from urllib.parse import quote
            encoded_path = quote(rel_path, safe="/")
            # Return structured result: display directive for frontend + clear success for LLM
            # The [DISPLAY:...] tag is parsed by the frontend to render a preview.
            # IMPORTANT: Do NOT call show_file again for this file — it is already displayed.
            size_kb = safe_path.stat().st_size / 1024
            return (
                f"[DISPLAY:workspace://{encoded_path}]\n"
                f"Successfully displayed {safe_path.name} ({size_kb:.0f}KB) inline in the chat. "
                f"The user can now see the file. Do not call show_file again for this file."
            )

        elif tool_name == "download_file":
            url = tool_args.get("url", "")
            dest_path_str = tool_args.get("path", "")
            if not url or not dest_path_str:
                return "Error: both 'url' and 'path' are required"

            safe_path = _safe_workspace_path(dest_path_str, project_id)
            safe_path.parent.mkdir(parents=True, exist_ok=True)

            # If path is a directory-like destination, derive filename from URL
            filename = tool_args.get("filename")
            if not filename and not safe_path.suffix:
                # No extension in path — treat as directory, derive filename from URL
                from urllib.parse import urlparse, unquote
                url_path = urlparse(url).path
                filename = unquote(url_path.split("/")[-1]) or "download"
                safe_path = safe_path / filename

            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()

                    # Try to get filename from Content-Disposition if we still need one
                    if not safe_path.suffix:
                        cd = resp.headers.get("content-disposition", "")
                        if "filename=" in cd:
                            import re as _re
                            match = _re.search(r'filename[*]?=["\']?([^"\';]+)', cd)
                            if match:
                                safe_path = safe_path.parent / match.group(1).strip()

                    safe_path.write_bytes(resp.content)
                    size_kb = len(resp.content) / 1024
                    content_type = resp.headers.get("content-type", "unknown")
                    return (
                        f"Downloaded {safe_path.name} ({size_kb:.1f}KB, {content_type}) "
                        f"to {dest_path_str}. Use show_file to display it or read_file to read its contents."
                    )
            except httpx.HTTPStatusError as e:
                return f"Download failed: HTTP {e.response.status_code} from {url}"
            except httpx.RequestError as e:
                return f"Download failed: {type(e).__name__}: {e}"
            except Exception as e:
                return f"Download failed: {e}"

        elif tool_name == "read_file":
            safe_path = _safe_workspace_path(tool_args["path"], project_id)
            if not safe_path.exists():
                return f"File not found: {tool_args['path']}"

            suffix = safe_path.suffix.lower()

            # PDF — extract text with pdfplumber, vision OCR fallback for scanned pages
            if suffix == ".pdf":
                try:
                    import pdfplumber
                    pages = []
                    blank_page_nums = []
                    with pdfplumber.open(safe_path) as pdf:
                        total_pages = len(pdf.pages)
                        for i, page in enumerate(pdf.pages):
                            text = (page.extract_text() or "").strip()
                            if text:
                                pages.append(f"--- Page {i + 1} ---\n{text}")
                            else:
                                blank_page_nums.append(i)

                    # Vision OCR for scanned/image-based pages
                    if blank_page_nums:
                        try:
                            from memory.file_indexer import _ocr_pdf_pages
                            ocr_results = await _ocr_pdf_pages(safe_path, blank_page_nums)
                            for pn, desc in sorted(ocr_results.items()):
                                pages.append(f"--- Page {pn + 1} (OCR) ---\n{desc}")
                        except Exception as ocr_err:
                            for pn in blank_page_nums:
                                pages.append(f"--- Page {pn + 1} ---\n[Scanned/image page — vision OCR unavailable: {ocr_err}]")

                    if pages:
                        # Sort by page number for correct order
                        pages.sort(key=lambda p: int(p.split("Page ")[1].split(" ")[0].split("---")[0]))
                        return "\n\n".join(pages)
                    return f"[PDF has {total_pages} page(s) but no extractable text and OCR failed]"
                except Exception as e:
                    return f"Error reading PDF: {e}"

            # Binary file types — return metadata instead of garbled content
            if suffix in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
                          ".mp3", ".mp4", ".wav", ".zip", ".tar", ".gz",
                          ".exe", ".dll", ".so", ".bin", ".dat"}:
                size = safe_path.stat().st_size
                return f"[Binary file: {safe_path.name}, {size:,} bytes — use a specialized tool to process this file type]"

            return safe_path.read_text(encoding="utf-8", errors="replace")

        elif tool_name == "write_file":
            safe_path = _safe_workspace_path(tool_args["path"], project_id)
            safe_path.parent.mkdir(parents=True, exist_ok=True)
            safe_path.write_text(tool_args["content"], encoding="utf-8")
            return f"File written: {tool_args['path']} ({len(tool_args['content'])} bytes)"

        elif tool_name == "list_workspace_files":
            base = _get_workspace_base(project_id)
            sub = tool_args.get("path", "")
            target = (base / sub) if sub else base
            target = target.resolve()
            if not str(target).startswith(str(base)):
                return "Access denied: path outside workspace"
            if not target.exists():
                return f"Directory not found: {sub}"
            entries = []
            for item in sorted(target.iterdir()):
                kind = "dir" if item.is_dir() else "file"
                size = item.stat().st_size if item.is_file() else 0
                entries.append(f"[{kind}] {item.name}" + (f" ({size} bytes)" if kind == "file" else ""))
            return "\n".join(entries) if entries else "Empty directory"

        elif tool_name == "save_last_response":
            history_count = max(1, int(tool_args.get("history_count", 1)))
            mode = (tool_args.get("mode") or "verbatim").lower()
            custom_prompt = tool_args.get("custom_prompt", "")

            # ── Gather source messages ──────────────────────────────────
            source_msgs: list[dict[str, Any]] = []
            if session_id:
                try:
                    from memory.manager import create_memory_manager
                    mgr = create_memory_manager(project_id=effective_project, session_id=session_id)
                    history = await mgr.episodic.get_history(session_id=session_id, limit=200)
                    # history is ASC; take last N that have content
                    filtered = [r for r in history if r.get("content")]
                    source_msgs = filtered[-history_count:] if filtered else []
                except Exception as e:
                    logger.warning("save_last_response episodic fallback failed: %s", e)

            # If nothing in episodic yet, fall back to the in-memory last assistant text
            if not source_msgs and last_assistant_text:
                source_msgs = [{"role": "assistant", "content": last_assistant_text}]

            if not source_msgs:
                return (
                    "No prior messages found in the current session. "
                    "Ask the user for the content they want saved and then call write_file directly."
                )

            # ── Build source text block ─────────────────────────────────
            if history_count == 1 and source_msgs[-1].get("role") == "assistant":
                source_text = source_msgs[-1]["content"]
                source_label = "last assistant response"
            else:
                lines = []
                for m in source_msgs:
                    role = m.get("role", "?").upper()
                    lines.append(f"[{role}]\n{m.get('content','')}")
                source_text = "\n\n".join(lines)
                source_label = f"last {len(source_msgs)} messages"

            # ── Transform via mode ──────────────────────────────────────
            content_body = source_text
            try:
                if mode == "verbatim":
                    content_body = source_text
                elif mode in ("summarize", "research", "custom"):
                    from models.provider import get_provider
                    provider = get_provider()
                    if mode == "summarize":
                        instr = (
                            "Summarize the following conversation excerpt into clear, "
                            "structured markdown notes. Preserve key facts, figures, and "
                            "named entities. Use headers and bullet points where helpful."
                        )
                    elif mode == "research":
                        # Do a quick web_search pass to enrich
                        try:
                            search_query = source_text[:400]
                            enriched = await _web_search(search_query)
                        except Exception:
                            enriched = ""
                        instr = (
                            "Expand the following source material into a researched briefing. "
                            "Integrate the additional search results below where relevant, "
                            "cite sources inline, and produce a polished markdown note."
                        )
                        if enriched:
                            source_text = source_text + "\n\n---\n\n## Additional search results\n" + enriched
                    else:  # custom
                        if not custom_prompt:
                            return "mode='custom' requires a custom_prompt argument."
                        instr = custom_prompt

                    prompt_messages = [
                        {"role": "system", "content": "You transform conversation excerpts into well-formatted markdown notes. Return only the note body — no preamble."},
                        {"role": "user", "content": f"{instr}\n\n---\nSOURCE ({source_label}):\n\n{source_text}"},
                    ]
                    transformed = ""
                    async for chunk in provider.chat(messages=prompt_messages, tools=[], stream=False):
                        if isinstance(chunk, dict):
                            transformed += chunk.get("content", "") or ""
                        elif isinstance(chunk, str):
                            transformed += chunk
                    content_body = transformed.strip() or source_text
                else:
                    return f"Unknown mode: {mode}"
            except Exception as e:
                logger.exception("save_last_response transform failed")
                return f"Transform failed ({mode}): {e}"

            # ── Write file ──────────────────────────────────────────────
            rel = tool_args["path"]
            safe = _safe_workspace_path(rel, project_id)
            safe.parent.mkdir(parents=True, exist_ok=True)
            content = content_body
            if safe.suffix.lower() in (".md", ".markdown") and tool_args.get("prepend_header", True):
                from datetime import datetime
                title = tool_args.get("title") or safe.stem.replace("-", " ").replace("_", " ").title()
                tags = tool_args.get("tags") or []
                frontmatter = [
                    "---",
                    f"title: {title}",
                    f"date: {datetime.utcnow().strftime('%Y-%m-%d')}",
                    f"mode: {mode}",
                    f"source_messages: {len(source_msgs)}",
                ]
                if tags:
                    frontmatter.append("tags: [" + ", ".join(tags) + "]")
                frontmatter.append("source: agent_response")
                frontmatter.append("---")
                content = "\n".join(frontmatter) + "\n\n# " + title + "\n\n" + content_body
            safe.write_text(content, encoding="utf-8")
            return f"Saved {source_label} ({mode}, {len(content_body)} chars) to {rel}"

        elif tool_name == "web_search":
            return await _web_search(tool_args["query"])

        elif tool_name.startswith("browser_"):
            from agent.browser_tools import browser_enabled, execute_browser_tool
            if not browser_enabled():
                return "Browser tools are disabled. Set BROWSER_ENABLED=true in .env and install Playwright."
            return await execute_browser_tool(tool_name, tool_args, effective_project)

        elif tool_name == "save_transcript_artifact":
            from artifacts.store import get_store, is_text_type, project_slug as _ps_st
            from artifacts import embedder as _emb_st
            from mcp_client.manager import get_mcp_manager
            import sqlite3 as _sqlite3st
            import json as _json_st

            video_id = (tool_args.get("video_id") or "").strip()
            raw_path = (tool_args.get("path") or "").strip()
            frontmatter = (tool_args.get("frontmatter") or "").strip()
            title = tool_args.get("title")
            if not video_id or not raw_path or not frontmatter:
                return (
                    "save_transcript_artifact rejected: video_id, path, "
                    "and frontmatter are all required."
                )

            # Strip any --- fences the agent may have included; we add
            # them ourselves so the result is always well-formed.
            fm = frontmatter
            if fm.startswith("---"):
                fm = fm[3:].lstrip("\n")
            if fm.endswith("---"):
                fm = fm[:-3].rstrip("\n")
            fm = fm.strip("\n").strip()

            # 1. Fetch transcript from the MCP server.
            mgr = get_mcp_manager()
            try:
                fetch_result = await mgr.execute_tool(
                    "mcp_SubDownload_fetch_transcript",
                    {"video_id": video_id, "save": False},
                )
            except Exception as e:
                return f"save_transcript_artifact: transcript fetch failed: {e}"

            # The MCP wrapper may return either a Python dict or a JSON
            # string. Handle both. Look for result.text (full stitched
            # transcript) or fall back to stitching segments.
            payload = fetch_result
            if isinstance(payload, str):
                try:
                    payload = _json_st.loads(payload)
                except Exception:
                    return f"save_transcript_artifact: could not parse MCP response as JSON: {payload[:200]}"
            if not isinstance(payload, dict):
                return f"save_transcript_artifact: unexpected MCP response type {type(payload).__name__}"
            inner = payload.get("result") if isinstance(payload.get("result"), dict) else payload
            transcript_text = (inner or {}).get("text") or ""
            if not transcript_text and isinstance((inner or {}).get("segments"), list):
                transcript_text = "\n".join(
                    (seg.get("text") or "").strip() for seg in inner["segments"]
                )
            if not transcript_text.strip():
                return f"save_transcript_artifact: MCP returned no transcript text for {video_id!r}"

            # 2. Stitch frontmatter + transcript.
            body = f"---\n{fm}\n---\n\n{transcript_text.strip()}\n"

            # 3. Normalize path the same way save_to_artifact does.
            proj = _ps_st(effective_project)
            norm = raw_path.lstrip("/").strip()
            if norm and norm != proj and not norm.startswith(f"{proj}/"):
                norm = f"{proj}/{norm}"

            # 4. Save with UNIQUE-path collision retry, mirroring
            #    save_to_artifact behavior.
            def _candidate(base: str, n: int) -> str:
                if n == 0:
                    return base
                p = Path(base)
                if p.suffix:
                    return str(p.with_name(f"{p.stem}-{n}{p.suffix}"))
                return f"{base}-{n}"

            store = get_store()
            final_path = norm
            a = None
            for n in range(50):
                cand = _candidate(norm, n)
                try:
                    a = store.create(
                        project_id=effective_project,
                        path=cand,
                        content=body,
                        content_type="text/markdown",
                        title=title or f"Transcript: {video_id}",
                        tags=["transcript", "youtube"],
                        source={"kind": "agent_transcript_save", "video_id": video_id, "session_id": session_id or ""},
                        edited_by=session_id or "agent",
                    )
                    final_path = cand
                    break
                except _sqlite3st.IntegrityError as e:
                    if "UNIQUE" not in str(e) or "path" not in str(e):
                        raise
                    continue
            if a is None:
                return (
                    f"save_transcript_artifact: path {norm!r} and 50 "
                    f"numbered variants are all taken."
                )

            _emb_st.schedule_embed(a["id"], effective_project)
            try:
                if memory_manager:
                    await memory_manager.index_artifact(a["id"])
            except Exception as _ie:
                logger.debug("graph index for transcript %s failed: %s", a["id"], _ie)

            chars = len(transcript_text)
            suffix_note = ""
            if final_path != norm:
                suffix_note = f" (path {norm!r} was taken; auto-suffixed)"
            return (
                f"Saved transcript artifact {a['path']} "
                f"(id={a['id']}, {chars} chars of transcript){suffix_note}"
            )

        elif tool_name == "create_skill":
            from skills import editor as _skill_ed
            from skills.registry import get_skill_registry
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

            # Refresh the registry so it picks up the new skill.
            try:
                from skills.registry import reload_skill_registry; reload_skill_registry()
            except Exception:
                pass

            return (
                f"Skill {name!r} created and registered.\n"
                f"  triggers: {triggers or '(none — auto-suggest disabled)'}\n"
                f"  tags: {tags or '(none)'}\n\n"
                f"Invoke it later with `/{name}` in any chat. Edit it "
                f"in the Skills tab if you want to refine the "
                f"instructions or add triggers."
            )

        elif tool_name == "create_task":
            from tasks.scheduler import schedule_agent_task
            plan_text = (tool_args.get("plan") or "").strip()
            skip_review = bool(tool_args.get("skip_review", False))

            # SERVER-SIDE GUARDRAIL: reject create_task without a plan, and
            # reject skip_review=true unless the user explicitly approved
            # the plan in the current conversation. The agent's system
            # prompt should already enforce this, but the model
            # sometimes ignores it — refusal here is the backstop.
            if not plan_text:
                return (
                    "create_task rejected: a plan is required. "
                    "Reply to the user with a numbered markdown plan "
                    "naming the exact tools you intend to use per step, "
                    "ask them to approve or edit, and only call "
                    "create_task again AFTER they explicitly approve in "
                    "this chat. Do not infer approval from prior "
                    "context, memory recall, or similar past requests."
                )

            plan_status = "approved" if skip_review else "proposed"
            task_id = await schedule_agent_task(
                name=tool_args.get("name", "task"),
                description=tool_args.get("description", ""),
                schedule=tool_args.get("schedule", "now"),
                project_id=effective_project,
                plan=plan_text,
                plan_status=plan_status,
                parent_session_id=session_id,
            )
            if skip_review:
                return (
                    f"Task scheduled (review skipped — assumes the user "
                    f"already approved the plan in this chat).\n"
                    f"  schedule_id: {task_id}  (SCHEDULE id, not a "
                    f"job run id)\n"
                    f"  name: {tool_args.get('name')}\n"
                    f"  schedule: {tool_args.get('schedule')}\n\n"
                    f"To check whether the schedule actually fired, "
                    f"call list_recent_jobs() or "
                    f"get_job_status(job_id={task_id!r}) — that "
                    f"resolves the schedule to its most recent job "
                    f"run.\n\n"
                    f"If the user did NOT explicitly approve, immediately "
                    f"acknowledge that and ask them to confirm or cancel."
                )
            return (
                f"Task PROPOSED — paused, awaiting your approval.\n"
                f"  schedule_id: {task_id}  (this is the SCHEDULE id, "
                f"not a job run id)\n"
                f"  name: {tool_args.get('name')}\n"
                f"  schedule: {tool_args.get('schedule')}\n\n"
                f"NOTE: Each time the schedule fires it produces a "
                f"separate JOB with its own UUID. To check whether it "
                f"actually ran, call list_recent_jobs() or "
                f"get_job_status(job_id={task_id!r}) — the latter "
                f"will resolve the schedule to its most recent run.\n\n"
                f"Tell the user the plan is queued for review and ask "
                f"them to read the chat or open the Tasks tab to approve "
                f"or edit. The schedule will not fire until they approve."
            )

        elif tool_name == "send_telegram":
            try:
                from telegram_bot.bot import send_message_to_all
                await send_message_to_all(tool_args["message"])
                return "Telegram message sent."
            except Exception as e:
                return f"Telegram send failed: {e}"

        elif tool_name == "index_workspace":
            path_arg = tool_args.get("path", "")
            force = tool_args.get("force", False)
            from memory.manager import create_memory_manager
            mgr = create_memory_manager(project_id=effective_project)
            base = _get_workspace_base(project_id)
            target = (base / path_arg).resolve() if path_arg else base
            if not str(target).startswith(str(base)):
                return "Access denied: path outside workspace"
            if target.is_file():
                result = await mgr.index_workspace_file(str(target), force=force)
            elif target.is_dir():
                result = await mgr.index_workspace_directory(str(target), force=force)
            else:
                # Path not on disk — check whether the user actually meant
                # an artifact folder. If artifacts exist under that prefix,
                # auto-pivot to index_artifact instead of failing.
                from artifacts.store import get_store as _get_art_store, project_slug as _ps_x
                art_store = _get_art_store()
                proj_slug = _ps_x(effective_project)
                norm_pref = (path_arg or "").lstrip("/").strip().rstrip("/")
                if norm_pref and norm_pref != proj_slug and not norm_pref.startswith(f"{proj_slug}/"):
                    norm_pref = f"{proj_slug}/{norm_pref}"
                if norm_pref:
                    norm_pref = norm_pref + "/"
                    matches = art_store.list(
                        project_id=effective_project,
                        path_prefix=norm_pref,
                        limit=500,
                    )
                    if matches:
                        # Auto-route to artifact indexing
                        from artifacts.store import is_text_type as _is_text_x
                        indexed = skipped = total_chunks = total_entities = 0
                        for it in matches:
                            if not _is_text_x(it["content_type"]):
                                skipped += 1
                                continue
                            r = await mgr.index_artifact(it["id"], force=force)
                            if r.get("skipped"):
                                skipped += 1
                            else:
                                indexed += 1
                                total_chunks += r.get("chunks_stored", 0)
                                total_entities += r.get("entities_extracted", 0)
                        return (
                            f"Path {path_arg!r} isn't on disk, but matched "
                            f"{len(matches)} artifact(s) under {norm_pref!r} — "
                            f"auto-routed to artifact indexing.\n"
                            f"Indexed {indexed} ({skipped} skipped): "
                            f"{total_chunks} chunks stored, "
                            f"{total_entities} entities extracted.\n"
                            f"Tip: prefer `index_artifact` for artifact "
                            f"folders; `index_workspace` is for files on disk."
                        )
                return (
                    f"Path not found: {path_arg!r}. If you meant an "
                    f"artifact folder, use `index_artifact(""path_prefix={path_arg!r})` instead — workspace is for files on "
                    f"disk, artifacts are the project's persistent store."
                )
            if result.get("skipped"):
                return f"File {path_arg} skipped: {result.get('reason', 'unchanged')}"
            chunks = result.get("chunks_stored", result.get("total_chunks", 0))
            entities = result.get("entities_extracted", result.get("total_entities", 0))
            files = result.get("files_processed", 1)
            return f"Indexed {files} file(s): {chunks} chunks stored, {entities} entities extracted to graph."

        elif tool_name == "index_artifact":
            from artifacts.store import get_store, is_text_type, project_slug as _ps_idx
            from memory.manager import create_memory_manager
            store = get_store()
            mgr = create_memory_manager(project_id=effective_project)
            force = bool(tool_args.get("force", False))
            aid = tool_args.get("id")
            prefix = tool_args.get("path_prefix")

            if aid:
                result = await mgr.index_artifact(aid, force=force)
                if result.get("skipped"):
                    return f"Artifact {aid} skipped: {result.get('reason', 'unchanged')}"
                return (
                    f"Indexed artifact {aid}: "
                    f"{result.get('chunks_stored', 0)} chunks stored, "
                    f"{result.get('entities_extracted', 0)} entities extracted."
                )

            if prefix is not None:
                # Apply same normalization as save/list so callers can
                # pass a bare folder name like 'NBJ/'.
                proj = _ps_idx(effective_project)
                norm = (prefix or "").lstrip("/").strip()
                if norm and norm != proj and not norm.startswith(f"{proj}/"):
                    norm = f"{proj}/{norm}"
                items = store.list(
                    project_id=effective_project,
                    path_prefix=norm or None,
                    limit=500,
                )
                if not items:
                    return f"(no artifacts match prefix {norm!r})"
                indexed = skipped = total_chunks = total_entities = 0
                for it in items:
                    if not is_text_type(it["content_type"]):
                        skipped += 1
                        continue
                    r = await mgr.index_artifact(it["id"], force=force)
                    if r.get("skipped"):
                        skipped += 1
                    else:
                        indexed += 1
                        total_chunks += r.get("chunks_stored", 0)
                        total_entities += r.get("entities_extracted", 0)
                return (
                    f"Indexed {indexed} artifact(s) under {norm!r} "
                    f"({skipped} skipped): {total_chunks} chunks stored, "
                    f"{total_entities} entities extracted."
                )

            return "index_artifact requires either 'id' or 'path_prefix'."

        elif tool_name == "get_job_status":
            from jobs.store import get_store
            jid = (tool_args.get("job_id") or "").strip()
            store = get_store()
            j = store.get_or_none(jid)
            via_schedule = False
            if not j and jid:
                # Fall back: maybe the caller passed a schedule_id
                # (the 8-char hex id returned by create_task) instead
                # of a full job UUID. Look up the most recent run.
                j = store.find_latest_for_schedule(jid)
                via_schedule = bool(j)
            if not j:
                # Also try matching a job UUID by 8-char prefix
                # (e.g. agent quotes a truncated id).
                if jid and len(jid) >= 4:
                    matches = store.list(limit=200)
                    cand = [r for r in matches if r["id"].startswith(jid)]
                    if len(cand) == 1:
                        j = cand[0]
            if not j:
                return (
                    f"No job or schedule found for id {jid!r}. "
                    f"If this looks like a schedule_id (8-char hex "
                    f"from create_task), the schedule may not have "
                    f"fired yet — call list_recent_jobs to see what "
                    f"actually ran, or check the Tasks tab to "
                    f"confirm the schedule is approved and active."
                )
            lines = [
                f"job_id: {j['id']}"
                + (f"  (resolved from schedule_id {jid!r})" if via_schedule else ""),
                f"schedule_id: {j.get('schedule_id') or '(none)'}",
                f"type: {j['job_type']}  status: {j['status'].upper()}",
                f"title: {j.get('title') or ''}",
                f"started: {j.get('started_at') or '(not yet)'}",
                f"completed: {j.get('completed_at') or '(still running)'}",
            ]
            if j.get("progress"):
                lines.append(f"progress: {j['progress']}")
            if j.get("error"):
                lines.append(f"error: {j['error']}")
            if j.get("pr_url"):
                lines.append(f"pr_url: {j['pr_url']}")
            if j.get("artifact_id"):
                lines.append(f"artifact_id: {j['artifact_id']}")
            if j.get("session_id"):
                lines.append(f"session_id: {j['session_id']}")
            if j.get("result"):
                summary = (j["result"] or {}).get("summary") if isinstance(j["result"], dict) else None
                if summary:
                    lines.append(f"summary: {summary[:500]}")
            return "\n".join(lines)

        elif tool_name == "list_recent_jobs":
            from jobs.store import get_store
            include_sys = bool(tool_args.get("include_system", False))
            statuses = [tool_args["status"]] if tool_args.get("status") else None
            from datetime import timedelta
            runs = get_store().list(
                project_id=effective_project,
                statuses=statuses, include_system=include_sys,
                started_within=timedelta(hours=72),
                limit=int(tool_args.get("limit") or 10),
            )
            if not runs:
                return "(no recent jobs in this project)"
            out = []
            for r in runs:
                line = f"#{r['id'][:8]}  [{r['job_type']}]  {r['status'].upper()}  {r.get('title') or ''}"
                if r.get("progress") and r["status"] == "running":
                    line += f"  ({r['progress'][:100]})"
                if r.get("error"):
                    line += f"  ERR: {r['error'][:120]}"
                if r.get("pr_url"):
                    line += f"  {r['pr_url']}"
                out.append(line)
            return "\n".join(out)

        elif tool_name == "consolidate_memory":
            if not memory_manager:
                return "No memory manager available."
            result = await memory_manager.consolidate_session()
            return result

        elif tool_name == "start_coding_task":
            from jobs.store import get_store
            title = tool_args.get("title") or "Coding task"
            description = tool_args.get("task_description") or ""
            payload = {
                "task_description": description,
                "coding_context": tool_args.get("coding_context") or "",
                "branch_name": tool_args.get("branch_name"),
                "base_branch": tool_args.get("base_branch"),
            }
            j = get_store().create(
                job_type="coding_task",
                project_id=effective_project,
                title=title,
                description=description[:200],
                payload=payload,
            )
            return (
                f"Coding task queued.\n"
                f"  job_id: {j['id']}\n"
                f"  title: {title}\n"
                f"You can call get_job_status(job_id={j['id'][:8]!r}) "
                f"or list_recent_jobs() to check progress. The user can "
                f"watch it in the chat Tasks tab."
            )

        elif tool_name == "code_execute":
            from sandbox import get_sandbox
            from sandbox.backend import SandboxConfig
            language = (tool_args.get("language") or "python").lower()
            code = tool_args.get("code") or ""
            if not code.strip():
                return "code_execute: empty code argument"
            timeout = int(tool_args.get("timeout_seconds") or 30)
            timeout = max(1, min(timeout, 300))
            cfg = SandboxConfig(timeout_seconds=timeout)
            result = await get_sandbox().execute_inline(
                language=language,
                code=code,
                filename=tool_args.get("filename"),
                config=cfg,
            )
            # Render result for the model in a stable, parseable form.
            parts = [f"exit_code: {result.exit_code}",
                     f"duration_ms: {result.duration_ms}"]
            if result.timed_out:
                parts.append("timed_out: true")
            if result.stdout:
                parts.append("stdout:\n" + result.stdout.rstrip())
            if result.stderr:
                parts.append("stderr:\n" + result.stderr.rstrip())
            return "\n\n".join(parts) or "(no output)"

        elif tool_name in ("save_to_artifact", "update_artifact", "read_artifact", "list_artifacts"):
            from artifacts.store import get_store, is_text_type, project_slug as _ps
            from artifacts import embedder as _emb
            store = get_store()

            def _normalize_artifact_path(p: str | None) -> str | None:
                """Make path/path_prefix consistent with how save stores them.

                Saves auto-prepend '<project_slug>/' if missing. Apply the
                same rule to lookups so agents can save 'NBJ/x.md' and
                find it again with either 'NBJ/' or 'default-project/NBJ/'.
                Idempotent: never double-prefixes.
                """
                if p is None:
                    return None
                norm = p.lstrip("/").strip()
                if not norm:
                    return norm
                proj = _ps(effective_project)
                if norm == proj or norm.startswith(f"{proj}/"):
                    return norm
                return f"{proj}/{norm}"

            if tool_name == "save_to_artifact":
                import sqlite3 as _sqlite3
                norm = _normalize_artifact_path(tool_args["path"])
                # Auto-resolve UNIQUE path collisions by suffixing -1, -2, ...
                # before the final extension. We retry up to 50 times before
                # surfacing the error.
                def _candidate(base: str, n: int) -> str:
                    if n == 0:
                        return base
                    p = Path(base)
                    # path may have no suffix or multiple dots; use the last
                    # component's last suffix only.
                    if p.suffix:
                        return str(p.with_name(f"{p.stem}-{n}{p.suffix}"))
                    return f"{base}-{n}"

                final_path = norm
                a = None
                for n in range(50):
                    cand = _candidate(norm, n)
                    try:
                        a = store.create(
                            project_id=effective_project,
                            path=cand,
                            content=tool_args["content"],
                            content_type=tool_args.get("content_type") or "text/markdown",
                            title=tool_args.get("title"),
                            tags=tool_args.get("tags"),
                            source={"kind": "agent", "session_id": session_id or ""},
                            edited_by=session_id or "agent",
                        )
                        final_path = cand
                        break
                    except _sqlite3.IntegrityError as e:
                        if "UNIQUE" not in str(e) or "path" not in str(e):
                            raise
                        continue
                if a is None:
                    return (
                        f"save_to_artifact failed: path {norm!r} and 50 "
                        f"numbered variants are all taken. Pick a different "
                        f"path or delete the existing artifact first."
                    )
                _emb.schedule_embed(a["id"], effective_project)
                # Auto-index into graph memory so cross-artifact concept
                # queries work without an explicit indexer call.
                try:
                    if memory_manager and is_text_type(a["content_type"]):
                        await memory_manager.index_artifact(a["id"])
                except Exception as _ie:
                    logger.debug("graph index for %s failed: %s", a["id"], _ie)
                suffix_note = ""
                if final_path != norm:
                    suffix_note = f" (path {norm!r} was taken; auto-suffixed)"
                return f"Saved artifact {a['path']} (id={a['id']}, v{1}){suffix_note}"
            if tool_name == "update_artifact":
                aid = tool_args["id"]
                try:
                    a = store.update(
                        aid,
                        content=tool_args["content"],
                        edit_summary=tool_args.get("edit_summary") or "Agent update",
                        edited_by=session_id or "agent",
                    )
                except KeyError:
                    return f"Artifact {aid} not found."
                _emb.schedule_embed(a["id"], effective_project)
                try:
                    if memory_manager and is_text_type(a["content_type"]):
                        await memory_manager.index_artifact(a["id"])
                except Exception as _ie:
                    logger.debug("graph index for %s failed: %s", a["id"], _ie)
                versions = store.list_versions(aid)
                return f"Updated artifact {a['path']} (now v{len(versions)})"
            if tool_name == "read_artifact":
                aid = tool_args.get("id")
                path = tool_args.get("path")
                a = None
                if aid:
                    a = store.get(aid)
                elif path:
                    norm_path = _normalize_artifact_path(path)
                    a = store.get_by_path(effective_project, norm_path)
                    # Tolerate agent passing the bare path even when the
                    # caller already saved it under a non-prefixed form.
                    if not a and norm_path != path:
                        a = store.get_by_path(effective_project, path)
                if not a:
                    return f"Artifact not found: id={aid} path={path}"
                if is_text_type(a["content_type"]):
                    return f"--- {a['path']} (v{store.list_versions(a['id'])[0]['version_number']}) ---\n{a.get('content') or ''}"
                return f"Artifact {a['path']} is binary ({a['content_type']}); fetch via /api/artifacts/{a['id']}/raw"
            if tool_name == "list_artifacts":
                norm_prefix = _normalize_artifact_path(tool_args.get("path_prefix"))
                items = store.list(
                    project_id=effective_project,
                    tag=tool_args.get("tag"),
                    content_type=tool_args.get("content_type"),
                    path_prefix=norm_prefix,
                    search=tool_args.get("search"),
                    limit=int(tool_args.get("limit") or 20),
                )
                if not items:
                    hint = ""
                    if norm_prefix and norm_prefix != (tool_args.get("path_prefix") or ""):
                        hint = (
                            f" (searched normalized prefix {norm_prefix!r}; "
                            f"caller passed {tool_args.get('path_prefix')!r})"
                        )
                    return f"(no artifacts match){hint}"
                lines = [
                    f"- id={i['id']} path={i['path']} type={i['content_type']} tags={i.get('tags')}"
                    for i in items
                ]
                return "\n".join(lines)
            return f"Unknown artifact tool: {tool_name}"

        elif tool_name.startswith("github_"):
            from api.connections import (
                get_connection, get_token, mark_used, mark_error,
                get_project_repo_for_tools, get_project_binding,
            )
            from integrations.github import GitHubClient, GitHubAuthError, GitHubError, GitHubForbidden, GitHubNotFound
            if tool_name == "github_list_connections":
                from api.connections import _connect as _gh_connect
                with _gh_connect() as cn:
                    rows = cn.execute(
                        "SELECT id, full_name, default_branch, account_login, status "
                        "FROM github_connections ORDER BY created_at DESC"
                    ).fetchall()
                items = [
                    f"- id={r['id']} repo={r['full_name']} branch={r['default_branch']} status={r['status']}"
                    for r in rows
                ]
                binding = get_project_binding(effective_project)
                bound_line = (
                    f"\nProject {effective_project} is bound to repo {binding['owner']}/{binding['repo']} "
                    f"(connection {binding['connection_id']})"
                ) if binding else f"\nProject {effective_project} has no repo bound."
                return "GitHub connections:\n" + ("\n".join(items) if items else "(none)") + bound_line

            # Resolve which repo this project should act on
            connection_id = tool_args.get("connection_id")
            if connection_id:
                # Explicit connection — caller picked owner/repo themselves
                conn_row = get_connection(connection_id)
                owner = (tool_args.get("owner") or (conn_row or {}).get("owner") or "")
                repo = (tool_args.get("repo") or (conn_row or {}).get("repo") or "")
            else:
                spec = get_project_repo_for_tools(effective_project)
                if not spec:
                    return (
                        f"No repo bound to project {effective_project}. Bind one "
                        f"in Settings → Connections (add a PAT) then in the "
                        f"Projects page pick a repo for this project."
                    )
                conn_row = get_connection(spec["connection_id"])
                owner = spec["owner"]; repo = spec["repo"]
            if not conn_row:
                return (
                    "Connection not found. Re-add the GitHub PAT in "
                    "Settings → Connections."
                )
            token = get_token(conn_row["id"])
            if not token:
                return f"Connection {conn_row['id']} has no stored token. Re-add it in Settings → Connections."
            client = GitHubClient(token)
            try:
                if tool_name == "github_read_file":
                    res = await client.read_file(owner, repo, tool_args["path"], ref=tool_args.get("ref"))
                    mark_used(conn_row["id"])
                    return f"--- {owner}/{repo}@{tool_args.get('ref') or conn_row['default_branch']}:{tool_args['path']} ---\n{res['content']}"
                if tool_name == "github_list_directory":
                    items = await client.list_directory(owner, repo, tool_args.get("path", ""), ref=tool_args.get("ref"))
                    mark_used(conn_row["id"])
                    lines = [f"{i.get('type','?')[0]} {i.get('name')} ({i.get('size','?')}b)" for i in items]
                    return f"{owner}/{repo}:{tool_args.get('path','')}\n" + "\n".join(lines)
                if tool_name == "github_create_branch":
                    res = await client.create_branch(
                        owner, repo,
                        new_branch=tool_args["new_branch"],
                        base_branch=tool_args.get("base_branch"),
                    )
                    mark_used(conn_row["id"])
                    return f"Branch created: {tool_args['new_branch']} (sha={res.get('object',{}).get('sha','?')[:8]})"
                if tool_name == "github_write_files":
                    res = await client.write_files(
                        owner, repo,
                        branch=tool_args["branch"],
                        files=tool_args["files"],
                        message=tool_args["message"],
                    )
                    mark_used(conn_row["id"])
                    return (
                        f"Committed {len(res['files'])} file(s) to {res['branch']} "
                        f"(commit={res['commit_sha'][:8]})"
                    )
                if tool_name == "github_create_pr":
                    base = tool_args.get("base") or conn_row["default_branch"]
                    pr = await client.create_pr(
                        owner, repo,
                        title=tool_args["title"],
                        head=tool_args["head"],
                        base=base,
                        body=tool_args.get("body", ""),
                        draft=tool_args.get("draft", False),
                    )
                    mark_used(conn_row["id"])
                    return f"PR #{pr.get('number')} created: {pr.get('html_url')}"
                if tool_name == "github_merge_pr":
                    res = await client.merge_pr(
                        owner, repo,
                        pr_number=int(tool_args["pr_number"]),
                        merge_method=tool_args.get("merge_method", "squash"),
                    )
                    mark_used(conn_row["id"])
                    return f"PR #{tool_args['pr_number']} merged ({'success' if res.get('merged') else 'no-op'})"
                return f"Unknown github tool: {tool_name}"
            except GitHubAuthError as e:
                mark_error(conn_row["id"], str(e))
                return f"GitHub auth failed: {e}. Re-add the PAT in Settings → Sources."
            except GitHubNotFound as e:
                return f"GitHub: not found — {e}"
            except GitHubForbidden as e:
                return f"GitHub: forbidden — {e}"
            except GitHubError as e:
                return f"GitHub error: {e}"

        else:
            return f"Unknown tool: {tool_name}"

    except Exception as e:
        logger.error(f"Tool '{tool_name}' error: {e}", exc_info=True)
        return f"Error executing {tool_name}: {str(e)}"


def _get_workspace_base(project_id: str | None = None) -> Path:
    if project_id and project_id != "default":
        path = settings.projects_dir / project_id / "workspace"
    else:
        path = settings.workspace_dir
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _safe_workspace_path(rel_path: str, project_id: str | None = None) -> Path:
    """Resolve path safely within workspace to prevent path traversal."""
    base = _get_workspace_base(project_id)
    target = (base / rel_path).resolve()
    if not str(target).startswith(str(base)):
        raise ValueError(f"Path traversal denied: {rel_path}")
    return target


async def _web_search(query: str) -> str:
    """Run a web search via the provider chain (Brave → SearXNG → DDG by default).

    Falls through to the next provider on exception, empty results, or
    quota/rate-limit exhaustion. Per-provider quotas, rate limits, and
    chain ordering are configured via the SearchProviderManager.
    """
    try:
        from agent.search_providers import get_search_manager
        return await get_search_manager().search(query)
    except Exception as e:
        logger.exception("search provider chain failed; falling back to DDG-only")
        return await _ddg_search(query)


async def _configured_search(query: str, base_url: str, api_key: str) -> str:
    """Call a configured JSON search endpoint and parse common response shapes."""
    headers: dict[str, str] = {
        "User-Agent": "Mozilla/5.0 (compatible; AgentHarness/1.0)",
        "Accept": "application/json",
    }
    if api_key:
        # Brave uses X-Subscription-Token; everything else gets Bearer
        if "brave.com" in base_url:
            headers["X-Subscription-Token"] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"

    # SearXNG expects the path to be /search
    if "searx" in base_url.lower() and not base_url.endswith("/search"):
        endpoint = base_url + "/search"
    else:
        endpoint = base_url

    params: dict[str, str] = {"q": query, "format": "json"}

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(endpoint, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"Configured search failed ({base_url}): {e} — falling back to DuckDuckGo")
        return await _ddg_search(query)

    # ── Normalise the result shape ────────────────────────────────────────────
    # SearXNG: {"results": [{"title","url","content","engine"}, ...]}
    # Brave:   {"web": {"results": [{"title","url","description"}, ...]}}
    # Generic: [{"title","url","snippet"/"description"/"content"}, ...]

    raw: list[dict] = []
    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict):
        if "results" in data:
            raw = data["results"]
        elif "web" in data and isinstance(data["web"], dict):
            raw = data["web"].get("results", [])

    if not raw:
        return "No results found."

    lines = []
    for i, item in enumerate(raw[:6]):
        title   = item.get("title", "").strip()
        url_str = item.get("url", item.get("href", "")).strip()
        snippet = (
            item.get("content")
            or item.get("description")
            or item.get("snippet")
            or ""
        ).strip()
        if title and url_str:
            lines.append(f"{i+1}. {title}\n   {url_str}\n   {snippet}")

    return "\n\n".join(lines) if lines else "No results found."


async def _ddg_search(query: str) -> str:
    """Fallback: DuckDuckGo via HTML scrape (no API key needed)."""
    url = "https://html.duckduckgo.com/html/"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; AgentHarness/1.0)"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, data={"q": query, "b": ""}, headers=headers)
            resp.raise_for_status()
            import re
            results = re.findall(r'<a class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', resp.text, re.S)
            snippets = re.findall(r'<a class="result__snippet"[^>]*>(.*?)</a>', resp.text, re.S)
            clean_tag = re.compile(r'<[^>]+>')
            lines = []
            for i, (link, title) in enumerate(results[:5]):
                clean_title = clean_tag.sub('', title).strip()
                snippet = clean_tag.sub('', snippets[i]).strip() if i < len(snippets) else ""
                lines.append(f"{i+1}. {clean_title}\n   {link}\n   {snippet}")
            return "\n\n".join(lines) if lines else "No results found."
    except Exception as e:
        return f"Search failed: {e}"

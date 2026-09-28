"""GitHub API tools (github_*), via the project's bound repository."""
from __future__ import annotations

from typing import Any
from agent.tools.registry import ToolContext, tool


SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "github_list_connections",
            "description": "Diagnostic only — list configured GitHub PATs and show which one is bound to this project. You do NOT need to call this before other github_* tools; they automatically use the project's bound repo.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_read_file",
            "description": "Read a file from the GitHub repo bound to this project. Use to understand existing code before making changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path within the repo, e.g. 'src/main.py'"},
                    "ref": {"type": "string", "description": "Optional branch or commit sha. Defaults to the repo's default branch."}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_list_directory",
            "description": "List files and folders at a path in the GitHub repo bound to this project.",
            "parameters": {
                "type": "object",
                "properties": {
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
            "name": "github_list_branches",
            "description": "List all branches in the GitHub repo bound to this project. Returns names, head shas, and protected flags. Use to discover iteration_loop branches, find PR candidates, or audit branch sprawl.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name_prefix": {"type": "string", "description": "Optional case-sensitive prefix filter, e.g. 'iteration/' to find iteration_loop branches."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_list_pulls",
            "description": "List pull requests in the GitHub repo bound to this project. Filter by state (open/closed/all) and optionally by head or base branch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {"type": "string", "enum": ["open", "closed", "all"], "default": "open"},
                    "head": {"type": "string", "description": "Filter by head branch, e.g. 'owner:feature-x' or 'feature-x'."},
                    "base": {"type": "string", "description": "Filter by base branch, e.g. 'main'."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_create_branch",
            "description": "Create a new branch off the default branch (or a named base branch) in the GitHub repo bound to this project.",
            "parameters": {
                "type": "object",
                "properties": {
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
            "name": "github_delete_branch",
            "description": "Delete a branch from the GitHub repo bound to this project. Use to clean up merged or obsolete branches. Will refuse to delete the repo's default branch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "branch": {"type": "string", "description": "Branch name to delete (no 'refs/heads/' prefix)."}
                },
                "required": ["branch"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_write_files",
            "description": "Atomically commit one or more files to a branch in the GitHub repo bound to this project. Use after github_create_branch to land changes the user will review.",
            "parameters": {
                "type": "object",
                "properties": {
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
            "description": "Open a pull request in the GitHub repo bound to this project.",
            "parameters": {
                "type": "object",
                "properties": {
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
            "description": "Merge a previously-opened pull request in the GitHub repo bound to this project. Use only when the user explicitly approves merging. Defaults to squash to keep main linear.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pr_number": {"type": "integer"},
                    "merge_method": {"type": "string", "enum": ["merge", "squash", "rebase"], "default": "squash"}
                },
                "required": ["pr_number"]
            }
        }
    },
]


@tool(prefix='github_')
async def _tool_github_tools(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    effective_project = ctx.effective_project
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

    # Resolve which repo this project should act on. The project's
    # repo binding is authoritative for owner/repo; the connection
    # only supplies the auth token. Agent-passed connection_id is
    # ignored — single repo per project.
    spec = get_project_repo_for_tools(effective_project)
    if not spec:
        return (
            f"No repo bound to project {effective_project}. Bind one "
            f"in Settings → Connections (add a PAT) then in the "
            f"Projects page pick a repo for this project."
        )
    conn_row = get_connection(spec["connection_id"])
    owner = spec["owner"]
    repo = spec["repo"]
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
        if tool_name == "github_list_branches":
            branches = await client.list_branches(owner, repo)
            mark_used(conn_row["id"])
            prefix = tool_args.get("name_prefix") or ""
            if prefix:
                branches = [b for b in branches if (b.get("name") or "").startswith(prefix)]
            lines = [
                f"{b['name']} (sha={(b.get('commit_sha') or '')[:8]}"
                + (", protected" if b.get("protected") else "")
                + ")"
                for b in branches
            ]
            header = f"{owner}/{repo} — {len(branches)} branch(es)"
            if prefix:
                header += f" matching '{prefix}'"
            return header + "\n" + ("\n".join(lines) if lines else "(none)")
        if tool_name == "github_list_pulls":
            pulls = await client.list_pulls(
                owner, repo,
                state=tool_args.get("state", "open"),
                head=tool_args.get("head"),
                base=tool_args.get("base"),
            )
            mark_used(conn_row["id"])
            lines = [
                f"#{p['number']} [{p['state']}] {p['head']} → {p['base']} — {p['title']}"
                + (" (draft)" if p.get("draft") else "")
                + f"\n  {p['html_url']}"
                for p in pulls
            ]
            header = f"{owner}/{repo} — {len(pulls)} pull(s) (state={tool_args.get('state','open')})"
            return header + "\n" + ("\n".join(lines) if lines else "(none)")
        if tool_name == "github_create_branch":
            res = await client.create_branch(
                owner, repo,
                new_branch=tool_args["new_branch"],
                base_branch=tool_args.get("base_branch"),
            )
            mark_used(conn_row["id"])
            return f"Branch created: {tool_args['new_branch']} (sha={res.get('object',{}).get('sha','?')[:8]})"
        if tool_name == "github_delete_branch":
            branch_name = tool_args["branch"]
            if branch_name == conn_row.get("default_branch") or branch_name == spec.get("default_branch"):
                return f"Refusing to delete the default branch '{branch_name}'."
            await client.delete_branch(owner, repo, branch_name)
            mark_used(conn_row["id"])
            return f"Branch deleted: {branch_name}"
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


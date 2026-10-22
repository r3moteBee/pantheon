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
            f"on the Connections page (GitHub tab: add a token), then "
            f"pick a repo in the chat's Repository tab."
        )
    conn_row = get_connection(spec["connection_id"])
    owner = spec["owner"]
    repo = spec["repo"]
    if not conn_row:
        return (
            "Connection not found. Re-add the GitHub token on the "
            "Connections page (GitHub tab)."
        )
    token = get_token(conn_row["id"])
    if not token:
        return f"Connection {conn_row['id']} has no stored token. Re-add it on the Connections page (GitHub tab)."
    action = tool_name.removeprefix("github_")
    if action in WRITE_ACTIONS and _from_messaging_app(ctx):
        return (f"{action} isn't available from {ctx.channel}: changes to the GitHub repo are made "
                "from the web chat or a background task the user approved. Tell the user what you "
                "would change so they can do it from the web chat.")
    if action in INTERACTIVE_ONLY_ACTIONS and not ctx.interactive:
        return (f"{action} only runs in a chat the user is driving; background tasks can't "
                f"{'merge pull requests' if action == 'merge_pr' else 'delete branches'}. "
                "Tell the user what you would like done so they can do it from the chat or on GitHub.")
    client = GitHubClient(token)
    try:
        if tool_name == "github_push_artifacts":
            return await _push_artifacts(ctx, conn_row, spec, tool_args)
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
        return f"GitHub auth failed: {e}. Re-add the token on the Connections page (GitHub tab)."
    except GitHubNotFound as e:
        return f"GitHub: not found — {e}"
    except GitHubForbidden as e:
        return f"GitHub: forbidden — {e}"
    except GitHubError as e:
        return f"GitHub error: {e}"



async def _push_artifacts(ctx: ToolContext, conn_row: dict, spec: dict, tool_args: dict[str, Any]) -> str:
    """github(action="push_artifacts"): the agent's side of api/github_push.
    Only in chats a person is driving, only the project's bound repo, always
    a new branch + PR, never mirroring, never files that look like secrets."""
    import time
    from api.github_push import collect, resolve_repo, run_push
    from artifacts.store import project_slug
    from integrations.github_push import PushError, sensitive_files
    if not ctx.interactive:
        return ("push_artifacts only runs in a chat the user is driving; background tasks can't publish "
                "code. Tell the user the folder is ready to push (Artifacts page → Push to GitHub).")
    slug = project_slug(ctx.effective_project)
    folder = (tool_args.get("folder") or "").strip().strip("/")
    if folder.startswith(slug + "/"):
        folder = folder[len(slug) + 1:]
    if not folder or folder == slug:
        return "push_artifacts needs `folder`, the artifact folder to push (e.g. 'SpiderKit/')."
    files, meta, _ = collect(ctx.effective_project, path_prefix=f"{slug}/{folder}/")
    if not files:
        return f"push_artifacts: no artifacts under {slug}/{folder}/ (list_artifacts shows the folders)."
    flagged = sensitive_files(files)
    if flagged:
        return ("push_artifacts refused: these files look like they hold credentials: "
                + ", ".join(flagged[:10]) + ". Remove them from the folder, or let the user push it from "
                "the Artifacts page, which asks before pushing such files.")
    name = folder.rsplit("/", 1)[-1]
    branch = (tool_args.get("branch") or "").strip() or f"pantheon/{name.lower()}-{time.strftime('%Y%m%d-%H%M')}"
    message = (tool_args.get("message") or "").strip() or f"Add {name} from Pantheon"
    try:
        conn, owner, repo = resolve_repo(ctx.effective_project, conn_row["id"], spec["owner"], spec["repo"])
        res = await run_push(ctx.effective_project, files, meta, conn=conn, owner=owner, repo=repo,
                             branch=branch, message=message, dest_dir=tool_args.get("path") or "",
                             base_branch=tool_args.get("base"), mirror=False, open_pr=True,
                             pr_title=tool_args.get("title"), pr_body=tool_args.get("body") or "",
                             allow_default_branch=False)
    except PushError as e:
        return f"push_artifacts: {e}"
    return res["summary"]


# ── Consolidated entry point ─────────────────────────────────────────────────
# One schema for the GitHub API instead of ten. Each action runs the
# github_<action> handler above; those names stay callable (hidden) so
# skills written against them keep working.

# Irreversible repo changes need a person in the loop (push_artifacts checks
# the same in _push_artifacts). create_branch / write_files / create_pr stay
# open to background runs: iteration_loop and coding_task jobs (plans the
# user approved) commit through them, and they only add a branch or a PR.
INTERACTIVE_ONLY_ACTIONS = frozenset({"merge_pr", "delete_branch"})
# Repo writes a messaging-app turn may not make: the bot relays messages from
# chat-app users (allowlisted, but not the web UI's signed-in user), and
# background jobs that commit (coding_task, iteration_loop) have channel None.
WRITE_ACTIONS = frozenset({"create_branch", "delete_branch", "write_files", "push_artifacts", "create_pr", "merge_pr"})


def _from_messaging_app(ctx) -> bool:
    return bool(getattr(ctx, "channel", None)) and ctx.channel != "web"

GITHUB_ACTIONS = ("list_connections", "read_file", "list_directory", "list_branches", "list_pulls",
                  "create_branch", "delete_branch", "write_files", "push_artifacts", "create_pr", "merge_pr")

SCHEMAS.append({
    "type": "function",
    "function": {
        "name": "github",
        "description": (
            "GitHub API on the project's bound repo. Actions: read_file(path, ref?) · list_directory(path?, "
            "ref?) · list_branches(name_prefix?) · list_pulls(state?, head?, base?) · create_branch(new_branch, "
            "base_branch?) · delete_branch(branch) - not the default branch; web chat only · write_files(branch, "
            "message, files=[{path, content}]) - one commit, after create_branch · push_artifacts(folder, "
            "branch?, message?, path?, title?, body?, base?) - commits an artifact folder as stored (no need to "
            "resend files) to a NEW branch and opens a PR; path = folder in the repo · create_pr(title, head, "
            "base?, body?, draft?) · merge_pr(pr_number, merge_method?) - only when the user approved merging; "
            "web chat only · list_connections()."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(GITHUB_ACTIONS)},
                "path": {"type": "string"},
                "folder": {"type": "string", "description": "push_artifacts: artifact folder, e.g. 'SpiderKit/'."},
                "ref": {"type": "string"},
                "name_prefix": {"type": "string"},
                "state": {"type": "string", "enum": ["open", "closed", "all"]},
                "head": {"type": "string"},
                "base": {"type": "string"},
                "new_branch": {"type": "string"},
                "base_branch": {"type": "string"},
                "branch": {"type": "string"},
                "message": {"type": "string"},
                "files": {"type": "array", "items": {"type": "object"}, "description": "[{path, content}]"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "draft": {"type": "boolean"},
                "pr_number": {"type": "integer"},
                "merge_method": {"type": "string", "enum": ["merge", "squash", "rebase"]},
            },
            "required": ["action"],
        },
    },
})


@tool("github")
async def _tool_github(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    action = (tool_args.get("action") or "").strip()
    if action not in GITHUB_ACTIONS:
        return f"Error: github action must be one of {', '.join(GITHUB_ACTIONS)}."
    args = {k: v for k, v in tool_args.items() if k != "action"}
    return await _tool_github_tools(ctx, f"github_{action}", args)

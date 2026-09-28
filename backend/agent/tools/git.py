"""Local git tools (git_*) on the project's repo checkout (HOST_EXEC_TOOLS, gated)."""
from __future__ import annotations

from typing import Any
from agent.tools.registry import ToolContext, tool
from agent.tools import workspace as _ws


SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "git_sync_repo",
            "description": (
                "Clone the project's bound GitHub repo into the local "
                "workspace (or fetch + fast-forward it if already cloned) "
                "and return the checkout path. Run this FIRST before doing "
                "local coding work: all other git_* tools and run_command "
                "automatically operate on this checkout once it exists. "
                "Files inside it are reachable via read_file/write_file "
                "under 'repos/<owner>__<repo>/...'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "branch": {
                        "type": "string",
                        "description": "Optional branch to check out after syncing. Defaults to the repo's default branch. Created locally if it doesn't exist yet."
                    },
                    "fresh": {
                        "type": "boolean",
                        "description": "If true, delete the existing checkout and re-clone from scratch."
                    }
                },
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show the local git working tree status (runs git status --porcelain).",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_create_branch",
            "description": "Create and switch to a new local git branch. Switched to existing if it already exists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "branch_name": {
                        "type": "string",
                        "description": "Name of the branch to create or switch to."
                    }
                },
                "required": ["branch_name"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_merge",
            "description": (
                "Merge a branch into the CURRENT branch of the local repo "
                "checkout. This is the ONLY correct way to merge — never "
                "simulate a merge by copying file contents between branches. "
                "On conflicts it returns the conflicted files with their "
                "conflict hunks; edit only those regions, then git_commit to "
                "conclude the merge. Pass abort=true to abandon an "
                "in-progress merge."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "branch": {
                        "type": "string",
                        "description": "Branch to merge in (local name or remote like 'feature/x' — origin/<branch> is tried automatically)."
                    },
                    "message": {
                        "type": "string",
                        "description": "Optional merge commit message. Defaults to 'Merge <ref> into <current branch>'."
                    },
                    "abort": {
                        "type": "boolean",
                        "description": "Abort the in-progress merge and restore the working tree."
                    }
                },
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": (
                "Stage files and commit them on the CURRENT branch of the "
                "local repo checkout. Refuses to commit files containing "
                "unresolved merge-conflict markers. The result names the "
                "branch the commit landed on — verify it matches your "
                "intent."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The commit message."
                    },
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of relative paths to files to stage. If empty/omitted, stages all workspace changes (git add .)."
                    }
                },
                "required": ["message"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_push_pr",
            "description": "Push the current git branch to the remote GitHub repository and create a Pull Request.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "The Pull Request title."
                    },
                    "body": {
                        "type": "string",
                        "description": "Optional description for the Pull Request."
                    },
                    "base": {
                        "type": "string",
                        "description": "Optional base branch to merge into. Defaults to remote's default branch or 'main'."
                    }
                },
                "required": ["title"],
                "additionalProperties": False
            }
        }
    },
]


@tool(prefix='git_')
async def _tool_git_tools(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    effective_project = ctx.effective_project
    cwd = _ws._resolve_repo_checkout(effective_project) or _ws._get_workspace_base(effective_project)

    if tool_name == "git_sync_repo":
        import shutil
        from api.connections import get_project_repo_for_tools, get_token
        spec = get_project_repo_for_tools(effective_project)
        if not spec:
            return (f"No repo bound to project {effective_project}. Bind one "
                    f"in Settings → Connections (add a PAT) then in the "
                    f"Projects page pick a repo for this project.")
        token = get_token(spec["connection_id"])  # None is fine for public repos
        owner, repo = spec["owner"], spec["repo"]
        branch = tool_args.get("branch") or spec["default_branch"] or "main"
        dest = _ws._repo_checkout_dir(effective_project, owner, repo)
        clean_url = f"https://github.com/{owner}/{repo}.git"
        auth_env = _ws._git_auth_env(token)

        def _redact(s: str) -> str:
            return s.replace(token, "********") if token else s

        if tool_args.get("fresh") and dest.exists():
            shutil.rmtree(dest, ignore_errors=True)

        if (dest / ".git").exists():
            _ws._scrub_git_config(dest)
            # Update existing checkout: fetch all branches, then
            # check out + fast-forward the requested one.
            code, out, err = await _ws._run_git_cmd(
                ["fetch", clean_url,
                 "+refs/heads/*:refs/remotes/origin/*"],
                dest, auto_init=False, env=auth_env)
            if code != 0:
                return f"Fetch failed: {_redact(err or out)}"
            code, out, err = await _ws._run_git_cmd(
                ["checkout", branch], dest, auto_init=False)
            if code != 0:
                code, out, err = await _ws._run_git_cmd(
                    ["checkout", "-b", branch, f"origin/{branch}"],
                    dest, auto_init=False)
                if code != 0:
                    # Branch doesn't exist remotely either — create
                    # it locally from the current HEAD.
                    code, out, err = await _ws._run_git_cmd(
                        ["checkout", "-b", branch], dest, auto_init=False)
                    if code != 0:
                        return (f"Synced refs but could not check out "
                                f"'{branch}': {_redact(err or out)}")
            ff_code, _, ff_err = await _ws._run_git_cmd(
                ["merge", "--ff-only", f"origin/{branch}"],
                dest, auto_init=False)
            ff_note = ("" if ff_code == 0 else
                       "\nNote: could not fast-forward (local commits "
                       "or no matching remote branch) — working tree "
                       "left as-is.")
            _ws._install_precommit_hook(dest)  # refresh on every sync
            action = "Updated existing checkout"
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            code, out, err = await _ws._run_git_cmd(
                ["clone", clean_url, str(dest)],
                dest.parent, auto_init=False, env=auth_env)
            if code != 0:
                return f"Clone failed: {_redact(err or out)}"
            await _ws._run_git_cmd(["config", "user.email", "agent@pantheon.local"],
                               dest, auto_init=False)
            await _ws._run_git_cmd(["config", "user.name", "Pantheon Agent"],
                               dest, auto_init=False)
            _ws._install_precommit_hook(dest)
            code, out, err = await _ws._run_git_cmd(
                ["checkout", branch], dest, auto_init=False)
            if code != 0:
                await _ws._run_git_cmd(["checkout", "-b", branch],
                                   dest, auto_init=False)
            ff_note = ""
            action = "Cloned"

        _, head, _ = await _ws._run_git_cmd(["log", "-1", "--oneline"],
                                        dest, auto_init=False)
        rel = dest.relative_to(_ws._get_workspace_base(effective_project))
        return (f"{action} {owner}/{repo} at workspace path '{rel}/'.\n"
                f"Branch: {branch}\nHEAD: {head}\n"
                f"git_* tools and run_command now operate on this "
                f"checkout; read_file/write_file reach it via "
                f"'{rel}/<path>'.{ff_note}")

    elif tool_name == "git_status":
        code, stdout, stderr = await _ws._run_git_cmd(["status", "--porcelain"], cwd)
        if code != 0:
            return f"Git status error: {stderr or stdout}"
        return stdout if stdout else "Working tree clean."

    elif tool_name == "git_create_branch":
        branch_name = tool_args["branch_name"]
        code, stdout, stderr = await _ws._run_git_cmd(["checkout", "-b", branch_name], cwd)
        if code != 0:
            # Switch to existing branch if branch already exists
            code, stdout, stderr = await _ws._run_git_cmd(["checkout", branch_name], cwd)
            if code != 0:
                return f"Error checking out branch {branch_name}: {stderr or stdout}"
            return f"Switched to existing branch {branch_name}"
        return f"Created and switched to branch {branch_name}"

    elif tool_name == "git_merge":
        if tool_args.get("abort"):
            code, stdout, stderr = await _ws._run_git_cmd(
                ["merge", "--abort"], cwd, auto_init=False)
            if code != 0:
                return f"Could not abort merge: {stderr or stdout}"
            return "Merge aborted — working tree restored."

        branch = (tool_args.get("branch") or "").strip()
        if not branch:
            return "git_merge: 'branch' is required (or pass abort=true)."

        # Resolve the ref: as-given first, then origin/<branch>
        ref = branch
        code, _, _ = await _ws._run_git_cmd(
            ["rev-parse", "--verify", "--quiet", ref], cwd, auto_init=False)
        if code != 0:
            code, _, _ = await _ws._run_git_cmd(
                ["rev-parse", "--verify", "--quiet", f"origin/{branch}"],
                cwd, auto_init=False)
            if code != 0:
                return (f"git_merge: ref '{branch}' not found locally or as "
                        f"origin/{branch}. Run git_sync_repo first to fetch "
                        f"all remote branches.")
            ref = f"origin/{branch}"

        _, cur_branch, _ = await _ws._run_git_cmd(
            ["branch", "--show-current"], cwd, auto_init=False)
        cur_branch = cur_branch.strip() or "(detached HEAD)"
        msg = tool_args.get("message") or f"Merge {ref} into {cur_branch}"
        code, stdout, stderr = await _ws._run_git_cmd(
            ["merge", "--no-ff", "-m", msg, ref], cwd, auto_init=False)
        if code == 0:
            _, head, _ = await _ws._run_git_cmd(
                ["log", "-1", "--oneline"], cwd, auto_init=False)
            return f"Merged '{ref}' into '{cur_branch}'.\nHEAD: {head}"

        _, conflicted, _ = await _ws._run_git_cmd(
            ["diff", "--name-only", "--diff-filter=U"], cwd, auto_init=False)
        files = [f for f in conflicted.splitlines() if f.strip()]
        if not files:
            # Failed for a non-conflict reason (dirty tree, etc.) —
            # make sure no half-merge state lingers.
            await _ws._run_git_cmd(["merge", "--abort"], cwd, auto_init=False)
            return (f"Merge of '{ref}' failed (no conflicts): "
                    f"{stderr or stdout}\nAny partial merge was aborted.")

        sections = []
        for f in files[:10]:
            try:
                lines = (cwd / f).read_text(errors="replace").splitlines()
            except Exception:
                lines = []
            hunks, snippet, capture = [], [], False
            for i, ln in enumerate(lines, 1):
                if ln.startswith("<<<<<<< "):
                    capture, snippet = True, [f"  line {i}:"]
                if capture:
                    snippet.append("  " + ln)
                if capture and ln.startswith(">>>>>>> "):
                    capture = False
                    hunks.append("\n".join(snippet))
            joined = "\n".join(hunks[:5])
            if len(joined) > 4000:
                joined = joined[:4000] + "\n  …(truncated)"
            sections.append(f"--- {f} ---\n{joined or '  (markers not shown — read the file)'}")
        more = (f"\n(+{len(files) - 10} more conflicted files)"
                if len(files) > 10 else "")
        return (f"Merge of '{ref}' into '{cur_branch}' has CONFLICTS "
                f"in {len(files)} file(s):{more}\n\n"
                + "\n\n".join(sections)
                + "\n\nTO RESOLVE: edit ONLY the conflicted regions in "
                  "each file — choose or combine the code between the "
                  "<<<<<<< and >>>>>>> markers, delete the markers, and "
                  "leave the rest of the file untouched. Do NOT rewrite "
                  "whole files from memory. Validate with run_command "
                  "(compiler/tests), then git_commit to conclude the "
                  "merge. To bail out, call git_merge with abort=true.")

    elif tool_name == "git_commit":
        message = tool_args["message"]
        files = tool_args.get("files") or []
        if files:
            for f in files:
                code, stdout, stderr = await _ws._run_git_cmd(["add", f], cwd)
                if code != 0:
                    return f"Error staging file {f}: {stderr or stdout}"
        else:
            code, stdout, stderr = await _ws._run_git_cmd(["add", "."], cwd)
            if code != 0:
                return f"Error staging changes: {stderr or stdout}"

        # Refuse to commit unresolved conflict markers. ('=======' is
        # not matched alone — it false-positives on setext/reST
        # headings; the <<< / >>> lines are unambiguous.)
        code, hits, _ = await _ws._run_git_cmd(
            ["grep", "--cached", "-nE", "^(<{7}|>{7})( |$)"],
            cwd, auto_init=False)
        if code == 0 and hits.strip():
            return ("Refusing to commit: unresolved merge-conflict "
                    "markers are staged:\n" + hits[:1500] +
                    "\nResolve the conflicts first (keep the right "
                    "code, delete the <<<<<<</=======/>>>>>>> marker "
                    "lines), then git_commit again.")

        code, stdout, stderr = await _ws._run_git_cmd(["commit", "-m", message], cwd)
        if code != 0:
            return f"Error committing: {stderr or stdout}"
        _, cur_branch, _ = await _ws._run_git_cmd(
            ["branch", "--show-current"], cwd, auto_init=False)
        return (f"Committed successfully on branch "
                f"'{cur_branch.strip() or '(detached HEAD)'}': {stdout}")

    elif tool_name == "git_push_pr":
        from api.connections import (
            get_connection, get_token,
            get_project_repo_for_tools,
        )
        from integrations.github import GitHubClient
        spec = get_project_repo_for_tools(effective_project)
        if not spec:
            return (
                f"No repo bound to project {effective_project}. Bind one "
                f"in Settings → Connections (add a PAT) then in the "
                f"Projects page pick a repo for this project."
            )
        conn_row = get_connection(spec["connection_id"])
        if not conn_row:
            return "Connection not found. Re-add the GitHub PAT in Settings → Connections."
        token = get_token(conn_row["id"])
        if not token:
            return f"Connection {conn_row['id']} has no stored token. Re-add it in Settings → Connections."

        owner = spec["owner"]
        repo = spec["repo"]

        code, stdout, stderr = await _ws._run_git_cmd(["branch", "--show-current"], cwd)
        if code != 0 or not stdout.strip():
            return f"Error getting current branch: {stderr or stdout}"
        branch_name = stdout.strip()

        remote_url = f"https://github.com/{owner}/{repo}.git"
        _ws._scrub_git_config(cwd)
        code, stdout, stderr = await _ws._run_git_cmd(
            ["push", "-u", remote_url, branch_name], cwd, env=_ws._git_auth_env(token))
        if code != 0:
            safe_err = (stderr or stdout).replace(token, "********")
            return f"Error pushing to remote branch {branch_name}: {safe_err}"

        base_branch = tool_args.get("base") or conn_row.get("default_branch") or "main"
        client = GitHubClient(token)
        try:
            pr_res = await client.create_pr(
                owner,
                repo,
                title=tool_args["title"],
                head=branch_name,
                base=base_branch,
                body=tool_args.get("body") or "",
            )
            from api.connections import mark_used
            mark_used(conn_row["id"])
            return (
                f"Successfully pushed branch {branch_name} to remote and "
                f"created PR #{pr_res.get('number')} in {owner}/{repo}: "
                f"{pr_res.get('html_url')}"
            )
        except Exception as e:
            return (
                f"Branch {branch_name} pushed to remote successfully, but "
                f"failed to create Pull Request: {e}"
            )

    else:
        return f"Unknown git tool: {tool_name}"


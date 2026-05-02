-- Phase G: IA refactor — task_runs, project_repo_bindings, project_mcp_enablement,
-- project_settings (per-project defaults).

CREATE TABLE IF NOT EXISTS task_runs (
    id              TEXT PRIMARY KEY,        -- UUID
    task_id         TEXT NOT NULL,           -- APScheduler job id (the schedule)
    task_name       TEXT NOT NULL,
    project_id      TEXT NOT NULL DEFAULT 'default',
    description     TEXT,
    status          TEXT NOT NULL,           -- 'queued'|'running'|'completed'|'failed'|'cancelled'
    started_at      TEXT,
    completed_at    TEXT,
    duration_ms     INTEGER,
    result          TEXT,                    -- JSON
    error           TEXT,
    session_id      TEXT,                    -- conversation produced
    artifact_id     TEXT                     -- artifact produced (if any)
);
CREATE INDEX IF NOT EXISTS idx_task_runs_project ON task_runs(project_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_task_runs_status  ON task_runs(status, started_at DESC);

-- Per-project repo binding. Connection lives in github_connections (already
-- exists). Binding pins one repo from a connection to a project.
CREATE TABLE IF NOT EXISTS project_repo_bindings (
    project_id      TEXT PRIMARY KEY,
    connection_id   TEXT NOT NULL,
    owner           TEXT NOT NULL,
    repo            TEXT NOT NULL,
    default_branch  TEXT NOT NULL DEFAULT 'main',
    bound_at        TEXT NOT NULL
);

-- Per-project MCP server enablement. Default = enabled if no row.
CREATE TABLE IF NOT EXISTS project_mcp_enablement (
    project_id      TEXT NOT NULL,
    server_id       TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (project_id, server_id)
);

-- Per-project chat-time defaults (seeds the chat Personality tab).
CREATE TABLE IF NOT EXISTS project_settings (
    project_id          TEXT PRIMARY KEY,
    persona             TEXT,                -- persona id (or null)
    tone_weight         TEXT DEFAULT 'balanced',  -- 'focused'|'balanced'|'broad'
    context_focus       TEXT DEFAULT 'balanced',  -- 'focused'|'balanced'|'broad'
    skill_discovery     TEXT DEFAULT 'off',  -- 'off'|'auto'|'always'
    updated_at          TEXT NOT NULL
);

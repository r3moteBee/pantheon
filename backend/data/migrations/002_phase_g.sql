-- Per-project chat settings (phase_g.db). Applied on every start by
-- utils/chat_settings._connect, which also drops the empty, never-used
-- task_runs / project_repo_bindings / project_mcp_enablement tables this file
-- used to create (repo bindings live in sources.db, api/connections.py).

-- Per-project chat-time defaults (seeds the chat Personality tab).
CREATE TABLE IF NOT EXISTS project_settings (
    project_id          TEXT PRIMARY KEY,
    persona             TEXT,                -- persona id (or null)
    tone_weight         TEXT DEFAULT 'balanced',  -- 'focused'|'balanced'|'broad'
    context_focus       TEXT DEFAULT 'balanced',  -- 'focused'|'balanced'|'broad'
    skill_discovery     TEXT DEFAULT 'off',  -- 'off'|'auto'|'always'
    updated_at          TEXT NOT NULL
);

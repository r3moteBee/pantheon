-- Phase H.1: unified jobs table — replaces the narrower task_runs.
-- Source of truth for any agent-initiated background work.

CREATE TABLE IF NOT EXISTS jobs (
    id                TEXT PRIMARY KEY,
    job_type          TEXT NOT NULL,            -- handler key
    project_id        TEXT NOT NULL DEFAULT 'default',
    status            TEXT NOT NULL DEFAULT 'queued',
                                                -- queued | running | completed |
                                                -- failed | cancelled | stalled
    title             TEXT,
    description       TEXT,
    payload           TEXT NOT NULL DEFAULT '{}',
    result            TEXT,
    error             TEXT,
    progress          TEXT,
    attempts          INTEGER NOT NULL DEFAULT 0,
    max_attempts      INTEGER NOT NULL DEFAULT 1,
    scheduled_for     TEXT,
    timeout_seconds   INTEGER,
    parent_job_id     TEXT,
    cancel_requested  INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    started_at        TEXT,
    completed_at      TEXT,
    last_heartbeat_at TEXT,
    -- Cross-references to other system objects
    schedule_id       TEXT,
    session_id        TEXT,
    artifact_id       TEXT,
    pr_url            TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_project_status
    ON jobs(project_id, status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status_due
    ON jobs(status, scheduled_for)
    WHERE status IN ('queued','running');
CREATE INDEX IF NOT EXISTS idx_jobs_type_project
    ON jobs(job_type, project_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_running_heartbeat
    ON jobs(status, last_heartbeat_at)
    WHERE status = 'running';

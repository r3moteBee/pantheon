-- Phase E: artifacts replace the workspace files model.
-- Single-user, project-scoped, full version history, content-addressed
-- binary blobs in data/blobs/.

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,                     -- UUID
    project_id TEXT NOT NULL DEFAULT 'default',
    path TEXT NOT NULL,                      -- logical path, e.g. "chats/2026-04/notes.md"
    title TEXT,
    content_type TEXT NOT NULL,              -- mime-ish: 'text/markdown', 'text/x-python',
                                             --          'image/png', 'image/svg+xml',
                                             --          'application/pdf', 'chat-export', ...
    content TEXT,                            -- text payload, NULL if binary
    blob_path TEXT,                          -- path under data/blobs/ relative, NULL if text
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',         -- JSON array
    source TEXT NOT NULL DEFAULT '{}',       -- JSON: {kind, session_id?, ...}
    pinned INTEGER NOT NULL DEFAULT 0,
    current_version_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS artifact_versions (
    id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    content TEXT,
    blob_path TEXT,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    edit_summary TEXT,
    edited_by TEXT,                          -- 'user' | 'agent' | 'migration' | session_id
    created_at TEXT NOT NULL,
    UNIQUE (artifact_id, version_number)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_artifacts_project_path
    ON artifacts(project_id, path) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_artifacts_project_created
    ON artifacts(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_artifacts_pinned
    ON artifacts(project_id, pinned) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_artifact_versions_artifact
    ON artifact_versions(artifact_id, version_number DESC);

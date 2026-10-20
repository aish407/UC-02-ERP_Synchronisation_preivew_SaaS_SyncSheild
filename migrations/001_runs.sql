CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id), user_id TEXT NOT NULL REFERENCES users(id),
 kind TEXT NOT NULL, status TEXT NOT NULL, input_rows INTEGER NOT NULL, valid_rows INTEGER NOT NULL,
 invalid_rows INTEGER NOT NULL, issue_count INTEGER NOT NULL DEFAULT 0,
 created_count INTEGER NOT NULL DEFAULT 0, updated_count INTEGER NOT NULL DEFAULT 0,
 unchanged_count INTEGER NOT NULL DEFAULT 0, idempotency_key TEXT, input_fingerprint TEXT,
 result_json TEXT NOT NULL, error_code TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS runs_workspace_date_idx ON runs(workspace_id,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS runs_workspace_idempotency_idx ON runs(workspace_id,idempotency_key) WHERE idempotency_key IS NOT NULL;

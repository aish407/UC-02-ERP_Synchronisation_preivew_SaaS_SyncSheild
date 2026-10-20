CREATE TABLE IF NOT EXISTS daily_usage (
 workspace_id TEXT NOT NULL REFERENCES workspaces(id), usage_date TEXT NOT NULL,
 usage_count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(workspace_id,usage_date)
);
INSERT OR IGNORE INTO daily_usage(workspace_id,usage_date,usage_count)
SELECT workspace_id,date(created_at),COUNT(*) FROM runs GROUP BY workspace_id,date(created_at);

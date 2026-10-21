ALTER TABLE workspaces ADD COLUMN subscription_started_at TEXT;
ALTER TABLE workspaces ADD COLUMN subscription_expires_at TEXT;
ALTER TABLE payment_intents ADD COLUMN reference_id TEXT;
ALTER TABLE payment_intents ADD COLUMN billing_country TEXT NOT NULL DEFAULT 'India';
CREATE UNIQUE INDEX IF NOT EXISTS payment_intents_reference_idx ON payment_intents(reference_id) WHERE reference_id IS NOT NULL;

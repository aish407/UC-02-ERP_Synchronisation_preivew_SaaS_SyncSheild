-- Revoke roles that legacy prototype versions may have allowed users to self-select.
-- This runs once; platform admins provisioned through the protected admin endpoint persist.
UPDATE users SET role='Operator' WHERE role='Admin';

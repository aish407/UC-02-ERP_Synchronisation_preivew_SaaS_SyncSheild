---
name: sync-preview
description: Verify SyncShield customer flows and admin controls, reproduce acceptance fixtures, troubleshoot failures, and deploy only to team-assigned Azure resources.
---

# SyncShield verification and deployment

## Trigger

Use this skill after modifying SyncShield, when a judge or teammate asks to reproduce UC-02 checks, when diagnosing local/API issues, or when deploying to an explicitly assigned Azure environment.

## Required inputs

- Repository root and a local Python 3.10+ / Node.js environment.
- The three included sample CSV fixtures.
- For platform-admin checks: `SYNCSHIELD_ADMIN_EMAIL` and `SYNCSHIELD_ADMIN_PASSWORD` supplied as environment variables; never ask to paste or record the password.
- For Azure deployment only: assigned subscription, resource group, App Service Plan ID, region, and globally unique Web App name. Verify the active Azure subscription before planning.

## Preflight and stop conditions

- Read `README.md`, check the repository root, and use isolated demo accounts only.
- Never send ERP write requests. Preview output must remain clearly labelled preview-only.
- Never run Terraform apply without explicit team assignment of the target resource group and plan and a reviewed plan. Do not invent IDs or deploy to a shared/unassigned resource.
- Stop if admin credentials are absent/invalid, the intended Azure subscription is unclear, or the reviewed plan targets unassigned resources.
- Never print or save passwords, cookies, customer uploads, card data, or payment-provider secrets in evidence.

## Exact local checks

From the repo root:

```powershell
python -m py_compile server.py
node --check public/app.js
python -m unittest discover -s tests -v
.\scripts\verify-local.ps1 -SkipHealthCheck
```

If the app is running, use `Invoke-RestMethod http://localhost:8000/health`; expect HTTP 200 with `status: ok`. To capture a redacted local transcript, run `scripts/capture-evidence.ps1 -SkipHealthCheck` (or omit the flag when the health endpoint is reachable).

## Expected fixture results

- `outputs/syncshield_old_customers.csv` baseline followed by `outputs/syncshield_new_customers.csv`: 4 UPDATE, 3 UNCHANGED, 3 CREATE.
- `outputs/syncshield_invalid_customers.csv`: duplicate ID R1, name R2, version R3, and status R4 issues are visible; invalid preview is rejected before quota debit.
- Basic permits 3 unique validations per UTC day; Pro permits preview. Matching idempotency keys replay saved results without another debit; changed input under the same key returns conflict.
- Test two tenants, Viewer/Operator permissions, suspended subscription behavior, forced preview failure (`SYNCSHIELD_FORCE_PROCESS_FAILURE=1`), admin login, feedback submission, and server-denied access to `/api/admin/overview` for regular users.
- Admin dashboard labels “online now” as recent authenticated session activity within five minutes, not a live socket count.

## Azure deploy and verify

1. Confirm assigned IDs and `az account show` subscription.
2. Run `scripts/deploy-azure.ps1` with the assigned resource group, plan ID, location, and app name. Review the Terraform plan; without `-Apply`, no infrastructure is created.
3. Only after scope review, rerun with `-Apply`, then check `/health` and run fixtures.
4. The current scaffold uses single-instance SQLite under App Service `/home`. Do not scale out; PostgreSQL support is required before production multi-instance use.

## Troubleshooting, rollback, cleanup

- Local connection refused: start `python server.py` from the repo root and keep it running.
- `/health` failure: inspect app startup/logs, port 8000 and deployed package; restart once and retry.
- Migration issue: stop writes, back up the database, inspect the first unapplied migration, and fix forward with a new additive migration; do not delete the database to clear state.
- Processor failure: inspect the saved Failed run and audit event; retry only with its original idempotency key and unchanged payload.
- Rollback using the team's prior known-good deployment package/image or slot, then check `/health` and history.
- Do not destroy shared resources as rollback. Cleanup is limited to resources explicitly owned by this team; get explicit confirmation before any destructive cleanup, then verify the exact resource names and scope first.

## Handover

Record useful AI assistance, corrections, and verification commands in the team AIFE notes. Evidence must be redacted and reproducible from a fresh assistant session; this skill does not substitute for a recorded Azure deployment or no-change second plan.

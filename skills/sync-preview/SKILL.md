---
name: sync-preview
description: Validate and safely verify SyncShield runs and previews, diagnose common local failures, and deploy only to assigned Azure resources.
---

# SyncShield verification and deployment

Use this skill when validating SyncShield changes, debugging API/run failures, generating judge-ready local evidence, or deploying the prototype to an assigned Azure App Service plan.

## Preconditions

- Work from the repository root and read its `README.md` and this skill.
- Do not use customer production data. Use `outputs/syncshield_old_customers.csv`, `outputs/syncshield_new_customers.csv`, and `outputs/syncshield_invalid_customers.csv`.
- Never send an ERP write request. SyncShield produces previews only.
- Do not run Terraform apply or Azure deployment without assigned resource group, App Service plan ID, location, and web app name. Never invent resource IDs or target shared resources not assigned to this project.
- Never collect or record raw passwords, cookies, payment card data, secrets, or uploaded customer files in evidence.

## Local checks

1. Run `python -m py_compile server.py`.
2. Run `node --check public/app.js`.
3. Run `python -m unittest discover -s tests -v`.
4. If the local app is intended to be live, run `Invoke-RestMethod http://localhost:8000/health`; expected JSON has `status: ok` and `service: SyncShield`.
5. Run `scripts/verify-local.ps1`. If the app is intentionally stopped, pass `-SkipHealthCheck`.
6. Record the command output/date in evidence without credentials or personal data.

## Acceptance fixtures

- Load `outputs/syncshield_old_customers.csv` as the workspace baseline, then upload `outputs/syncshield_new_customers.csv` as incoming records. Expected actions: 4 UPDATE, 3 UNCHANGED, 3 CREATE. Confirm the UI labels the result preview-only and that no ERP endpoint is called.
- Validate `outputs/syncshield_invalid_customers.csv`. Confirm duplicate record ID (R1), name (R2), version (R3), and status (R4) violations identify row, field and explanation, including multiple issues where applicable.
- Use an isolated test account/workspace for access checks. Confirm a second workspace cannot list or fetch the first workspace's runs, quota or baseline.
- Exercise idempotency by repeating the same request/key, then changing the payload under that key. Expected: same saved result without extra quota debit; changed payload receives conflict.
- Check Basic/Pro gates, quota denial, suspended tenant submission denial with history reads still available, and forced processor failure (`SYNCSHIELD_FORCE_PROCESS_FAILURE=1`) in a local-only process.

## Azure deployment

1. Confirm the provided resource group and service plan are explicitly assigned to this project and that `az account show` points at the intended subscription.
2. Run `scripts/deploy-azure.ps1` with all four required parameters and review the Terraform plan. Without `-Apply`, the script only produces the plan.
3. Apply only after reviewing resource names and scope. The script deploys source ZIP after Terraform provisions the web app.
4. Check `https://<web-app-name>.azurewebsites.net/health`, sign in with a non-sensitive demo account, and rerun the acceptance fixtures.
5. The scaffold uses SQLite under App Service `/home` and supports a single instance only. Do not enable scale-out. A PostgreSQL migration is required before claiming production multi-instance SaaS readiness.

## Troubleshooting and rollback

- `/health` unavailable: confirm app process start, App Service logs, port 8000 setting and deployment package contents; restart the app once and retest.
- Local connection refused: from the repo root start `python server.py`, leave it running, then request `http://localhost:8000/health`.
- Migration failure: stop writes, preserve a copy of the database, inspect the earliest unapplied `migrations/*.sql`, fix forward with an additive migration, then rerun tests. Do not delete production database files to clear migration state.
- Invalid rows: inspect per-row issue details and validation report; do not preview a rejected batch until the violations are fixed.
- Processor failure: inspect saved run status/error code and audit entry; replay only with the original idempotency key and unchanged input.
- Rollback: use the prior known-good ZIP package or Azure deployment slot/version according to the team's assigned deployment procedure, then verify `/health` and run history. Do not destroy the shared App Service plan or resource group as a rollback method.


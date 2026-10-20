# UC-02 local evidence

Run `scripts/verify-local.ps1` with the app running to record reproducible local verification. The script reports syntax checks, unit/acceptance tests and `/health`. Keep generated evidence free of credentials, session cookies, uploaded customer files and payment details. Azure deployment evidence is only valid after a deployment to the team-assigned resource group and plan; this repository does not contain that environment's resource identifiers.

# Azure deployment scaffold

This scaffold provisions one Linux Web App on an **already assigned App Service plan**. It does not create a resource group, service plan, database, or monitoring workspace. Supply only the resource group and plan assigned to your team. The deploy script creates a plan by default; `-Apply` is required to provision and deploy.

The current application uses SQLite. The scaffold places the DB under App Service `/home` storage and is intended for a single-instance judging/demo deployment only. It is not a multi-instance production architecture or a PostgreSQL implementation. Do not scale out this prototype. Before a production deployment, replace SQLite with a managed PostgreSQL backend and add monitoring/secret management to the assigned infrastructure.

🛡️SyncShield — ERP Synchronization Preview SaaS
Validate. Compare. Preview. Synchronize with Confidence.
SyncShield is a locally hosted SaaS prototype that helps businesses validate customer data, identify changes between existing and incoming datasets, and preview ERP synchronization operations before execution.
It provides a workspace-based environment with data validation, change detection, usage tracking, run history, subscription plans, and administrative controls.
Built for safer ERP data synchronization.
SyncShield previews proposed changes without connecting to or modifying a live ERP system.

🚀 Project Overview
ERP synchronization involves transferring customer information between systems while maintaining data consistency and integrity. Invalid records, duplicate identifiers, and unexpected changes can lead to data quality issues.
SyncShield addresses this challenge by allowing users to:
Validate incoming customer datasets before processing.
Compare existing customer records with incoming data.
Identify records that need to be created, updated, or left unchanged.
Preview synchronization results before taking action.
Track previous validation and synchronization-preview runs.
Manage workspace usage, subscription plans, and access controls.

The application is designed as a multi-tenant SaaS prototype, with workspace-scoped data and usage controls.
✨ Key Features

 1. 📂 Customer Data Validation
Upload customer datasets in CSV or JSON format and validate their structure and contents.
Detect duplicate record IDs.
Identify missing customer names.
Validate version numbers.
Check supported status values.
Process datasets containing up to 10 records per request.
 
 2. 🔄 ERP Synchronization Preview
Compare an existing customer snapshot with a new incoming dataset to identify the proposed changes.
Operation
Description
🟢 CREATE
A new customer record is detected.
🟡 UPDATE
An existing customer's information has changed.
🔵 UNCHANGED
The customer record requires no changes.
Example: Comparing the included sample datasets demonstrates four updates, three unchanged records, and three new customer records.
These are proposed operations only. SyncShield does not execute changes against a live ERP.
 
 3. 📊 Workspace Dashboard
Monitor data-processing activity through a centralized overview.
Persisted run metrics.
Daily processing volume.
Recent workspace runs.
Historical run information.
 
 4. 🕘 Run History & Export
Maintain a record of accepted processing runs.
Review previously saved results.
Inspect individual run details.
Export saved run information.
Track failed processing attempts.
 
 5. 🏢 Multi-Tenant Workspace Isolation
Provide separate workspaces for individual accounts.
Workspace-scoped dashboard metrics.
Isolated run history and usage information.
Session-based authorization.
Rejection of unauthorized workspace overrides.
Workspace suspension controls.
 
 6. 💳 Subscription & Usage Management
Explore different subscription tiers and their associated capabilities.
Capability
Basic
Pro
Customer data validation
✅
✅
Synchronization preview
❌
✅
Daily accepted-run quota
3
20
Workspace dashboard
✅
✅
Run history
✅
✅
Usage limits and subscription features reflect the current prototype configuration.
 
 7. 🔐 Authentication & Security
Implement foundational security controls for the local prototype.
Password hashing using PBKDF2-HMAC-SHA256 with salts.
Random session tokens stored as hashes.
HttpOnly and SameSite session cookies.
Workspace-scoped authorization.
Audit events for supported administrative changes.
Idempotency protection for repeated submissions.
 
 8. 🛠️ Administrative Controls
Provide administrative visibility into workspace operations.
Review workspace quotas.
Compare subscription plans.
Inspect run statuses.
Switch demo roles.
Access role-restricted diagnostics and audit events.
 
 9. 💰 Payment Checkout Preview
Explore the subscription checkout experience.
Illustrative pricing of ₹999 per month.
Optional Razorpay-hosted payment links.
Secure handling of payment credentials by the hosted payment provider.
Important: Live payment activation is not implemented. Without the required Razorpay credentials, the checkout page remains available for demonstration but does not confirm payment or upgrade the subscription.
🔄 How SyncShield Works



         ┌────────────────────────┐
         │ User Signs Up          │
         └────────────┬───────────┘
                      │
                      ▼
         ┌────────────────────────┐
         │ Select Basic or Pro    │
         └────────────┬───────────┘
                      │
                      ▼
         ┌────────────────────────┐
         │ Upload Customer Data   │
         │ CSV / JSON             │
         └────────────┬───────────┘
                      │
                      ▼
         ┌────────────────────────┐
         │ Validate Input Records │
         └────────────┬───────────┘
                      │
                      ▼
         ┌────────────────────────┐
         │ Compare Existing Data  │
         │ With Incoming Records  │
         └────────────┬───────────┘
                      │
                      ▼
         ┌────────────────────────┐
         │ Preview CREATE, UPDATE │
         │ & UNCHANGED            │
         └────────────┬───────────┘
                      │
                      ▼
         ┌────────────────────────┐
         │ Save Results & Review  │
         │ Run History            │
         └────────────────────────┘

   
Result: A structured preview of proposed customer-data changes before any live ERP synchronization takes place.
🧰 Technology Stack
Component
Technology
Backend
Python
Database
SQLite
Frontend
HTML, CSS, JavaScript
Data formats
CSV, JSON
Authentication
PBKDF2-HMAC-SHA256 password hashing
Session management
Random tokens and secure cookie attributes
Payment integration
Optional Razorpay Payment Links
Infrastructure
Terraform, Azure App Service deployment scaffold
Testing
Python unittest, JavaScript syntax checks, PowerShell verification scripts
The stack above reflects the technologies and integrations documented in the current repository. The Azure infrastructure is a deployment scaffold, not a completed production deployment.

📁 Project Structure

UC-02-ERP_Synchronisation_preivew_SaaS_SyncSheild/
│
├── server.py
├── schema.sql
├── syncshield.db
│

├── migrations/
│ └── Ordered database migration scripts
│

├── public/
│ └── app.js
│

├── outputs/
│ ├── syncshield_old_customers.csv
│ ├── syncshield_new_customers.csv
│ └── syncshield_invalid_customers.csv
│

├── tests/
│ └── Automated tests
│

├── scripts/
│ ├── verify-local.ps1
│ └── deploy-azure.ps1
│

├── infra/
│ └── terraform/
│

├── skills/
│ └── sync-preview/
│ └── SKILL.md
│

└── README.md
This is a logical overview based on the documented files. Adjust the tree if the actual repository contains additional files or different directory names.
⚙️ Installation & Setup
Prerequisites
Python 3.10 or newer.
Git.
A terminal such as PowerShell.
A modern web browser.
No third-party Python packages are required for the documented local setup.

Step 1: Clone the Repository
git clone <YOUR_REPOSITORY_URL>
Navigate into the project directory:
cd UC-02-ERP_Synchronisation_preivew_SaaS_SyncSheild
Replace <YOUR_REPOSITORY_URL> with your actual GitHub repository URL.

Step 2: Start the Application
python server.py
The application initializes its SQLite database on first startup, unless a different database path is configured.

Step 3: Open the Application
Open the local application address printed by the server, or use the application's configured local URL.

Step 4: Create an Account
Open /signup.
Create an account.
Complete the initial setup.
Select the Basic or Pro plan.
Upload a CSV or JSON customer dataset.
Review the validation results and available dashboard features.

Step 5: Try Synchronization Preview
To explore the complete preview workflow:
Select the Pro plan.
Upload outputs/syncshield_old_customers.csv as the existing customer snapshot.
Upload outputs/syncshield_new_customers.csv as the incoming dataset.
Review the proposed CREATE, UPDATE, and UNCHANGED operations.
Open Run History to inspect the saved results.

Note: The Pro workspace initially contains a seeded customer record. Uploading the old customer snapshot replaces the baseline used for the preview.
🧪 Sample Datasets
The repository includes three datasets for testing different scenarios.

Dataset
Purpose
syncshield_old_customers.csv
Existing customer records used as the baseline.
syncshield_new_customers.csv
Incoming customer records used to demonstrate synchronization changes.
syncshield_invalid_customers.csv
Invalid records used to test data validation.
Expected Preview Results
When the included old and new datasets are compared:
Operation
Expected Count
CREATE
3
UPDATE
4
UNCHANGED
3
Total incoming records
10

These results demonstrate how SyncShield identifies potential changes between customer datasets.

🗄️ Database & Data Integrity
SyncShield uses SQLite to persist workspace and processing information.
Key implementation details include:
A database schema documented in schema.sql.
Ordered, additive database migrations.
Migration tracking through schema_migrations.
Persisted processing results.
Workspace-scoped data access.
Transactional quota reservation.
Idempotent submission handling.
Repeated submissions using the same idempotency key and identical input replay the saved result. Reusing that key with different input returns HTTP 409 Conflict.
These mechanisms help maintain consistency during repeated submissions and processing operations.

🧪 Testing & Verification
Run the Python unit tests:
python -m unittest discover -s tests -v
Check JavaScript syntax:
node --check public/app.js
Compile-check the Python backend:
python -m py_compile server.py
Run the local verification script:

.\scripts\verify-local.ps1 -SkipHealthCheck
Tip: Run these checks from the project root. If the server is already running after code changes, stop it with Ctrl+C and restart it to load the updated application and database migrations.

☁️ Azure Deployment Scaffold
The repository includes Terraform infrastructure definitions and a PowerShell deployment script for an Azure App Service setup.
The deployment script reviews a Terraform plan by default.
Provisioning and deployment require the appropriate explicit configuration and the -Apply option.
Current infrastructure limitations:
The application uses SQLite rather than PostgreSQL.
The deployment scaffold targets a single-instance deployment.
It is not designed for multi-instance database scaling.
A production Azure deployment is not included as a completed feature.

⚠️ Current Limitations
SyncShield is a prototype intended for demonstration and evaluation.
The following capabilities are not yet configured:
Production Azure deployment.
Email verification.
Password reset and production account recovery.
Microsoft sign-in.
Workspace invitations.
Payment webhook verification and automatic paid-plan activation.
A production-ready, scalable database architecture.
The subscription and role-management controls are intended for local demonstration, not production administration.
Before enabling real payments, configure Razorpay credentials and implement a publicly reachable webhook with signature verification and reliable payment-state handling.

🔮 Future Enhancements
Potential future improvements include:
AI-assisted anomaly detection: Identify unusual customer-data changes using machine learning.
Advanced data quality scoring: Score incoming records based on completeness, consistency, and validity.
Intelligent change-risk analysis: Flag synchronization previews that deserve additional review.
ERP connectors: Introduce controlled integrations with supported ERP platforms.
Approval workflows: Require human approval before executing proposed changes.
Scalable database architecture: Migrate to PostgreSQL and implement a deployment architecture suitable for multiple application instances.
Enhanced observability: Add structured logs, monitoring, and operational alerts.
These are proposed enhancements, not claims about currently implemented features.

💡 Why SyncShield?
Traditional data synchronization can make it difficult to understand the impact of incoming changes before processing them.
SyncShield introduces a preview-first workflow that helps teams:
Detect invalid records before processing.
Understand proposed data changes.
Review processing history.
Enforce workspace-level usage controls.
Reduce uncertainty before a future ERP integration.
Our Approach
Validate first. Preview changes. Review the results. Synchronize only when authorized.

👥 Team & Project Information
Detail
Information
Project
SyncShield
Use case
UC-02 — ERP Synchronization Preview
Category
SaaS / Enterprise Software
Primary focus
Customer-data validation and synchronization previews
Current status
Locally hosted prototype
Developed as a prototype for safer and more transparent ERP data synchronization.

🛡️ SyncShield
Making ERP synchronization more transparent, controlled, and reliable. 

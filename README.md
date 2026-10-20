# 🛡️ SyncShield
### ERP Synchronization Preview SaaS

**Validate. Compare. Preview. Synchronize with Confidence.**

What if you could identify every proposed customer-data change *before* it reaches your ERP system?

Meet **SyncShield** — a locally hosted SaaS prototype that helps businesses validate customer data, detect changes between existing and incoming datasets, and preview synchronization operations before execution.

From data validation and change detection to workspace isolation, usage tracking, and processing history, SyncShield brings greater visibility and control to the customer-data synchronization workflow.

> **Our principle:** No surprises. No blind synchronization. Just a clear preview before action.

**UC-02 | ERP Synchronization Preview | SaaS / Enterprise Software**

---

## 🎯 The Problem We're Solving

Customer data rarely stays the same across business systems. Records change, new customers appear, and incoming datasets may contain duplicates, missing fields, or invalid values.

When these changes are difficult to review before processing, businesses face risks such as:

- ❌ Invalid customer records entering a workflow.
- 🔄 Unexpected changes to existing customer information.
- 🔍 Limited visibility into what a synchronization operation will change.
- 📋 Difficulty reviewing previous processing activity.
- ⚠️ Insufficient control over workspace usage and access.

Traditional synchronization workflows need more than the ability to transfer data. They need a way to **understand, validate, and review proposed changes before execution.**

### 💡 Our Solution: SyncShield

SyncShield introduces a **preview-first approach** to ERP synchronization.

It allows users to validate incoming datasets, compare customer records, identify proposed changes, and review processing results through a workspace-based interface.

**Important:** SyncShield previews proposed changes without connecting to or modifying a live ERP system.

---

## ✨ What Makes SyncShield Stand Out?

| Capability | What It Delivers |
|---|---|
| 📂 **Smart Data Validation** | Detects invalid customer records before processing. |
| 🔄 **Change Detection** | Identifies records to create, update, or leave unchanged. |
| 📊 **Workspace Dashboard** | Provides visibility into processing activity and usage. |
| 🕘 **Run History** | Makes previously saved processing results available for review. |
| 🏢 **Workspace Isolation** | Separates workspace data and usage information. |
| 💳 **Subscription Management** | Demonstrates Basic and Pro plans with different capabilities. |
| 🔐 **Security Foundations** | Includes password hashing, session handling, and authorization controls. |
| 🛠️ **Administrative Controls** | Provides role-restricted operational visibility. |
| 💰 **Checkout Preview** | Demonstrates a subscription checkout experience with optional Razorpay Payment Links. |

---

## 🚀 Core Features

### 1. 📂 Customer Data Validation

**Catch data-quality problems before they become processing problems.**

Upload customer datasets in CSV or JSON format and validate their structure and contents before proceeding.

**Validation capabilities include:**
- Duplicate record ID detection.
- Missing customer name identification.
- Version-number validation.
- Supported status-value validation.
- Processing of up to **10 records per request**.

This validation layer helps users identify problematic records before reviewing synchronization changes.

### 2. 🔄 ERP Synchronization Preview

**Know what will change before anything reaches a live ERP system.**

SyncShield compares an existing customer snapshot with an incoming dataset and categorizes the proposed operations.

| Operation | Meaning |
|---|---|
| 🟢 `CREATE` | A new customer record is detected. |
| 🟡 `UPDATE` | An existing customer's information has changed. |
| 🔵 `UNCHANGED` | The customer record requires no changes. |

With the included sample datasets, the expected preview identifies:

- **3** new customer records.
- **4** existing records requiring updates.
- **3** unchanged records.

That's **10 incoming records classified into clear, reviewable outcomes.**

These are proposed operations only. SyncShield does not execute changes against a live ERP.

### 3. 📊 Workspace Dashboard

**Turn processing activity into a clear operational overview.**

The centralized workspace dashboard provides visibility into:

- Persisted processing-run metrics.
- Daily processing volume.
- Recent workspace runs.
- Historical run information.

This gives users a convenient way to review their workspace's processing activity.

### 4. 🕘 Run History & Export

**Every accepted run deserves a clear record.**

SyncShield maintains processing history so users can revisit previously saved results.

Available capabilities include:

- Reviewing previously saved results.
- Inspecting individual run details.
- Exporting saved run information.
- Tracking failed processing attempts.

This makes it easier to understand previous processing activity without relying entirely on memory or manually maintained records.

### 5. 🏢 Multi-Tenant Workspace Isolation

**Separate workspaces. Controlled access. Clear boundaries.**

SyncShield uses a workspace-based model to organize access and processing information for individual accounts.

Its implemented prototype capabilities include:

- Workspace-scoped dashboard metrics.
- Isolated run history and usage information.
- Session-based authorization.
- Rejection of unauthorized workspace overrides.
- Workspace suspension controls.

These controls form the foundation of a multi-tenant SaaS workflow. They do not, by themselves, establish production-grade security.

### 6. 💳 Subscription & Usage Management

**Different plans. Different capabilities. Defined usage limits.**

SyncShield demonstrates subscription-based access through Basic and Pro plans.

| Capability | Basic | Pro |
|---|:---:|:---:|
| Customer data validation | ✅ | ✅ |
| Synchronization preview | ❌ | ✅ |
| Daily accepted-run quota | 3 | 20 |
| Workspace dashboard | ✅ | ✅ |
| Run history | ✅ | ✅ |

Usage limits and subscription features reflect the current prototype configuration.

### 7. 🔐 Authentication & Security

**Build trust into the workflow from the beginning.**

The prototype implements foundational security controls for local use.

- **Password protection:** PBKDF2-HMAC-SHA256 password hashing with salts.
- **Session management:** Random session tokens stored as hashes.
- **Cookie security:** HttpOnly and SameSite session cookies.
- **Authorization:** Workspace-scoped access controls.
- **Auditability:** Audit events for supported administrative changes.
- **Submission protection:** Idempotency handling for repeated requests.

These controls help establish safer application behavior, but they are not a substitute for a complete production security review.

### 8. 🛠️ Administrative Controls

**Operational visibility without losing control.**

Administrative capabilities provide visibility into workspace operations, including:

- Reviewing workspace quotas.
- Comparing subscription plans.
- Inspecting processing-run statuses.
- Switching demo roles.
- Accessing role-restricted diagnostics and audit events.

Administrative and role-management features are intended for local demonstration rather than production administration.

### 9. 💰 Payment Checkout Preview

**Explore the subscription experience without misrepresenting payment status.**

SyncShield includes a demonstration of the subscription checkout workflow.

- Illustrative pricing: **₹999 per month**.
- Optional Razorpay-hosted Payment Links.
- Payment credentials handled by the hosted payment provider.

**Current limitation:** Live payment activation is not implemented.

Without the required Razorpay credentials, the checkout page remains available for demonstration but does not confirm payment or upgrade the subscription.

---

## 🔄 How SyncShield Works

From account creation to a reviewable synchronization preview, the workflow follows a straightforward sequence.

```text
          ┌─────────────────────────┐
          │      User Signs Up      │
          └────────────┬────────────┘
                       │
                       ▼
          ┌─────────────────────────┐
          │   Select Basic or Pro   │
          └────────────┬────────────┘
                       │
                       ▼
          ┌─────────────────────────┐
          │ Upload Customer Dataset │
          │       CSV / JSON        │
          └────────────┬────────────┘
                       │
                       ▼
          ┌─────────────────────────┐
          │   Validate Input Data   │
          └────────────┬────────────┘
                       │
                       ▼
          ┌─────────────────────────┐
          │ Compare Existing and    │
          │ Incoming Customer Data  │
          └────────────┬────────────┘
                       │
                       ▼
          ┌─────────────────────────┐
          │ Preview Proposed        │
          │ CREATE / UPDATE /       │
          │ UNCHANGED Operations    │
          └────────────┬────────────┘
                       │
                       ▼
          ┌─────────────────────────┐
          │ Save Results and Review │
          │       Run History       │
          └─────────────────────────┘
```

### The Outcome

A structured preview of proposed customer-data changes, available for review **before any live ERP synchronization takes place**.

---

## 🧰 Technology Stack

SyncShield combines a lightweight backend, local database, browser-based interface, and supporting development tools.

| Component | Technology |
|---|---|
| Backend | Python |
| Database | SQLite |
| Frontend | HTML, CSS, JavaScript |
| Supported Data Formats | CSV, JSON |
| Password Hashing | PBKDF2-HMAC-SHA256 |
| Session Management | Random tokens and secure cookie attributes |
| Payment Integration | Optional Razorpay Payment Links |
| Infrastructure | Terraform |
| Deployment Target | Azure App Service deployment scaffold |
| Backend Testing | Python `unittest` |
| JavaScript Validation | Node.js syntax checks |
| Local Verification | PowerShell scripts |

The stack reflects the technologies and integrations documented in the current repository.

**Deployment status:** The Azure infrastructure is a deployment scaffold, not a completed production deployment.

---

## 📁 Project Structure

```text
UC-02-ERP_Synchronisation_preview_SaaS_SyncShield/
│
├── server.py
├── schema.sql
├── syncshield.db
├── README.md
│
├── migrations/
│   └── Ordered database migration scripts
│
├── public/
│   └── app.js
│
├── outputs/
│   ├── syncshield_old_customers.csv
│   ├── syncshield_new_customers.csv
│   └── syncshield_invalid_customers.csv
│
├── tests/
│   └── Automated tests
│
├── scripts/
│   ├── verify-local.ps1
│   └── deploy-azure.ps1
│
├── infra/
│   └── terraform/
│
└── skills/
    └── sync-preview/
        └── SKILL.md
```

> **Note:** This is the documented project layout. Actual filenames and directory contents may differ. Replace descriptive placeholders with the real filenames where applicable.

---

## ⚙️ Installation & Setup

### Prerequisites

Ensure that the following are available:

- Python 3.10 or newer.
- Git.
- PowerShell or another compatible terminal.
- A modern web browser.

No third-party Python packages are required for the documented local setup.

### Step 1: Clone the Repository

```bash
git clone <YOUR_REPOSITORY_URL>
cd <PROJECT_DIRECTORY>
```

Replace the placeholders with your actual repository URL and project directory.

### Step 2: Start the Application

```bash
python server.py
```

The application initializes its SQLite database on first startup unless a different database path is configured.

### Step 3: Open the Application

Open the local application address printed by the server, or use the application's configured local URL.

### Step 4: Create an Account

1. Open `/signup`.
2. Create an account.
3. Complete the initial setup.
4. Select the Basic or Pro plan.
5. Upload a CSV or JSON customer dataset.
6. Review the validation results and available dashboard features.

### Step 5: Run a Synchronization Preview

For the complete preview workflow:

1. Select the Pro plan.
2. Upload `outputs/syncshield_old_customers.csv` as the existing customer snapshot.
3. Upload `outputs/syncshield_new_customers.csv` as the incoming dataset.
4. Review the proposed `CREATE`, `UPDATE`, and `UNCHANGED` operations.
5. Open **Run History** to inspect the saved results.

> **Note:** The Pro workspace initially contains a seeded customer record. Uploading the old customer snapshot replaces the baseline used for the preview.

---

## 🧪 Sample Datasets

Three sample datasets are included to demonstrate different validation and synchronization scenarios.

| Dataset | Purpose |
|---|---|
| `syncshield_old_customers.csv` | Existing customer records used as the baseline. |
| `syncshield_new_customers.csv` | Incoming customer records used to demonstrate synchronization changes. |
| `syncshield_invalid_customers.csv` | Invalid records used to test data validation. |

All datasets are located in the `outputs/` directory.

### Expected Preview Results

Comparing the included old and new datasets should produce the following expected counts:

| Operation | Expected Count |
|---|---:|
| `CREATE` | 3 |
| `UPDATE` | 4 |
| `UNCHANGED` | 3 |
| **Total Incoming Records** | **10** |

These results demonstrate how SyncShield identifies potential changes between customer datasets.

---

## 🗄️ Database & Data Integrity

SyncShield uses SQLite to persist workspace and processing information.

### Implementation Highlights

- **Schema definition:** Database structure documented in `schema.sql`.
- **Database migrations:** Ordered, additive migration scripts.
- **Migration tracking:** Applied migrations tracked through `schema_migrations`.
- **Persistent results:** Processing results saved for later review.
- **Workspace isolation:** Workspace-scoped data access.
- **Quota management:** Transactional quota reservation.
- **Idempotent submissions:** Identical repeated submissions replay the saved result when the same idempotency key is reused.
- **Conflict handling:** Reusing that key with different input returns `HTTP 409 Conflict`.

These mechanisms help maintain consistency during repeated submissions and processing operations.

---

## 🧪 Testing & Verification

Run the following checks from the project root.

### 1. Python Unit Tests

```bash
python -m unittest discover -s tests -v
```

### 2. JavaScript Syntax Check

```bash
node --check public/app.js
```

### 3. Python Compilation Check

```bash
python -m py_compile server.py
```

### 4. Local Verification Script

Run in PowerShell:

```powershell
.\scripts\verify-local.ps1 -SkipHealthCheck
```

**Verification tips:**

- Run the commands from the project root.
- Install Node.js if you need to run the JavaScript syntax check.
- If the server is running after code changes, stop it using `Ctrl+C` and restart it to load the updated application and database migrations.

---

## ☁️ Azure Deployment Scaffold

SyncShield includes Terraform infrastructure definitions and a PowerShell deployment script for an Azure App Service setup.

### Included Deployment Components

- Terraform infrastructure definitions under `infra/terraform/`.
- Deployment automation through `scripts/deploy-azure.ps1`.
- Terraform plan review by default.

Provisioning and deployment require the appropriate explicit configuration and the `-Apply` option.

### Current Infrastructure Limitations

- SQLite is used instead of PostgreSQL.
- The deployment scaffold targets a single-instance deployment.
- The architecture is not designed for multi-instance database scaling.
- A production Azure deployment is not included as a completed feature.

The deployment components provide a foundation for further infrastructure work but should not be considered a production-ready hosting solution.

---

## ⚠️ Current Limitations

SyncShield is a **locally hosted prototype intended for demonstration and evaluation**.

The following capabilities are not yet configured:

- Production Azure deployment.
- Email verification.
- Password reset and production account recovery.
- Microsoft sign-in.
- Workspace invitations.
- Payment webhook verification and automatic paid-plan activation.
- A production-ready, scalable database architecture.

### Production Readiness

Subscription and role-management controls are intended for local demonstration, not production administration.

Before enabling real payments, the application requires:

1. Razorpay credential configuration.
2. A publicly reachable webhook endpoint.
3. Webhook signature verification.
4. Reliable payment-state handling.

These limitations are important when evaluating the current implementation and planning future development.

---

## 🔮 Future Enhancements

SyncShield can be extended with additional intelligence, automation, and scalability.

The following are proposed enhancements, not claims about existing functionality.

| Enhancement | Planned Benefit |
|---|---|
| 🤖 **AI-Assisted Anomaly Detection** | Use machine learning to identify unusual customer-data changes. |
| 📈 **Advanced Data Quality Scoring** | Score records based on completeness, consistency, and validity. |
| ⚠️ **Intelligent Change-Risk Analysis** | Flag synchronization previews that require additional review. |
| 🔗 **ERP Connectors** | Introduce controlled integrations with supported ERP platforms. |
| 👥 **Approval Workflows** | Require human approval before executing proposed changes. |
| 🗄️ **Scalable Database Architecture** | Migrate to PostgreSQL and support a suitable multi-instance deployment architecture. |
| 📡 **Enhanced Observability** | Introduce structured logs, monitoring, and operational alerts. |

These improvements represent opportunities to evolve SyncShield from a local demonstration into a more capable synchronization-management platform.

---

## 💡 Why SyncShield?

**Because moving data is not enough. You need to understand what changes before you make them.**

SyncShield introduces a preview-first approach to customer-data synchronization, helping teams:

- Detect invalid records before processing.
- Understand proposed changes between datasets.
- Review processing history.
- Enforce workspace-level usage controls.
- Reduce uncertainty before a future ERP integration.

Rather than treating synchronization as a blind transfer of data, SyncShield makes the proposed outcome visible and reviewable.

### Our Approach

**VALIDATE → COMPARE → PREVIEW → REVIEW**

Synchronization with a live ERP system is outside the current prototype's implemented scope.

---

## 👥 Team & Project Information

| Detail | Information |
|---|---|
| **Project Name** | SyncShield |
| **Use Case** | UC-02 — ERP Synchronization Preview |
| **Category** | SaaS / Enterprise Software |
| **Primary Focus** | Customer-data validation and synchronization previews |
| **Current Status** | Locally hosted prototype |

**Project Objective:** Develop a prototype for safer and more transparent ERP data synchronization through customer-data validation, change previews, workspace controls, and processing-history review.

---

<div align="center">

## 🛡️ SyncShield

### See the change before you make the change.

**Validate first. Preview changes. Review the results. Synchronize only when authorized.**

*Making ERP synchronization more transparent, controlled, and reliable.*

</div>

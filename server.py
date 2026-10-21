"""SyncShield local SaaS prototype. Run with: python server.py"""
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qs
import hashlib, hmac, json, os, re, secrets, socket, sqlite3, time, calendar, ipaddress
import base64, urllib.error, urllib.request

ROOT = Path(__file__).parent
DB = Path(os.environ.get("SYNCSHIELD_DB", str(ROOT / "syncshield.db")))
ADMIN_DB = Path(os.environ.get("SYNCSHIELD_ADMIN_DB", str(ROOT / "platform_admin.db")))
PORT = int(os.environ.get("PORT", "8000"))
SESSION_SECONDS = 7 * 24 * 60 * 60
PLAN_LIMITS = {"Basic": 3, "Pro": 20}
TEAM_JOB_TITLES = ("Developer", "Software Developer", "Tester / QA", "Designer", "Code Reviewer", "System Architect",
                   "Cloud/DevOps Engineer", "Security Engineer", "Data/Integration Engineer", "Platform Administrator",
                   "Product Manager", "Data Analyst", "DevOps Engineer", "Project Manager", "Other")

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS workspaces (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, plan TEXT NOT NULL DEFAULT 'Basic', subscription_status TEXT NOT NULL DEFAULT 'ACTIVE',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 full_name TEXT NOT NULL, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
 password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'Operator', job_title TEXT NOT NULL DEFAULT 'Other',
 account_status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (account_status IN ('ACTIVE','SUSPENDED')),
 onboarding_complete INTEGER NOT NULL DEFAULT 0,
 preferred_format TEXT NOT NULL DEFAULT 'CSV', intended_use TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 expires_at INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS sessions_expiry_idx ON sessions(expires_at);
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id), user_id TEXT NOT NULL REFERENCES users(id),
 kind TEXT NOT NULL, status TEXT NOT NULL, input_rows INTEGER NOT NULL, valid_rows INTEGER NOT NULL,
 invalid_rows INTEGER NOT NULL, issue_count INTEGER NOT NULL DEFAULT 0,
 created_count INTEGER NOT NULL DEFAULT 0, updated_count INTEGER NOT NULL DEFAULT 0,
 unchanged_count INTEGER NOT NULL DEFAULT 0, idempotency_key TEXT, input_fingerprint TEXT,
 result_json TEXT NOT NULL, error_code TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS runs_workspace_date_idx ON runs(workspace_id,created_at);
CREATE TABLE IF NOT EXISTS daily_usage (
 workspace_id TEXT NOT NULL REFERENCES workspaces(id), usage_date TEXT NOT NULL,
 usage_count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(workspace_id,usage_date)
);
CREATE TABLE IF NOT EXISTS audit_events (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id), user_id TEXT REFERENCES users(id),
 event_type TEXT NOT NULL, details_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS audit_workspace_date_idx ON audit_events(workspace_id,created_at);
CREATE TABLE IF NOT EXISTS payment_intents (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id), user_id TEXT NOT NULL REFERENCES users(id),
 gateway TEXT NOT NULL, plan TEXT NOT NULL, months INTEGER NOT NULL, amount_minor INTEGER NOT NULL,
 currency TEXT NOT NULL DEFAULT 'INR', status TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS feedback (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id), user_id TEXT NOT NULL REFERENCES users(id),
 category TEXT NOT NULL, message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'NEW',
 rating INTEGER CHECK (rating IS NULL OR rating BETWEEN 1 AND 5), assigned_to TEXT,
 resolution_notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS workspace_baselines (
 workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL REFERENCES users(id), rows_json TEXT NOT NULL,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

def apply_migrations(con):
    """Apply ordered, additive SQL migrations once; safe on both fresh and legacy DBs."""
    con.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    for migration in sorted((ROOT / "migrations").glob("*.sql")):
        version = migration.stem
        if con.execute("SELECT 1 FROM schema_migrations WHERE version=?", (version,)).fetchone():
            continue
        sql = migration.read_text(encoding="utf-8")
        safe_version = version.replace("'", "''")
        con.executescript("BEGIN IMMEDIATE;\n" + sql + f"\nINSERT INTO schema_migrations(version) VALUES('{safe_version}');\nCOMMIT;")


@contextmanager
def db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=15000")
    try:
        yield con
    except Exception:
        if con.in_transaction: con.rollback()
        raise
    else:
        if con.in_transaction: con.commit()
    finally:
        con.close()

@contextmanager
def platform_admin_db():
    """Keep platform-administrator credentials in a dedicated SQLite file."""
    ADMIN_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ADMIN_DB, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=15000")
    con.execute("""CREATE TABLE IF NOT EXISTS platform_admins (
        id TEXT PRIMARY KEY, full_name TEXT NOT NULL, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
        company_id TEXT NOT NULL, organization_name TEXT NOT NULL, professional_role TEXT NOT NULL,
        password_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING'
          CHECK(status IN ('PENDING','ACTIVE','REJECTED','SUSPENDED')),
        submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, approved_at TEXT, approved_by TEXT
    )""")
    try:
        yield con
    except Exception:
        if con.in_transaction: con.rollback()
        raise
    else:
        if con.in_transaction: con.commit()
    finally:
        con.close()

with db() as con:
    con.executescript(SCHEMA)
    workspace_cols = {r["name"] for r in con.execute("PRAGMA table_info(workspaces)")}
    if "subscription_status" not in workspace_cols:
        con.execute("ALTER TABLE workspaces ADD COLUMN subscription_status TEXT NOT NULL DEFAULT 'ACTIVE'")
    session_cols = {r["name"] for r in con.execute("PRAGMA table_info(sessions)")}
    if "last_seen_at" not in session_cols:
        con.execute("ALTER TABLE sessions ADD COLUMN last_seen_at TEXT")
        con.execute("UPDATE sessions SET last_seen_at=created_at WHERE last_seen_at IS NULL")
    user_cols = {r["name"] for r in con.execute("PRAGMA table_info(users)")}
    if "job_title" not in user_cols:
        con.execute("ALTER TABLE users ADD COLUMN job_title TEXT NOT NULL DEFAULT 'Other'")
    if "account_status" not in user_cols:
        con.execute("ALTER TABLE users ADD COLUMN account_status TEXT NOT NULL DEFAULT 'ACTIVE'")
    feedback_cols = {r["name"] for r in con.execute("PRAGMA table_info(feedback)")}
    for name, declaration in (("rating", "INTEGER"), ("assigned_to", "TEXT"), ("resolution_notes", "TEXT NOT NULL DEFAULT ''")):
        if name not in feedback_cols:
            con.execute(f"ALTER TABLE feedback ADD COLUMN {name} {declaration}")
    # Additive migration for a database created by the earlier prototype version.
    cols = {r["name"] for r in con.execute("PRAGMA table_info(runs)")}
    for name, declaration in (("issue_count", "INTEGER NOT NULL DEFAULT 0"),
                               ("idempotency_key", "TEXT"),
                               ("input_fingerprint", "TEXT"),
                               ("error_code", "TEXT")):
        if name not in cols:
            con.execute(f"ALTER TABLE runs ADD COLUMN {name} {declaration}")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS runs_workspace_idempotency_idx ON runs(workspace_id,idempotency_key) WHERE idempotency_key IS NOT NULL")
    con.execute("INSERT OR IGNORE INTO daily_usage(workspace_id,usage_date,usage_count) SELECT workspace_id,date(created_at),COUNT(*) FROM runs GROUP BY workspace_id,date(created_at)")
    apply_migrations(con)

def bootstrap_platform_admin():
    email = os.environ.get("SYNCSHIELD_ADMIN_EMAIL", "").strip().lower()
    password = os.environ.get("SYNCSHIELD_ADMIN_PASSWORD", "")
    name = os.environ.get("SYNCSHIELD_ADMIN_NAME", "Platform Administrator").strip()
    organization = os.environ.get("SYNCSHIELD_ADMIN_ORGANIZATION", "SyncShield Platform").strip()
    job_title = os.environ.get("SYNCSHIELD_ADMIN_JOB_TITLE", "Platform Administrator").strip()
    if not email and not password:
        return
    if (not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(password) < 12
            or not name or len(name) > 100 or not organization or len(organization) > 100
            or job_title not in TEAM_JOB_TITLES):
        raise RuntimeError("Set valid admin name, email, organization, job title and a password of at least 12 characters.")
    with db() as con:
        row = con.execute("SELECT id,workspace_id FROM users WHERE email=? COLLATE NOCASE", (email,)).fetchone()
        if row:
            con.execute("UPDATE users SET full_name=?,job_title=?,role='Admin',password_hash=?,onboarding_complete=1 WHERE id=?",
                        (name,job_title,password_hash(password),row["id"]))
            con.execute("UPDATE workspaces SET name=? WHERE id=?",(organization,row["workspace_id"]))
        else:
            workspace_id, user_id = secrets.token_hex(16), secrets.token_hex(16)
            con.execute("INSERT INTO workspaces(id,name,plan) VALUES(?,?,'Basic')", (workspace_id, organization))
            con.execute("INSERT INTO users(id,workspace_id,full_name,email,password_hash,role,job_title,onboarding_complete) VALUES(?,?,?,?,?,'Admin',?,1)",
                        (user_id,workspace_id,name,email,password_hash(password),job_title))
            row=con.execute("SELECT id,workspace_id FROM users WHERE email=? COLLATE NOCASE",(email,)).fetchone()
    with db() as con:
        workspace=con.execute("SELECT name FROM workspaces WHERE id=?",(row["workspace_id"],)).fetchone()[0]
    with platform_admin_db() as con:
        con.execute("""INSERT INTO platform_admins(id,full_name,email,company_id,organization_name,professional_role,password_hash,status,approved_at)
            VALUES(?,?,?,?,?,?,?,'ACTIVE',CURRENT_TIMESTAMP)
            ON CONFLICT(email) DO UPDATE SET id=excluded.id,full_name=excluded.full_name,company_id=excluded.company_id,
            organization_name=excluded.organization_name,professional_role=excluded.professional_role,password_hash=excluded.password_hash,status='ACTIVE'""",
            (row["id"],name,email,row["workspace_id"],workspace,job_title,password_hash(password)))

def sync_legacy_platform_admins():
    """Move pre-existing trusted admin credentials into the dedicated auth database."""
    with db() as con:
        admins=con.execute("""SELECT u.id,u.full_name,u.email,u.password_hash,u.job_title,w.id workspace_id,w.name organization
            FROM users u JOIN workspaces w ON w.id=u.workspace_id WHERE u.role='Admin' AND u.account_status='ACTIVE'""").fetchall()
    if not admins: return
    with platform_admin_db() as con:
        for admin in admins:
            con.execute("""INSERT INTO platform_admins(id,full_name,email,company_id,organization_name,professional_role,password_hash,status,approved_at)
                VALUES(?,?,?,?,?,?,?,'ACTIVE',CURRENT_TIMESTAMP)
                ON CONFLICT(email) DO UPDATE SET id=excluded.id,full_name=excluded.full_name,company_id=excluded.company_id,
                organization_name=excluded.organization_name,professional_role=excluded.professional_role,password_hash=excluded.password_hash,status='ACTIVE'
                WHERE platform_admins.status='PENDING'""",
                (admin["id"],admin["full_name"],admin["email"],admin["workspace_id"],admin["organization"],admin["job_title"],admin["password_hash"]))
    with db() as con:
        for admin in admins:
            con.execute("UPDATE users SET password_hash=? WHERE id=?",(password_hash(secrets.token_urlsafe(40)),admin["id"]))

def utc_day():
    return datetime.now(timezone.utc).date().isoformat()

def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"pbkdf2_sha256${salt.hex()}${digest.hex()}"

def password_ok(password, encoded):
    try:
        algorithm, salt, _ = encoded.split("$")
        return algorithm == "pbkdf2_sha256" and hmac.compare_digest(password_hash(password, bytes.fromhex(salt)), encoded)
    except (ValueError, TypeError):
        return False

bootstrap_platform_admin()
sync_legacy_platform_admins()

def public_user(row):
    return {"id": row["id"], "name": row["full_name"], "email": row["email"],
            "role": row["role"], "jobTitle": row["job_title"], "workspace": row["workspace_name"], "workspaceId": row["workspace_id"],
            "plan": row["plan"], "subscriptionStatus": row["subscription_status"], "onboardingComplete": bool(row["onboarding_complete"]),
            "preferredFormat": row["preferred_format"], "intendedUse": row["intended_use"],
            "subscriptionStartedAt": row["subscription_started_at"], "subscriptionExpiresAt": row["subscription_expires_at"]}

def validate_rows(rows):
    seen, issues, valid = set(), [], 0
    for i, row in enumerate(rows, 1):
        row_issues = []
        if not isinstance(row, dict):
            row_issues.append({"row": i, "record_id": None, "rule": "R1–R4", "field": "row", "message": "Each row must be an object."})
        else:
            rid, name, version, status = (row.get("record_id"), row.get("name"), row.get("version"), row.get("status"))
            if not isinstance(rid, str) or not rid or len(rid) > 20 or rid in seen:
                row_issues.append({"row": i, "record_id": rid if isinstance(rid, str) else None, "rule": "R1", "field": "record_id", "message": "ID must be unique, nonempty and at most 20 characters."})
            if isinstance(rid, str): seen.add(rid)
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
                row_issues.append({"row": i, "record_id": rid if isinstance(rid, str) else None, "rule": "R2", "field": "name", "message": "Name must be 1–40 characters after trimming."})
            if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= 100:
                row_issues.append({"row": i, "record_id": rid if isinstance(rid, str) else None, "rule": "R3", "field": "version", "message": "Version must be an integer from 1 to 100."})
            if status not in ("ACTIVE", "INACTIVE"):
                row_issues.append({"row": i, "record_id": rid if isinstance(rid, str) else None, "rule": "R4", "field": "status", "message": "Status must be ACTIVE or INACTIVE."})
        # Stable machine-readable codes make validation reports actionable and filterable.
        for issue in row_issues:
            issue["code"] = "VAL_ROW_FORMAT" if issue["rule"] == "R1–R4" else f"VAL_{issue['rule']}"
        issues.extend(row_issues)
        if not row_issues: valid += 1
    return {"valid_rows": valid, "invalid_rows": len(rows) - valid, "issues": issues, "rows": rows}

def fingerprint(rows):
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def audit_event(workspace_id, user_id, event_type, details=None):
    with db() as con:
        con.execute("INSERT INTO audit_events(id,workspace_id,user_id,event_type,details_json) VALUES(?,?,?,?,?)",
                    (secrets.token_hex(12), workspace_id, user_id, event_type,
                     json.dumps(details or {}, ensure_ascii=False)))

def run_public(row, include_result=False):
    obj = {k: row[k] for k in ("id", "workspace_id", "status", "kind", "input_rows", "valid_rows", "invalid_rows", "issue_count", "created_count", "updated_count", "unchanged_count", "created_at", "error_code")}
    if include_result: obj["result"] = json.loads(row["result_json"])
    return obj

class Handler(BaseHTTPRequestHandler):
    server_version = "SyncShieldPrototype/1.1"

    def log_message(self, fmt, *args):
        # Keep request bodies, emails, and cookies out of access logs.
        super().log_message(fmt, *args)

    def send_json(self, code, payload, headers=None):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (headers or {}).items(): self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def body(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > 1_000_000: return None
            payload = json.loads(self.rfile.read(size) or b"{}")
            return payload if isinstance(payload, dict) else None
        except (ValueError, json.JSONDecodeError):
            return None

    def session(self):
        token = ""
        for part in self.headers.get("Cookie", "").split(";"):
            key, _, value = part.strip().partition("=")
            if key == "ss_session": token = value
        if not token: return None
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with db() as con:
            user = con.execute("""SELECT u.*, w.name workspace_name, w.plan, w.subscription_status,w.subscription_started_at,w.subscription_expires_at FROM sessions s
                JOIN users u ON u.id=s.user_id JOIN workspaces w ON w.id=u.workspace_id
                WHERE s.token_hash=? AND s.expires_at>?""", (token_hash, int(time.time()))).fetchone()
            if user:
                expiry=user["subscription_expires_at"]
                if user["plan"]=="Pro" and expiry:
                    try:
                        expiry_dt=datetime.fromisoformat(expiry.replace("Z","+00:00"))
                        if expiry_dt.tzinfo is None:expiry_dt=expiry_dt.replace(tzinfo=timezone.utc)
                        if expiry_dt<=datetime.now(timezone.utc):
                            con.execute("UPDATE workspaces SET plan='Basic' WHERE id=? AND plan='Pro'",(user["workspace_id"],))
                            con.execute("INSERT INTO audit_events(id,workspace_id,user_id,event_type,details_json) VALUES(?,?,?,?,?)",
                                (secrets.token_hex(12),user["workspace_id"],user["id"],"subscription.expired",json.dumps({"expired_at":expiry})))
                            user=con.execute("""SELECT u.*, w.name workspace_name, w.plan, w.subscription_status,w.subscription_started_at,w.subscription_expires_at FROM users u JOIN workspaces w ON w.id=u.workspace_id WHERE u.id=?""",(user["id"],)).fetchone()
                    except ValueError:
                        pass
                con.execute("UPDATE sessions SET last_seen_at=CURRENT_TIMESTAMP WHERE token_hash=?", (token_hash,))
        return (user, token_hash) if user else None

    def set_session(self, user_id):
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with db() as con:
            con.execute("DELETE FROM sessions WHERE expires_at<=?", (int(time.time()),))
            con.execute("INSERT INTO sessions(token_hash,user_id,expires_at,last_seen_at) VALUES(?,?,?,CURRENT_TIMESTAMP)", (token_hash, user_id, int(time.time()) + SESSION_SECONDS))
        return {"Set-Cookie": f"ss_session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_SECONDS}"}

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        if path.startswith("/api/") and any(k.lower() in ("tenant", "tenant_id", "workspace", "workspace_id") for k in query):
            return self.send_json(400, {"error": "Workspace identity comes from your signed-in session; tenant overrides are not accepted."})
        auth = self.session() if path.startswith("/api/") else None
        if path == "/api/me": return self.send_json(200, {"user": public_user(auth[0])} if auth else {"user": None})
        if path in ("/health", "/api/health"): return self.send_json(200, {"status": "ok", "service": "SyncShield"})
        if path == "/api/dashboard": return self.dashboard(auth)
        if path == "/api/usage": return self.usage(auth)
        if path == "/api/payments/status": return self.payment_status(auth, query)
        if path == "/api/payments/config":
            addr = ipaddress.ip_address(self.client_address[0].split("%", 1)[0])
            if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped: addr = addr.ipv4_mapped
            demo = os.environ.get("SYNCSHIELD_DEMO_PAYMENTS", "1") == "1" and addr.is_loopback
            hosted = bool(os.environ.get("RAZORPAY_KEY_ID") and os.environ.get("RAZORPAY_KEY_SECRET"))
            return self.send_json(200, {"mode": "demo" if demo else "hosted" if hosted else "unconfigured"})
        if path == "/api/runs": return self.list_runs(auth)
        if path == "/api/admin/diagnostics": return self.admin_diagnostics(auth)
        if path == "/api/admin/overview": return self.admin_overview(auth)
        if path == "/api/platform-admin/admin-requests": return self.platform_admin_requests(auth)
        if path == "/api/platform-admin/overview": return self.platform_overview(auth, query)
        if path == "/api/platform-admin/users": return self.platform_users(auth, query)
        if path.startswith("/api/platform-admin/users/"): return self.platform_user_profile(auth, path.rsplit("/", 1)[-1])
        if path == "/api/platform-admin/feedback": return self.platform_feedback(auth)
        if path == "/api/ping": return self.send_json(200, {"ok": True}) if auth else self.send_json(401, {"error": "Please sign in."})
        if path == "/api/baseline": return self.get_baseline(auth)
        if path.startswith("/api/runs/"): return self.get_run(auth, path.rsplit("/", 1)[-1])
        if path.startswith("/api/"): return self.send_json(404, {"error": "Not found"})
        public = (ROOT / "public").resolve()
        target = (public / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
        if public not in target.parents and target != public: return self.send_error(404)
        if not target.is_file(): target = public / "index.html"
        data = target.read_bytes()
        mime = {".html": "text/html", ".css": "text/css", ".js": "application/javascript"}.get(target.suffix, "application/octet-stream")
        self.send_response(200); self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data))); self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers(); self.wfile.write(data)

    def do_POST(self):
        path = urlparse(self.path).path
        data = self.body()
        if data is None: return self.send_json(400, {"error": "Request must be a JSON object under 1 MB."})
        routes = {"/api/signup": self.signup, "/api/login": self.login, "/api/logout": self.logout,
                  "/api/admin/login": self.admin_login, "/api/feedback": self.submit_feedback,
                  "/api/admin/accounts": self.create_admin_account,
                  "/api/admin/registration": self.register_platform_admin,
                  "/api/platform-admin/user-action": self.platform_user_action,
                  "/api/platform-admin/feedback-action": self.platform_feedback_action,
                  "/api/platform-admin/admin-request-action": self.platform_admin_request_action,
                  "/api/onboarding": self.onboarding, "/api/validate": self.validate,
                  "/api/process": self.process, "/api/plan": self.set_plan,
                  "/api/demo/role": self.demo_role, "/api/payments/checkout": self.create_checkout,
                  "/api/payments/demo-complete": self.demo_complete,
                  "/api/baseline": self.save_baseline}
        handler = routes.get(path)
        return handler(data) if handler else self.send_json(404, {"error": "Not found"})

    def signup(self, data):
        name = str(data.get("name", "")).strip(); email = str(data.get("email", "")).strip().lower()
        workspace = str(data.get("company", "")).strip(); password = data.get("password", "")
        job_title = data.get("job_title", "Other")
        if not name or len(name) > 100: return self.send_json(400, {"error": "Enter your name (up to 100 characters)."})
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254: return self.send_json(400, {"error": "Enter a valid work email."})
        if not isinstance(password, str) or len(password) < 6 or len(password) > 128: return self.send_json(400, {"error": "Password must be 6–128 characters."})
        if not workspace or len(workspace) > 100: return self.send_json(400, {"error": "Enter a workspace name (up to 100 characters)."})
        if not isinstance(job_title, str) or job_title not in TEAM_JOB_TITLES: return self.send_json(400, {"error": "Choose a team role from the list."})
        workspace_id, user_id = secrets.token_hex(16), secrets.token_hex(16)
        try:
            with db() as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute("INSERT INTO workspaces(id,name) VALUES(?,?)", (workspace_id, workspace))
                con.execute("INSERT INTO users(id,workspace_id,full_name,email,password_hash,job_title) VALUES(?,?,?,?,?,?)", (user_id, workspace_id, name, email, password_hash(password), job_title))
                con.commit()
        except sqlite3.IntegrityError:
            return self.send_json(409, {"error": "An account with this email already exists."})
        return self.send_json(201, {"userId": user_id, "next": "/onboarding"}, self.set_session(user_id))

    def login(self, data):
        email = str(data.get("email", "")).strip().lower(); password = data.get("password", "")
        with db() as con: user = con.execute("SELECT * FROM users WHERE email=? COLLATE NOCASE", (email,)).fetchone()
        if not user or user["role"] == "Admin" or not isinstance(password, str) or not password_ok(password, user["password_hash"]):
            return self.send_json(401, {"error": "Email or password is incorrect."})
        if user["account_status"] == "SUSPENDED":
            return self.send_json(403, {"error": "This account is suspended. Contact SyncShield support."})
        audit_event(user["workspace_id"], user["id"], "user.login", {})
        next_page = "/platform-admin/overview" if user["role"] == "Admin" else ("/dashboard" if user["onboarding_complete"] else "/onboarding")
        return self.send_json(200, {"next": next_page}, self.set_session(user["id"]))

    def admin_login(self, data):
        email = str(data.get("email", "")).strip().lower()
        password = data.get("password", "")
        if not isinstance(password, str):
            return self.send_json(401, {"error": "Administrator credentials are incorrect or not authorized."})
        with platform_admin_db() as acon:
            admin_credential = acon.execute("SELECT * FROM platform_admins WHERE email=? COLLATE NOCASE", (email,)).fetchone()
        if not admin_credential or admin_credential["status"] != "ACTIVE" or not password_ok(password, admin_credential["password_hash"]):
            return self.send_json(403, {"error": "Administrator credentials are incorrect or not authorized."})
        with db() as con:
            user = con.execute("SELECT * FROM users WHERE id=? AND role='Admin'", (admin_credential["id"],)).fetchone()
        if not user or user["account_status"] == "SUSPENDED":
            return self.send_json(403, {"error": "This administrator account is suspended."})
        audit_event(user["workspace_id"], user["id"], "admin.login", {})
        return self.send_json(200, {"next": "/platform-admin/overview"}, self.set_session(user["id"]))

    def register_platform_admin(self, data):
        required = {"name", "email", "company_id", "organization", "job_title", "password"}
        if set(data) != required:
            return self.send_json(400, {"error": "Complete all administrator registration fields."})
        name, email = data["name"], data["email"]
        company_id, organization, job_title, password = data["company_id"], data["organization"], data["job_title"], data["password"]
        if not isinstance(name,str) or not name.strip() or len(name.strip())>100:
            return self.send_json(400,{"error":"Enter a name up to 100 characters."})
        if not isinstance(email,str) or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",email.strip()) or len(email.strip())>254:
            return self.send_json(400,{"error":"Enter a valid email address."})
        if not isinstance(company_id,str) or not company_id.strip() or len(company_id.strip())>80:
            return self.send_json(400,{"error":"Enter a company ID up to 80 characters."})
        if not isinstance(organization,str) or not organization.strip() or len(organization.strip())>100:
            return self.send_json(400,{"error":"Enter a company or organization name."})
        if not isinstance(job_title,str) or job_title not in TEAM_JOB_TITLES:
            return self.send_json(400,{"error":"Choose a professional role from the list."})
        if not isinstance(password,str) or len(password)<12 or len(password)>128:
            return self.send_json(400,{"error":"Password must be 12–128 characters."})
        email=email.strip().lower()
        with db() as con:
            if con.execute("SELECT 1 FROM users WHERE email=? COLLATE NOCASE",(email,)).fetchone():
                return self.send_json(409,{"error":"An account already uses this email."})
        request_id=secrets.token_hex(16)
        try:
            with platform_admin_db() as con:
                changed=con.execute("""INSERT INTO platform_admins(id,full_name,email,company_id,organization_name,professional_role,password_hash)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(email) DO UPDATE SET id=excluded.id,full_name=excluded.full_name,
                    company_id=excluded.company_id,organization_name=excluded.organization_name,professional_role=excluded.professional_role,
                    password_hash=excluded.password_hash,status='PENDING',submitted_at=CURRENT_TIMESTAMP,approved_at=NULL,approved_by=NULL
                    WHERE platform_admins.status='REJECTED'""",
                    (request_id,name.strip(),email,company_id.strip(),organization.strip(),job_title,password_hash(password))).rowcount
                if not changed:return self.send_json(409,{"error":"An administrator account or request already uses this email."})
        except sqlite3.IntegrityError:
            return self.send_json(409,{"error":"An administrator account or request already uses this email."})
        return self.send_json(201,{"status":"PENDING","request_id":request_id,"message":"Your administrator access request has been sent for approval."})

    def create_admin_account(self, data):
        auth = self.session()
        if not auth: return self.send_json(401, {"error": "Administrator sign-in required."})
        actor = auth[0]
        if actor["role"] != "Admin":
            audit_event(actor["workspace_id"], actor["id"], "access.denied.admin_account_create", {"role":actor["role"]})
            return self.send_json(403, {"error": "Only a platform administrator can create administrator accounts."})
        if set(data) != {"name", "email", "password"}:
            return self.send_json(400, {"error": "Provide a name, email and password."})
        name = data.get("name"); email = data.get("email"); password = data.get("password")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
            return self.send_json(400, {"error": "Enter a name (up to 100 characters)."})
        if not isinstance(email, str) or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email.strip()) or len(email.strip()) > 254:
            return self.send_json(400, {"error": "Enter a valid email address."})
        if not isinstance(password, str) or len(password) < 12 or len(password) > 128:
            return self.send_json(400, {"error": "Password must be 12–128 characters."})
        user_id = secrets.token_hex(16)
        email = email.strip().lower()
        try:
            with platform_admin_db() as acon:
                acon.execute("INSERT INTO platform_admins(id,full_name,email,company_id,organization_name,professional_role,password_hash,status,approved_at,approved_by) VALUES(?,?,?,?,?,?,?,'ACTIVE',CURRENT_TIMESTAMP,?)",
                    (user_id,name.strip(),email,actor["workspace_id"],actor["workspace_name"],"Platform Administrator",password_hash(password),actor["id"]))
            with db() as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute("""INSERT INTO users(id,workspace_id,full_name,email,password_hash,role,onboarding_complete)
                    VALUES(?,?,?,?,?,'Admin',1)""",
                    (user_id, actor["workspace_id"], name.strip(), email, password_hash(secrets.token_urlsafe(32))))
                con.execute("INSERT INTO audit_events(id,workspace_id,user_id,event_type,details_json) VALUES(?,?,?,?,?)",
                    (secrets.token_hex(16), actor["workspace_id"], actor["id"], "admin.account_created",
                     json.dumps({"email":email}, separators=(",", ":"))))
        except sqlite3.IntegrityError:
            with platform_admin_db() as acon: acon.execute("DELETE FROM platform_admins WHERE id=?",(user_id,))
            return self.send_json(409, {"error": "An account with this email already exists."})
        return self.send_json(201, {"id":user_id, "name":name.strip(), "email":email, "role":"Admin"})

    def logout(self, _data):
        auth = self.session()
        if auth:
            with db() as con: con.execute("DELETE FROM sessions WHERE token_hash=?", (auth[1],))
        return self.send_json(200, {"ok": True}, {"Set-Cookie": "ss_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"})

    def onboarding(self, data):
        auth = self.session()
        if not auth: return self.send_json(401, {"error": "Please sign in."})
        if auth[0]["role"] == "Admin": return self.send_json(403, {"error": "Platform administrators do not use workspace onboarding."})
        name = str(data.get("workspace", "")).strip(); fmt = data.get("format", "CSV")
        use = str(data.get("use", "")).strip(); plan = data.get("plan", "Basic")
        if not name or len(name) > 100 or fmt not in ("CSV", "JSON") or len(use) > 300 or plan not in ("Basic", "Pro"):
            return self.send_json(400, {"error": "Check the workspace name and setup preferences."})
        if auth[0]["onboarding_complete"]:
            return self.send_json(409, {"error": "Workspace setup is already complete. Manage plan changes from SaaS Administration."})
        with db() as con:
            # Pro is only a requested plan here. Entitlements are granted after
            # the payment provider confirms the full payment server-side.
            con.execute("UPDATE workspaces SET name=? WHERE id=?", (name, auth[0]["workspace_id"]))
            con.execute("UPDATE users SET preferred_format=?,intended_use=? WHERE id=?", (fmt, use, auth[0]["id"]))
            if plan == "Basic":
                con.execute("UPDATE workspaces SET plan='Basic',subscription_started_at=NULL,subscription_expires_at=NULL WHERE id=?",(auth[0]["workspace_id"],))
                con.execute("UPDATE users SET onboarding_complete=1 WHERE id=?",(auth[0]["id"],))
        if plan == "Pro":
            months=data.get("months",1)
            if isinstance(months,bool) or months not in (1,3,12):return self.send_json(400,{"error":"Choose a Pro term of 1, 3 or 12 months."})
            return self.send_json(200, {"ok": True, "next": f"/checkout?mode=onboarding&months={months}"})
        audit_event(auth[0]["workspace_id"],auth[0]["id"],"workspace.onboarding_completed",{"plan":"Basic","format":fmt})
        return self.send_json(200, {"ok": True, "next": "/dashboard"})

    def set_plan(self, data):
        """Demo-only plan selector; workspace-scoped and available to operators/admins."""
        auth = self.session()
        if not auth: return self.send_json(401, {"error": "Please sign in."})
        user = auth[0]
        if user["role"] == "Viewer": return self.send_json(403, {"error": "Viewer access is read-only."})
        if user["subscription_status"] != "ACTIVE": return self.send_json(403, {"error": "This workspace subscription is suspended."})
        if set(data) != {"plan"} or data.get("plan") != "Basic":
            return self.send_json(400, {"error": "Pro access is enabled only after the payment provider confirms a Pro subscription."})
        with db() as con:
            con.execute("UPDATE workspaces SET plan='Basic',subscription_started_at=NULL,subscription_expires_at=NULL WHERE id=?", (user["workspace_id"],))
            updated = con.execute("""SELECT u.*, w.name workspace_name, w.plan, w.subscription_status,w.subscription_started_at,w.subscription_expires_at
                FROM users u JOIN workspaces w ON w.id=u.workspace_id WHERE u.id=?""", (user["id"],)).fetchone()
        audit_event(user["workspace_id"], user["id"], "plan.demo_changed", {"from":user["plan"],"to":data["plan"]})
        return self.send_json(200, {"user": public_user(updated), "demo_plan": True})

    def demo_role(self, data):
        auth = self.session()
        if not auth: return self.send_json(401, {"error": "Please sign in."})
        if auth[0]["role"] == "Admin": return self.send_json(403, {"error": "Platform administrator roles are managed by server configuration."})
        if set(data) != {"role"} or data.get("role") not in ("Operator", "Viewer"):
            return self.send_json(400, {"error": "Choose Operator or Viewer."})
        old_role, user = auth[0]["role"], auth[0]
        with db() as con:
            con.execute("UPDATE users SET role=? WHERE id=? AND workspace_id=?",
                        (data["role"], user["id"], user["workspace_id"]))
            updated = con.execute("""SELECT u.*, w.name workspace_name, w.plan, w.subscription_status,w.subscription_started_at,w.subscription_expires_at
                FROM users u JOIN workspaces w ON w.id=u.workspace_id WHERE u.id=?""", (user["id"],)).fetchone()
        audit_event(user["workspace_id"], user["id"], "demo.role_switched", {"from":old_role,"to":data["role"]})
        return self.send_json(200, {"user": public_user(updated), "demo_mode": True})

    def submit_feedback(self, data):
        auth = self.session()
        if not auth: return self.send_json(401, {"error": "Sign in before sending feedback."})
        if set(data) - {"category", "message", "rating"} or not {"category", "message"}.issubset(data):
            return self.send_json(400, {"error": "Choose a feedback category, provide a message, and optionally add a rating."})
        category, message = data.get("category"), data.get("message")
        rating = data.get("rating")
        if category not in ("Idea", "Issue", "Bug report", "Complaint", "Other") or not isinstance(message, str) or not message.strip() or len(message.strip()) > 2000 or (rating is not None and (isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5)):
            return self.send_json(400, {"error": "Feedback must be 1–2,000 characters with a valid category."})
        user, feedback_id = auth[0], secrets.token_hex(12)
        with db() as con:
            con.execute("INSERT INTO feedback(id,workspace_id,user_id,category,message,rating) VALUES(?,?,?,?,?,?)",
                        (feedback_id, user["workspace_id"], user["id"], category, message.strip(), rating))
        audit_event(user["workspace_id"], user["id"], "feedback.submitted", {"feedback_id": feedback_id, "category": category})
        return self.send_json(201, {"id": feedback_id, "status": "NEW", "message": "Thanks — your feedback has been sent to the SyncShield team."})

    def create_checkout(self, data):
        auth = self.session()
        if not auth: return self.send_json(401, {"error": "Please sign in."})
        if auth[0]["role"] == "Viewer":
            audit_event(auth[0]["workspace_id"],auth[0]["id"],"access.denied.payment",{"role":"Viewer"})
            return self.send_json(403, {"error": "Viewer access is read-only; ask a workspace operator to manage billing."})
        if auth[0]["subscription_status"] != "ACTIVE":
            audit_event(auth[0]["workspace_id"],auth[0]["id"],"access.denied.payment",{"subscription_status":"SUSPENDED"})
            return self.send_json(403, {"error": "This workspace subscription is suspended."})
        if set(data) - {"months", "billing_country", "onboarding", "phone"} or "months" not in data or isinstance(data.get("months"), bool) or data.get("months") not in (1, 3, 12):
            return self.send_json(400, {"error": "Choose an advance period of 1, 3 or 12 months."})
        user = auth[0]; months = data["months"]
        onboarding = data.get("onboarding", False)
        country = data.get("billing_country", "India")
        phone=data.get("phone","")
        if not isinstance(onboarding,bool) or not isinstance(country,str) or not country.strip() or len(country)>80 or any(ord(char)<32 for char in country) or not isinstance(phone,str) or len(phone)>30 or (phone.strip() and not re.fullmatch(r"\+?[0-9][0-9 .()\-]{6,28}",phone.strip())):
            return self.send_json(400,{"error":"Check the billing country and optional phone number."})
        if onboarding and user["onboarding_complete"]:
            return self.send_json(409,{"error":"Workspace setup is already complete."})
        if not onboarding and not user["onboarding_complete"]:
            return self.send_json(409,{"error":"Finish workspace setup before purchasing a plan."})
        key_id, key_secret = os.environ.get("RAZORPAY_KEY_ID"), os.environ.get("RAZORPAY_KEY_SECRET")
        if not key_id or not key_secret:
            return self.send_json(503, {"error": "Secure payment checkout is not configured. Add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET on the server before accepting payments."})
        amount_minor = 99900 * months
        reference_id=secrets.token_hex(16)
        public_url=os.environ.get("SYNCSHIELD_PUBLIC_URL",f"http://localhost:{PORT}").rstrip("/")
        payload = {"amount": amount_minor, "currency": "INR", "accept_partial": False,
            "description": f"SyncShield Pro · {months} month{'s' if months != 1 else ''}",
            "customer": {"name": user["full_name"], "email": user["email"], **({"contact":phone.strip()} if phone.strip() else {})},
            "notify": {"sms": False, "email": False}, "reminder_enable": False,
            "reference_id":reference_id,"callback_url":f"{public_url}/checkout?payment_ref={reference_id}{'&mode=onboarding' if onboarding else ''}","callback_method":"get",
            "notes": {"plan": "Pro", "months": str(months),"billing_country":country.strip()}}
        request = urllib.request.Request("https://api.razorpay.com/v1/payment_links",
            data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json",
            "Authorization": "Basic " + base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                provider = json.loads(response.read(1_000_000))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            print("Razorpay checkout request failed.")
            return self.send_json(502, {"error": "The payment provider could not create checkout. Please retry shortly."})
        if not provider.get("id") or not provider.get("short_url"):
            print("Razorpay returned a checkout response without a payment link URL.")
            return self.send_json(502, {"error": "The payment provider returned an invalid checkout response."})
        payment_id = provider["id"]
        with db() as con:
            con.execute("INSERT INTO payment_intents(id,workspace_id,user_id,gateway,plan,months,amount_minor,currency,status,reference_id,billing_country) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (payment_id,user["workspace_id"],user["id"],"Razorpay","Pro",months,amount_minor,"INR","CREATED",reference_id,country.strip()))
        audit_event(user["workspace_id"], user["id"], "payment.checkout_created", {"payment_id":payment_id,"months":months,"amount_minor":amount_minor,"currency":"INR"})
        return self.send_json(201, {"payment_id":payment_id,"payment_ref":reference_id,"checkout_url":provider["short_url"],
            "status":"CREATED","amount_minor":amount_minor,"currency":"INR","months":months})

    def demo_complete(self, data):
        auth = self.session()
        if not auth: return self.send_json(401, {"error": "Please sign in."})
        user = auth[0]
        addr = ipaddress.ip_address(self.client_address[0].split("%", 1)[0])
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped: addr = addr.ipv4_mapped
        if os.environ.get("SYNCSHIELD_DEMO_PAYMENTS", "1") != "1" or not addr.is_loopback:
            return self.send_json(404, {"error": "Simulated checkout is available only in the local development environment."})
        if user["role"] == "Viewer" or user["subscription_status"] != "ACTIVE":
            return self.send_json(403, {"error": "This account cannot manage workspace billing."})
        allowed = {"months", "billing_country", "payment_method", "onboarding"}
        if set(data) - allowed or data.get("months") not in (1, 3, 12) or isinstance(data.get("months"), bool):
            return self.send_json(400, {"error": "Choose a valid Pro term."})
        country, method = data.get("billing_country", "India"), data.get("payment_method")
        if not isinstance(country, str) or not country.strip() or len(country) > 80 or method not in ("CARD", "UPI", "NETBANKING", "BANK_TRANSFER"):
            return self.send_json(400, {"error": "Check your country and payment method."})
        onboarding = data.get("onboarding", False)
        if not isinstance(onboarding, bool): return self.send_json(400, {"error": "Invalid checkout mode."})
        if onboarding and user["onboarding_complete"]: return self.send_json(409, {"error": "Workspace setup is already complete."})
        if not onboarding and not user["onboarding_complete"]: return self.send_json(409, {"error": "Finish workspace setup before purchasing a plan."})
        now = datetime.now(timezone.utc)
        old = user["subscription_expires_at"]
        if old:
            try:
                old_date = datetime.fromisoformat(old.replace("Z", "+00:00"))
                if old_date > now: now = old_date
            except ValueError: pass
        months = data["months"]
        month_index = now.year * 12 + now.month - 1 + months
        year, month = divmod(month_index, 12); month += 1
        expiry = now.replace(year=year, month=month, day=min(now.day, calendar.monthrange(year, month)[1]))
        started_at, expires_at = now.isoformat(timespec="seconds"), expiry.isoformat(timespec="seconds")
        payment_id = "demo_" + secrets.token_hex(10)
        with db() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("INSERT INTO payment_intents(id,workspace_id,user_id,gateway,plan,months,amount_minor,currency,status,reference_id,billing_country) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (payment_id,user["workspace_id"],user["id"],"SyncShield simulated checkout","Pro",months,99900*months,"INR","DEMO_PAID",secrets.token_hex(16),country.strip()))
            con.execute("UPDATE workspaces SET plan='Pro',subscription_status='ACTIVE',subscription_started_at=?,subscription_expires_at=? WHERE id=?",
                (started_at,expires_at,user["workspace_id"]))
            con.execute("UPDATE users SET onboarding_complete=1 WHERE workspace_id=?",(user["workspace_id"],))
        audit_event(user["workspace_id"],user["id"],"payment.demo_completed",{"payment_id":payment_id,"months":months,"method":method,"amount_minor":99900*months})
        return self.send_json(200,{"status":"DEMO_PAID","simulated":True,"plan":"Pro","months":months,"starts_at":started_at,"expires_at":expires_at,"reference":payment_id})

    def payment_status(self, auth, query):
        if not auth:return self.send_json(401,{"error":"Please sign in to confirm your payment."})
        reference_id=query.get("payment_ref",[""])[0].strip()
        if not re.fullmatch(r"[a-f0-9]{32}",reference_id):return self.send_json(400,{"error":"Payment reference is missing or invalid."})
        user=auth[0]
        with db() as con:
            intent=con.execute("SELECT * FROM payment_intents WHERE reference_id=? AND workspace_id=?",(reference_id,user["workspace_id"])).fetchone()
        if not intent:return self.send_json(404,{"error":"That payment reference does not belong to this workspace."})
        if intent["status"]=="PAID":
            with db() as con: dates=con.execute("SELECT subscription_started_at,subscription_expires_at FROM workspaces WHERE id=?",(user["workspace_id"],)).fetchone()
            return self.send_json(200,{"status":"PAID","plan":"Pro","months":intent["months"],"starts_at":dates[0],"expires_at":dates[1]})
        key_id,key_secret=os.environ.get("RAZORPAY_KEY_ID"),os.environ.get("RAZORPAY_KEY_SECRET")
        if not key_id or not key_secret:return self.send_json(503,{"error":"Payment verification is not configured on this server."})
        request=urllib.request.Request(f"https://api.razorpay.com/v1/payment_links/{intent['id']}",headers={"Authorization":"Basic "+base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()},method="GET")
        try:
            with urllib.request.urlopen(request,timeout=15) as response: provider=json.loads(response.read(1_000_000))
        except (urllib.error.URLError,TimeoutError,json.JSONDecodeError):
            return self.send_json(502,{"error":"The payment provider could not verify this payment yet. Retry in a moment."})
        if provider.get("reference_id")!=reference_id or provider.get("id")!=intent["id"]:
            return self.send_json(400,{"error":"Payment verification did not match this checkout."})
        paid=int(provider.get("amount_paid",0) or 0)>=intent["amount_minor"] and provider.get("status")=="paid"
        if not paid:return self.send_json(200,{"status":str(provider.get("status","pending")).upper(),"plan":"Basic","months":intent["months"]})
        paid_at=provider.get("paid_at")
        start=datetime.fromtimestamp(int(paid_at),timezone.utc) if paid_at else datetime.now(timezone.utc)
        with db() as con:
            con.execute("BEGIN IMMEDIATE")
            fresh=con.execute("SELECT status FROM payment_intents WHERE id=? AND workspace_id=?",(intent["id"],user["workspace_id"])).fetchone()
            workspace=con.execute("SELECT subscription_expires_at FROM workspaces WHERE id=?",(user["workspace_id"],)).fetchone()
            if fresh["status"]!="PAID":
                old=workspace["subscription_expires_at"]
                if old:
                    try:
                        old_date=datetime.fromisoformat(old.replace("Z","+00:00"))
                        if old_date>start:start=old_date
                    except ValueError:pass
                month_index=start.year*12+start.month-1+intent["months"]
                year,month=divmod(month_index,12);month+=1
                expiry=start.replace(year=year,month=month,day=min(start.day,calendar.monthrange(year,month)[1]))
                started_at=start.astimezone(timezone.utc).isoformat(timespec="seconds")
                expires_at=expiry.astimezone(timezone.utc).isoformat(timespec="seconds")
                con.execute("UPDATE payment_intents SET status='PAID' WHERE id=?",(intent["id"],))
                con.execute("UPDATE workspaces SET plan='Pro',subscription_status='ACTIVE',subscription_started_at=?,subscription_expires_at=? WHERE id=?",(started_at,expires_at,user["workspace_id"]))
                con.execute("UPDATE users SET onboarding_complete=1 WHERE workspace_id=?",(user["workspace_id"],))
            else:
                dates=con.execute("SELECT subscription_started_at,subscription_expires_at FROM workspaces WHERE id=?",(user["workspace_id"],)).fetchone()
                started_at,expires_at=dates[0],dates[1]
        audit_event(user["workspace_id"],user["id"],"payment.pro_activated",{"payment_id":intent["id"],"months":intent["months"],"expires_at":expires_at})
        return self.send_json(200,{"status":"PAID","plan":"Pro","months":intent["months"],"starts_at":started_at,"expires_at":expires_at})

    def admin_diagnostics(self, auth):
        if not auth: return self.send_json(401, {"error": "Please sign in."})
        user = auth[0]
        if user["role"] != "Admin":
            audit_event(user["workspace_id"], user["id"], "access.denied.admin_diagnostics", {"role":user["role"]})
            return self.send_json(403, {"error": "Admin role required to view operational diagnostics and audit events."})
        with db() as con:
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            events = con.execute("""SELECT a.id,a.event_type,a.details_json,a.created_at,u.full_name
                FROM audit_events a LEFT JOIN users u ON u.id=a.user_id WHERE a.workspace_id=?
                ORDER BY a.created_at DESC LIMIT 50""", (user["workspace_id"],)).fetchall()
            run_counts = con.execute("SELECT COUNT(*) total, SUM(status='Failed') failed FROM runs WHERE workspace_id=?",(user["workspace_id"],)).fetchone()
            payment = con.execute("SELECT id,status,amount_minor,currency,months,created_at FROM payment_intents WHERE workspace_id=? ORDER BY created_at DESC LIMIT 1",(user["workspace_id"],)).fetchone()
        return self.send_json(200, {"service":"SyncShield","database":"healthy" if integrity=="ok" else "attention",
            "database_check":integrity,"tenant":user["workspace_name"],"runs":run_counts["total"],"failed_runs":run_counts["failed"] or 0,
            "latest_payment":dict(payment) if payment else None,
            "events":[{"id":e["id"],"event_type":e["event_type"],"details":json.loads(e["details_json"]),"created_at":e["created_at"],"actor":e["full_name"] or "System"} for e in events]})

    def admin_overview(self, auth):
        if not auth: return self.send_json(401, {"error": "Administrator sign-in required."})
        admin = auth[0]
        if admin["role"] != "Admin":
            audit_event(admin["workspace_id"], admin["id"], "access.denied.platform_admin", {})
            return self.send_json(403, {"error": "Platform administrator access required."})
        day = utc_day()
        with db() as con:
            totals = con.execute("""SELECT (SELECT COUNT(*) FROM users) users,
                (SELECT COUNT(*) FROM workspaces) workspaces,
                (SELECT COUNT(DISTINCT user_id) FROM sessions WHERE expires_at>? AND last_seen_at>=datetime('now','-5 minutes')) online""",
                (int(time.time()),)).fetchone()
            tenants = con.execute("""SELECT w.id,w.name,w.plan,w.subscription_status,w.created_at,
                COUNT(DISTINCT u.id) user_count,COALESCE(du.usage_count,0) quota_used,
                COUNT(DISTINCT CASE WHEN date(r.created_at)=? THEN r.id END) runs_today,
                MAX(r.created_at) last_run
                FROM workspaces w LEFT JOIN users u ON u.workspace_id=w.id
                LEFT JOIN daily_usage du ON du.workspace_id=w.id AND du.usage_date=?
                LEFT JOIN runs r ON r.workspace_id=w.id
                GROUP BY w.id,w.name,w.plan,w.subscription_status,w.created_at,du.usage_count
                ORDER BY w.created_at DESC""", (day, day)).fetchall()
            members = con.execute("""SELECT u.id,u.full_name,u.email,u.role,u.job_title,u.created_at,w.id workspace_id,w.name workspace,
                w.plan,w.subscription_status,COALESCE(du.usage_count,0) quota_used
                FROM users u JOIN workspaces w ON w.id=u.workspace_id
                LEFT JOIN daily_usage du ON du.workspace_id=w.id AND du.usage_date=?
                ORDER BY u.created_at DESC""", (day,)).fetchall()
            feedback = con.execute("""SELECT f.id,f.category,f.message,f.status,f.created_at,u.full_name,u.email,w.name workspace
                FROM feedback f JOIN users u ON u.id=f.user_id JOIN workspaces w ON w.id=f.workspace_id
                ORDER BY f.created_at DESC LIMIT 100""").fetchall()
            plans = con.execute("SELECT plan,COUNT(*) count FROM workspaces GROUP BY plan").fetchall()
        tenant_data = []
        for row in tenants:
            item = dict(row)
            item["quota_limit"] = PLAN_LIMITS.get(item["plan"], PLAN_LIMITS["Basic"])
            item["quota_remaining"] = max(0, item["quota_limit"] - item["quota_used"])
            tenant_data.append(item)
        member_data = []
        for row in members:
            item = dict(row)
            item["quota_limit"] = PLAN_LIMITS.get(item["plan"], PLAN_LIMITS["Basic"])
            item["quota_remaining"] = max(0, item["quota_limit"] - item["quota_used"])
            member_data.append(item)
        audit_event(admin["workspace_id"], admin["id"], "admin.overview.viewed", {"workspaces": totals["workspaces"]})
        return self.send_json(200, {"users": totals["users"], "online_users": totals["online"],
            "workspaces": totals["workspaces"], "utc_date": day,
            "plans": {row["plan"]: row["count"] for row in plans}, "tenants": tenant_data,
            "members": member_data, "feedback": [dict(row) for row in feedback]})

    def _platform_admin(self, auth):
        if not auth: return None, self.send_json(401, {"error":"Platform administrator sign-in required."})
        admin = auth[0]
        if admin["role"] != "Admin":
            audit_event(admin["workspace_id"], admin["id"], "access.denied.platform_admin", {})
            return None, self.send_json(403, {"error":"Platform administrator access required."})
        return admin, None

    def platform_admin_requests(self, auth):
        admin,error=self._platform_admin(auth)
        if error:return error
        with platform_admin_db() as con:
            requests=con.execute("""SELECT id,full_name,email,company_id,organization_name,professional_role,submitted_at
                FROM platform_admins WHERE status='PENDING' ORDER BY submitted_at""").fetchall()
        return self.send_json(200,{"requests":[dict(r) for r in requests]})

    def platform_admin_request_action(self,data):
        admin,error=self._platform_admin(self.session())
        if error:return error
        if set(data)!={"request_id","action"} or data.get("action") not in ("approve","reject"):
            return self.send_json(400,{"error":"Choose whether to approve or reject this request."})
        request_id=data["request_id"]
        if not isinstance(request_id,str) or not re.fullmatch(r"[a-f0-9]{32}",request_id):
            return self.send_json(400,{"error":"Invalid request ID."})
        with platform_admin_db() as con:
            request=con.execute("SELECT * FROM platform_admins WHERE id=? AND status='PENDING'",(request_id,)).fetchone()
        if not request:return self.send_json(404,{"error":"Pending administrator request not found."})
        if data["action"]=="reject":
            with platform_admin_db() as con:
                con.execute("UPDATE platform_admins SET status='REJECTED',approved_at=CURRENT_TIMESTAMP,approved_by=? WHERE id=? AND status='PENDING'",(admin["id"],request_id))
            audit_event(admin["workspace_id"],admin["id"],"admin.registration_rejected",{"email":request["email"]})
            return self.send_json(200,{"ok":True,"status":"REJECTED"})
        with db() as con:
            if con.execute("SELECT 1 FROM users WHERE email=? COLLATE NOCASE",(request["email"],)).fetchone():
                return self.send_json(409,{"error":"A workspace account already uses this email. Resolve that account before approval."})
        with platform_admin_db() as con:
            changed=con.execute("UPDATE platform_admins SET status='ACTIVE',approved_at=CURRENT_TIMESTAMP,approved_by=? WHERE id=? AND status='PENDING'",(admin["id"],request_id)).rowcount
        if not changed:return self.send_json(409,{"error":"This request has already been reviewed."})
        workspace_id=secrets.token_hex(16)
        try:
            with db() as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute("INSERT INTO workspaces(id,name,plan) VALUES(?,?,'Basic')",(workspace_id,request["organization_name"]))
                con.execute("INSERT INTO users(id,workspace_id,full_name,email,password_hash,role,job_title,onboarding_complete) VALUES(?,?,?,?,?,'Admin',?,1)",
                    (request_id,workspace_id,request["full_name"],request["email"],password_hash(secrets.token_urlsafe(40)),request["professional_role"]))
        except sqlite3.IntegrityError:
            with platform_admin_db() as con:con.execute("UPDATE platform_admins SET status='PENDING',approved_at=NULL,approved_by=NULL WHERE id=?",(request_id,))
            return self.send_json(409,{"error":"Could not create this administrator account. The request remains pending."})
        audit_event(admin["workspace_id"],admin["id"],"admin.registration_approved",{"email":request["email"],"company_id":request["company_id"]})
        return self.send_json(200,{"ok":True,"status":"ACTIVE","email":request["email"]})

    def platform_overview(self, auth, query):
        admin, error = self._platform_admin(auth)
        if error: return error
        end = utc_day(); start = (datetime.now(timezone.utc).date() - timedelta(days=29)).isoformat()
        start_arg = query.get("start", [start])[0]; end_arg = query.get("end", [end])[0]
        try:
            datetime.strptime(start_arg, "%Y-%m-%d"); datetime.strptime(end_arg, "%Y-%m-%d")
            if start_arg > end_arg or (datetime.strptime(end_arg, "%Y-%m-%d") - datetime.strptime(start_arg, "%Y-%m-%d")).days > 365:
                raise ValueError()
        except ValueError: return self.send_json(400, {"error":"Choose a valid date range up to 366 days."})
        today = utc_day(); month_start = datetime.now(timezone.utc).date().replace(day=1).isoformat()
        with db() as con:
            counts = con.execute("""SELECT (SELECT COUNT(*) FROM users) users,
                (SELECT COUNT(*) FROM audit_events WHERE event_type IN ('user.login','admin.login') AND date(created_at)=?) logins_today,
                (SELECT COUNT(*) FROM audit_events WHERE event_type IN ('user.login','admin.login') AND date(created_at)>=?) logins_month,
                (SELECT COUNT(DISTINCT user_id) FROM sessions WHERE expires_at>? AND last_seen_at>=datetime('now','-5 minutes')) active_now,
                (SELECT COUNT(DISTINCT user_id) FROM (SELECT user_id FROM audit_events WHERE event_type IN ('user.login','admin.login') AND date(created_at)=? UNION SELECT user_id FROM runs WHERE date(created_at)=? UNION SELECT user_id FROM sessions WHERE date(last_seen_at)=?)) active_today,
                (SELECT COUNT(DISTINCT user_id) FROM (SELECT user_id FROM audit_events WHERE event_type IN ('user.login','admin.login') AND date(created_at)>=? UNION SELECT user_id FROM runs WHERE date(created_at)>=? UNION SELECT user_id FROM sessions WHERE date(last_seen_at)>=?)) active_month,
                (SELECT COUNT(*) FROM workspaces) organizations,
                (SELECT COUNT(*) FROM workspaces WHERE plan='Basic') basic,
                (SELECT COUNT(*) FROM workspaces WHERE plan='Pro') pro""",
                (today,today,int(time.time()),today,today,today,month_start,month_start,month_start)).fetchone()
            daily_logins = con.execute("SELECT date(created_at) date,COUNT(*) count FROM audit_events WHERE event_type IN ('user.login','admin.login') AND date(created_at) BETWEEN ? AND ? GROUP BY date(created_at)",(start_arg,end_arg)).fetchall()
            daily_active = con.execute("SELECT date,COUNT(DISTINCT user_id) count FROM (SELECT date(created_at) date,user_id FROM audit_events WHERE event_type IN ('user.login','admin.login') UNION SELECT date(created_at) date,user_id FROM runs UNION SELECT date(last_seen_at) date,user_id FROM sessions) WHERE date BETWEEN ? AND ? GROUP BY date ORDER BY date",(start_arg,end_arg)).fetchall()
            daily_users = con.execute("SELECT date(created_at) date,COUNT(*) count FROM users WHERE date(created_at) BETWEEN ? AND ? GROUP BY date(created_at)",(start_arg,end_arg)).fetchall()
            monthly_active = con.execute("SELECT strftime('%Y-%m',date) month,COUNT(DISTINCT user_id) count FROM (SELECT date(created_at) date,user_id FROM audit_events WHERE event_type IN ('user.login','admin.login') UNION SELECT date(created_at) date,user_id FROM runs UNION SELECT date(last_seen_at) date,user_id FROM sessions) WHERE date BETWEEN ? AND ? GROUP BY strftime('%Y-%m',date)",(start_arg,end_arg)).fetchall()
            daily_usage = con.execute("SELECT usage_date date,SUM(usage_count) count FROM daily_usage WHERE usage_date BETWEEN ? AND ? GROUP BY usage_date ORDER BY usage_date",(start_arg,end_arg)).fetchall()
            monthly_logins = con.execute("SELECT strftime('%Y-%m',created_at) month,COUNT(*) count FROM audit_events WHERE event_type IN ('user.login','admin.login') AND date(created_at) BETWEEN ? AND ? GROUP BY month ORDER BY month",(start_arg,end_arg)).fetchall()
            monthly_users = con.execute("SELECT strftime('%Y-%m',created_at) month,COUNT(*) count FROM users WHERE date(created_at) BETWEEN ? AND ? GROUP BY month ORDER BY month",(start_arg,end_arg)).fetchall()
            monthly_usage = con.execute("SELECT substr(usage_date,1,7) month,SUM(usage_count) count FROM daily_usage WHERE usage_date BETWEEN ? AND ? GROUP BY month ORDER BY month",(start_arg,end_arg)).fetchall()
            plan_distribution = con.execute("SELECT plan label,COUNT(*) count FROM workspaces GROUP BY plan ORDER BY plan").fetchall()
            account_distribution = con.execute("SELECT account_status label,COUNT(*) count FROM users GROUP BY account_status ORDER BY account_status").fetchall()
            run_distribution = con.execute("SELECT status label,COUNT(*) count FROM runs WHERE date(created_at) BETWEEN ? AND ? GROUP BY status ORDER BY status",(start_arg,end_arg)).fetchall()
            login_recent = con.execute("SELECT a.id,a.event_type,a.created_at,u.id user_id,u.full_name,u.email FROM audit_events a LEFT JOIN users u ON u.id=a.user_id WHERE a.event_type IN ('user.login','admin.login') ORDER BY a.created_at DESC LIMIT 8").fetchall()
            audit = con.execute("SELECT a.id,a.event_type,a.created_at,a.details_json,u.full_name actor FROM audit_events a LEFT JOIN users u ON u.id=a.user_id WHERE a.event_type IN ('admin.account_created','admin.user_status','admin.user_plan','admin.feedback_updated','plan.demo_changed') ORDER BY a.created_at DESC LIMIT 20").fetchall()
        audit_event(admin["workspace_id"], admin["id"], "admin.platform_overview.viewed", {"start":start_arg,"end":end_arg})
        return self.send_json(200, {"range":{"start":start_arg,"end":end_arg},"metrics":dict(counts),
            "daily_logins":[dict(x) for x in daily_logins],"daily_active":[dict(x) for x in daily_active],"daily_users":[dict(x) for x in daily_users],
            "monthly_active":[dict(x) for x in monthly_active],"daily_usage":[dict(x) for x in daily_usage],
            "monthly_logins":[dict(x) for x in monthly_logins],"monthly_users":[dict(x) for x in monthly_users],"monthly_usage":[dict(x) for x in monthly_usage],
            "plan_distribution":[dict(x) for x in plan_distribution],"account_distribution":[dict(x) for x in account_distribution],"run_distribution":[dict(x) for x in run_distribution],
            "recent_logins":[dict(x) for x in login_recent],"audit":[{**dict(x),"details":json.loads(x["details_json"])} for x in audit]})

    def platform_users(self, auth, query):
        admin, error = self._platform_admin(auth)
        if error: return error
        term = query.get("q", [""])[0].strip()[:120]
        plan = query.get("plan", [""])[0]; status = query.get("status", [""])[0]
        title = query.get("job_title", [""])[0]; role = query.get("role", [""])[0]
        try:
            page = max(1, min(10000, int(query.get("page", ["1"])[0])))
            limit = max(1, min(100, int(query.get("limit", ["25"])[0])))
        except (ValueError, TypeError):
            return self.send_json(400, {"error":"Page and page size must be whole numbers."})
        where = []; args = []
        if term:
            where.append("(u.id LIKE ? OR u.full_name LIKE ? OR u.email LIKE ?)"); args += [f"%{term}%"]*3
        if plan in ("Basic","Pro"): where.append("w.plan=?"); args.append(plan)
        if status in ("ACTIVE","SUSPENDED"): where.append("u.account_status=?"); args.append(status)
        if title: where.append("u.job_title=?"); args.append(title)
        if role in ("Admin","Operator","Viewer"): where.append("u.role=?"); args.append(role)
        clause = " WHERE " + " AND ".join(where) if where else ""
        with db() as con:
            total = con.execute("SELECT COUNT(*) FROM users u JOIN workspaces w ON w.id=u.workspace_id"+clause,args).fetchone()[0]
            rows = con.execute("""SELECT u.id,u.full_name,u.email,u.job_title,u.role,u.account_status,u.created_at,
                w.id workspace_id,w.name organization,w.plan,
                (SELECT MAX(created_at) FROM audit_events WHERE user_id=u.id AND event_type IN ('user.login','admin.login')) last_login,
                (SELECT COUNT(*) FROM runs WHERE user_id=u.id) run_count
                FROM users u JOIN workspaces w ON w.id=u.workspace_id"""+clause+" ORDER BY u.created_at DESC LIMIT ? OFFSET ?",args+[limit,(page-1)*limit]).fetchall()
        return self.send_json(200,{"users":[dict(r) for r in rows],"total":total,"page":page,"limit":limit,"pages":max(1,(total+limit-1)//limit),"job_titles":list(TEAM_JOB_TITLES)})

    def platform_user_profile(self, auth, user_id):
        admin,error=self._platform_admin(auth)
        if error:return error
        with db() as con:
            user=con.execute("""SELECT u.id,u.full_name,u.email,u.job_title,u.role,u.account_status,u.created_at,w.id workspace_id,w.name organization,w.plan,w.subscription_status,w.subscription_started_at,w.subscription_expires_at FROM users u JOIN workspaces w ON w.id=u.workspace_id WHERE u.id=?""",(user_id,)).fetchone()
            if not user:return self.send_json(404,{"error":"User not found."})
            logins=con.execute("SELECT event_type,created_at FROM audit_events WHERE user_id=? AND event_type IN ('user.login','admin.login') ORDER BY created_at DESC LIMIT 20",(user_id,)).fetchall()
            today=utc_day()
            runs=con.execute("SELECT id,kind,status,input_rows,valid_rows,invalid_rows,created_at FROM runs WHERE user_id=? ORDER BY created_at DESC LIMIT 20",(user_id,)).fetchall()
            usage=con.execute("SELECT COALESCE(SUM(usage_count),0) FROM daily_usage WHERE workspace_id=? AND usage_date=?",(user["workspace_id"],today)).fetchone()[0]
            totals=con.execute("SELECT COUNT(*) run_count,COALESCE(SUM(input_rows),0) rows_processed,COALESCE(SUM(LENGTH(CAST(result_json AS BLOB))),0) saved_snapshot_bytes,MAX(created_at) last_run FROM runs WHERE user_id=?",(user_id,)).fetchone()
            personal_today=con.execute("SELECT COUNT(*) FROM runs WHERE user_id=? AND date(created_at)=?",(user_id,today)).fetchone()[0]
            feedback=con.execute("SELECT id,category,status,rating,created_at FROM feedback WHERE user_id=? ORDER BY created_at DESC LIMIT 10",(user_id,)).fetchall()
        audit_event(admin["workspace_id"],admin["id"],"admin.user_profile.viewed",{"user_id":user_id})
        plan_limit=PLAN_LIMITS.get(user["plan"],0)
        return self.send_json(200,{"user":dict(user),"usage":{"plan_run_limit_per_day":plan_limit,"workspace_runs_today":usage,"workspace_runs_remaining_today":max(0,plan_limit-usage),"user_runs_today":personal_today,"run_count":totals["run_count"],"rows_processed":totals["rows_processed"],"saved_snapshot_bytes":totals["saved_snapshot_bytes"],"last_run":totals["last_run"],"plan_expires_at":user["subscription_expires_at"],"plan_expiry_label":user["subscription_expires_at"] or "Not recorded"},"logins":[dict(r) for r in logins],"runs":[dict(r) for r in runs],"feedback":[dict(r) for r in feedback]})

    def platform_feedback(self, auth):
        admin,error=self._platform_admin(auth)
        if error:return error
        with db() as con:
            rows=con.execute("""SELECT f.id,f.category,f.message,f.status,f.rating,f.assigned_to,f.resolution_notes,f.created_at,
                u.id user_id,u.full_name,u.email,w.name organization FROM feedback f
                JOIN users u ON u.id=f.user_id JOIN workspaces w ON w.id=f.workspace_id
                ORDER BY CASE WHEN f.status IN ('NEW','IN_REVIEW') THEN 0 ELSE 1 END,f.created_at DESC LIMIT 300""").fetchall()
        return self.send_json(200,{"items":[dict(r) for r in rows]})

    def platform_user_action(self,data):
        auth=self.session()
        admin,error=self._platform_admin(auth)
        if error:return error
        if set(data)!={"user_id","action","value"} or data["action"] not in ("status","plan"):
            return self.send_json(400,{"error":"Choose an account status or subscription plan change."})
        target=data["user_id"]; action=data["action"]; value=data["value"]
        if action=="status" and value not in ("ACTIVE","SUSPENDED"):return self.send_json(400,{"error":"Invalid account status."})
        if action=="plan" and value not in ("Basic","Pro"):return self.send_json(400,{"error":"Invalid subscription plan."})
        with db() as con:
            row=con.execute("SELECT workspace_id,role,email FROM users WHERE id=?",(target,)).fetchone()
            if not row:return self.send_json(404,{"error":"User not found."})
            if target==admin["id"] and action=="status" and value=="SUSPENDED":return self.send_json(400,{"error":"You cannot suspend your own active administrator account."})
            if action=="status":
                con.execute("UPDATE users SET account_status=? WHERE id=?",(value,target))
                if value=="SUSPENDED":con.execute("DELETE FROM sessions WHERE user_id=?",(target,))
            elif value=="Basic":con.execute("UPDATE workspaces SET plan='Basic',subscription_started_at=NULL,subscription_expires_at=NULL WHERE id=?",(row["workspace_id"],))
            else:con.execute("UPDATE workspaces SET plan='Pro' WHERE id=?",(row["workspace_id"],))
        audit_event(admin["workspace_id"],admin["id"],"admin.user_"+action,{"user_id":target,"email":row["email"],"value":value})
        return self.send_json(200,{"ok":True})

    def platform_feedback_action(self,data):
        auth=self.session();admin,error=self._platform_admin(auth)
        if error:return error
        if set(data)!={"feedback_id","status","assigned_to","resolution_notes"}:return self.send_json(400,{"error":"Provide feedback status, assignee and resolution notes."})
        status=data["status"];assignee=data["assigned_to"];notes=data["resolution_notes"]
        if status not in ("NEW","IN_REVIEW","RESOLVED","CLOSED") or not isinstance(assignee,str) or len(assignee)>254 or not isinstance(notes,str) or len(notes)>2000:return self.send_json(400,{"error":"Check status, assignee and resolution notes."})
        with db() as con:
            row=con.execute("SELECT id FROM feedback WHERE id=?",(data["feedback_id"],)).fetchone()
            if not row:return self.send_json(404,{"error":"Feedback item not found."})
            if assignee.strip() and not con.execute("SELECT 1 FROM users WHERE email=? COLLATE NOCASE AND role='Admin'",(assignee.strip(),)).fetchone():
                return self.send_json(400,{"error":"Assign this item to an authorized platform administrator email."})
            con.execute("UPDATE feedback SET status=?,assigned_to=?,resolution_notes=? WHERE id=?",(status,assignee.strip() or None,notes.strip(),data["feedback_id"]))
        audit_event(admin["workspace_id"],admin["id"],"admin.feedback_updated",{"feedback_id":data["feedback_id"],"status":status,"assigned_to":assignee.strip()})
        return self.send_json(200,{"ok":True})

    def quota_check(self, con, user, key, payload_fingerprint):
        workspace_id, day = user["workspace_id"], utc_day()
        if key:
            old = con.execute("SELECT * FROM runs WHERE workspace_id=? AND idempotency_key=?", (workspace_id, key)).fetchone()
            if old:
                if old["input_fingerprint"] != payload_fingerprint: return "conflict", old
                return "replay", old
        con.execute("INSERT OR IGNORE INTO daily_usage(workspace_id,usage_date,usage_count) VALUES(?,?,0)", (workspace_id, day))
        usage = con.execute("SELECT usage_count FROM daily_usage WHERE workspace_id=? AND usage_date=?", (workspace_id, day)).fetchone()[0]
        limit = PLAN_LIMITS.get(user["plan"], PLAN_LIMITS["Basic"])
        if usage >= limit: return "quota", {"used": usage, "limit": limit}
        con.execute("UPDATE daily_usage SET usage_count=usage_count+1 WHERE workspace_id=? AND usage_date=?", (workspace_id, day))
        return "accepted", None

    def commit_run(self, user, kind, validation, key, fp, result, counts=None):
        counts = counts or {"created": 0, "updated": 0, "unchanged": 0}
        run_id = secrets.token_hex(12)
        status = "Completed"
        with db() as con:
            con.execute("BEGIN IMMEDIATE")
            outcome, previous = self.quota_check(con, user, key, fp)
            if outcome == "conflict":
                con.rollback(); return "conflict", previous
            if outcome == "replay":
                con.rollback(); return "replay", previous
            if outcome == "quota":
                con.rollback(); return "quota", previous
            con.execute("""INSERT INTO runs(id,workspace_id,user_id,kind,status,input_rows,valid_rows,invalid_rows,issue_count,
              created_count,updated_count,unchanged_count,idempotency_key,input_fingerprint,result_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (run_id,user["workspace_id"],user["id"],kind,status,len(validation["rows"]),validation["valid_rows"],
               validation["invalid_rows"],len(validation["issues"]),counts["created"],counts["updated"],counts["unchanged"],
               key,fp,json.dumps(result,ensure_ascii=False)))
            con.execute("INSERT INTO audit_events(id,workspace_id,user_id,event_type,details_json) VALUES(?,?,?,?,?)",
                (secrets.token_hex(12),user["workspace_id"],user["id"],"run.created",
                 json.dumps({"run_id":run_id,"kind":kind,"rows":len(validation["rows"]),"status":status})))
            con.commit()
        with db() as con: saved=con.execute("SELECT * FROM runs WHERE id=? AND workspace_id=?",(run_id,user["workspace_id"])).fetchone()
        return "created", saved

    def validate(self, data):
        auth = self.session()
        if not auth: return self.send_json(401, {"error": "Sign in to validate a batch."})
        user = auth[0]
        if user["subscription_status"] != "ACTIVE": return self.send_json(403, {"error": "This workspace subscription is suspended. New runs are disabled."})
        if set(data) != {"rows"}: return self.send_json(400, {"error": "Only rows are accepted; workspace access is derived from your signed-in session."})
        if user["role"] == "Viewer":
            audit_event(user["workspace_id"], user["id"], "access.denied.validation", {"role":"Viewer"})
            return self.send_json(403, {"error": "Viewer access is read-only."})
        rows = data.get("rows")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 10: return self.send_json(400, {"error": "Provide between 1 and 10 customer rows."})
        result = validate_rows(rows)
        key = self.headers.get("Idempotency-Key", "").strip()[:120] or secrets.token_urlsafe(18)
        outcome, record = self.commit_run(user, "validation", result, key, fingerprint({"operation":"validation","rows":rows}), result)
        if outcome == "conflict": return self.send_json(409, {"error": "Idempotency key was already used with different input."})
        if outcome == "quota":
            audit_event(user["workspace_id"], user["id"], "quota.exceeded", {"used":record["used"],"limit":record["limit"],"kind":"validation"})
            return self.send_json(429, {"error": "Daily run quota reached.", "used": record["used"], "limit": record["limit"]})
        response = json.loads(record["result_json"])
        response.update({"run_id": record["id"], "status": record["status"], "replayed": outcome == "replay"})
        return self.send_json(200, response)

    def get_baseline(self, auth):
        if not auth: return self.send_json(401, {"error": "Please sign in to inspect the baseline."})
        user = auth[0]
        if user["plan"] != "Pro": return self.send_json(403, {"error": "Workspace baselines are part of Sync Preview on the Pro plan."})
        with db() as con:
            saved = con.execute("SELECT rows_json,updated_at FROM workspace_baselines WHERE workspace_id=?", (user["workspace_id"],)).fetchone()
        rows = json.loads(saved["rows_json"]) if saved else [{"record_id": "C01", "name": "Anu", "version": 1, "status": "ACTIVE"}]
        return self.send_json(200, {"rows": rows, "source": "workspace" if saved else "seeded", "updated_at": saved["updated_at"] if saved else None})

    def save_baseline(self, data):
        auth = self.session()
        if not auth: return self.send_json(401, {"error": "Sign in to load the old customer data as a baseline."})
        user = auth[0]
        if user["role"] == "Viewer": return self.send_json(403, {"error": "Viewer access is read-only; ask an operator to load the baseline."})
        if user["plan"] != "Pro": return self.send_json(403, {"error": "Workspace baselines are part of Sync Preview on the Pro plan."})
        if set(data) != {"rows"}: return self.send_json(400, {"error": "Only baseline rows are accepted; workspace access comes from your signed-in session."})
        rows = data.get("rows")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 10:
            return self.send_json(400, {"error": "A baseline must contain between 1 and 10 customer records."})
        validation = validate_rows(rows)
        if validation["invalid_rows"]:
            return self.send_json(400, {"error": "Baseline not saved. Fix the listed data errors first.", "error_code": "BASELINE_VALIDATION_FAILED", "valid_rows": validation["valid_rows"], "invalid_rows": validation["invalid_rows"], "issues": validation["issues"]})
        serialized = json.dumps(rows, ensure_ascii=False)
        with db() as con:
            con.execute("""INSERT INTO workspace_baselines(workspace_id,user_id,rows_json) VALUES(?,?,?)
                ON CONFLICT(workspace_id) DO UPDATE SET user_id=excluded.user_id,rows_json=excluded.rows_json,updated_at=CURRENT_TIMESTAMP""",
                (user["workspace_id"], user["id"], serialized))
        audit_event(user["workspace_id"], user["id"], "baseline.loaded", {"rows":len(rows)})
        return self.send_json(200, {"rows":rows,"source":"workspace","saved":True,"message":f"Loaded {len(rows)} valid baseline records for this workspace."})

    def process(self, data):
        auth = self.session()
        if not auth: return self.send_json(401, {"error": "Sign in to preview a batch."})
        user = auth[0]; rows = data.get("rows")
        if user["subscription_status"] != "ACTIVE": return self.send_json(403, {"error": "This workspace subscription is suspended. New runs are disabled."})
        if set(data) != {"rows"}: return self.send_json(400, {"error": "Only rows are accepted; workspace access is derived from your signed-in session."})
        if user["role"] == "Viewer":
            audit_event(user["workspace_id"], user["id"], "access.denied.preview", {"role":"Viewer"})
            return self.send_json(403, {"error": "Viewer access is read-only."})
        if user["plan"] != "Pro":
            audit_event(user["workspace_id"], user["id"], "access.denied.preview", {"plan":user["plan"]})
            return self.send_json(403, {"error": "Sync Preview is available on the Pro plan."})
        if not isinstance(rows, list) or not 1 <= len(rows) <= 10: return self.send_json(400, {"error": "Provide between 1 and 10 customer rows."})
        validation = validate_rows(rows)
        if validation["invalid_rows"]: return self.send_json(400, {"error": "Preview blocked: this batch contains invalid rows. Correct the listed fields and validate again; no preview run was created and no quota was used.", "error_code": "VALIDATION_BLOCKED", "reason": "Every row must pass R1–R4 before a sync preview can be generated.", "issues": validation["issues"]})
        with db() as con:
            saved_baseline = con.execute("SELECT rows_json FROM workspace_baselines WHERE workspace_id=?", (user["workspace_id"],)).fetchone()
        baseline_rows = json.loads(saved_baseline["rows_json"]) if saved_baseline else [{"record_id": "C01", "name": "Anu", "version": 1, "status": "ACTIVE"}]
        baseline = {row["record_id"]: row for row in baseline_rows}
        output, counts = [], {"created": 0, "updated": 0, "unchanged": 0}
        for row in rows:
            old = baseline.get(row["record_id"])
            action = "CREATE" if old is None else ("UNCHANGED" if all(old[k] == row[k] for k in ("name", "version", "status")) else "UPDATE")
            counts[{"CREATE": "created", "UPDATE": "updated", "UNCHANGED": "unchanged"}[action]] += 1
            changes = [] if old is None else [{"field": k, "before": old[k], "after": row[k]} for k in ("name", "version", "status") if old[k] != row[k]]
            output.append({"record_id": row["record_id"], "action": action,
                "reason": "No matching baseline record." if old is None else ("All compared fields match." if action == "UNCHANGED" else "One or more fields differ from the baseline."),
                "changes": changes, "incoming": row, "baseline": old})
        result = {"input_rows": len(rows), **counts, "rows": output}
        key = self.headers.get("Idempotency-Key", "").strip()[:120] or secrets.token_urlsafe(18)
        outcome, record = self.commit_run(user, "preview", validation, key, fingerprint({"operation":"preview","baseline":baseline_rows,"rows":rows}), result, counts)
        if outcome == "conflict": return self.send_json(409, {"error": "Idempotency key was already used with different input."})
        if outcome == "quota":
            audit_event(user["workspace_id"], user["id"], "quota.exceeded", {"used":record["used"],"limit":record["limit"],"kind":"preview"})
            return self.send_json(429, {"error": "Daily run quota reached.", "used": record["used"], "limit": record["limit"]})
        force_failure = os.environ.get("SYNCSHIELD_FORCE_PROCESS_FAILURE", "").lower() in ("1", "true", "yes")
        if outcome == "created" and force_failure:
            failure = {"run_id": record["id"], "status": "Failed", "error_code": "PROCESSOR_FAILED", "error": "The preview processor failed after the run was accepted."}
            with db() as con:
                con.execute("UPDATE runs SET status='Failed',error_code=?,result_json=? WHERE id=? AND workspace_id=?", (failure["error_code"],json.dumps(failure),record["id"],user["workspace_id"]))
            audit_event(user["workspace_id"], user["id"], "run.failed", {"run_id":record["id"],"error_code":failure["error_code"]})
            return self.send_json(500, failure)
        if outcome == "replay" and record["status"] == "Failed":
            saved = json.loads(record["result_json"])
            return self.send_json(500, saved)
        result.update({"run_id": record["id"], "status": record["status"], "replayed": outcome == "replay"})
        return self.send_json(200 if outcome == "replay" else 201, result)

    def list_runs(self, auth):
        if not auth: return self.send_json(401, {"error": "Please sign in."})
        user = auth[0]
        with db() as con:
            rows = con.execute("SELECT * FROM runs WHERE workspace_id=? ORDER BY created_at DESC LIMIT 100", (user["workspace_id"],)).fetchall()
        return self.send_json(200, {"runs": [run_public(r) for r in rows]})

    def get_run(self, auth, run_id):
        if not auth: return self.send_json(401, {"error": "Please sign in."})
        with db() as con:
            row = con.execute("SELECT * FROM runs WHERE id=? AND workspace_id=?", (run_id, auth[0]["workspace_id"])).fetchone()
        if not row: return self.send_json(404, {"error": "Run not found."})
        return self.send_json(200, run_public(row, True))

    def dashboard(self, auth):
        if not auth: return self.send_json(401, {"error": "Please sign in."})
        user = auth[0]; wid = user["workspace_id"]; day = utc_day()
        with db() as con:
            allrow = con.execute("""SELECT COUNT(*) total, SUM(status='Completed') successful, SUM(status='Failed') failed,
                COALESCE(SUM(input_rows),0) rows_processed, COALESCE(SUM(issue_count),0) validation_errors,
                COALESCE(SUM(created_count),0) created, COALESCE(SUM(updated_count),0) updated,
                COALESCE(SUM(unchanged_count),0) unchanged, COALESCE(SUM(valid_rows),0) valid_rows,
                COALESCE(SUM(invalid_rows),0) invalid_rows FROM runs WHERE workspace_id=?""", (wid,)).fetchone()
            today_row = con.execute("""SELECT COUNT(*) total,COALESCE(SUM(status='Completed'),0) successful,
                COALESCE(SUM(status='Failed'),0) failed,COALESCE(SUM(issue_count),0) validation_errors
                FROM runs WHERE workspace_id=? AND date(created_at)=?""", (wid, day)).fetchone()
            failed_previews = con.execute("SELECT COUNT(*) FROM runs WHERE workspace_id=? AND kind='preview' AND status='Failed'", (wid,)).fetchone()[0]
            preview_count = con.execute("SELECT COUNT(*) FROM runs WHERE workspace_id=? AND kind='preview'", (wid,)).fetchone()[0]
            daily = con.execute("SELECT date(created_at) day,COUNT(*) total FROM runs WHERE workspace_id=? AND created_at>=datetime('now','-6 days') GROUP BY date(created_at) ORDER BY day", (wid,)).fetchall()
            recent = con.execute("SELECT * FROM runs WHERE workspace_id=? ORDER BY created_at DESC LIMIT 10", (wid,)).fetchall()
            usage = con.execute("SELECT usage_count FROM daily_usage WHERE workspace_id=? AND usage_date=?", (wid, day)).fetchone()
        total_rows = allrow["valid_rows"] + allrow["invalid_rows"]
        metrics = {"runs_today": today_row["total"], "total_runs": allrow["total"], "successful_runs": today_row["successful"],
            "failed_runs": today_row["failed"], "validation_errors": today_row["validation_errors"],
            "rows_processed": allrow["rows_processed"], "created": allrow["created"], "updated": allrow["updated"],
            "unchanged": allrow["unchanged"], "validation_success_rate": round(100*allrow["valid_rows"]/total_rows,1) if total_rows else 0,
            "processing_failure_rate": round(100*failed_previews/preview_count,1) if preview_count else 0,
            "quota_used": usage[0] if usage else 0, "quota_limit": PLAN_LIMITS.get(user["plan"],3), "plan": user["plan"]}
        series = []
        for delta in range(6, -1, -1):
            date = (datetime.now(timezone.utc).date()-timedelta(days=delta)).isoformat()
            series.append({"date": date, "runs": next((r["total"] for r in daily if r["day"] == date), 0)})
        return self.send_json(200, {"metrics": metrics, "daily_runs": series, "recent_runs": [run_public(r) for r in recent],
            "tenant": {"id": wid, "name": user["workspace_name"]}, "plan": user["plan"]})

    def usage(self, auth):
        if not auth: return self.send_json(401, {"error": "Please sign in."})
        user = auth[0]; day = utc_day()
        with db() as con:
            row = con.execute("SELECT usage_count FROM daily_usage WHERE workspace_id=? AND usage_date=?", (user["workspace_id"], day)).fetchone()
            payment = con.execute("SELECT id,status,months,amount_minor,currency,created_at FROM payment_intents WHERE workspace_id=? ORDER BY created_at DESC LIMIT 1", (user["workspace_id"],)).fetchone()
        limit = PLAN_LIMITS.get(user["plan"], 3)
        return self.send_json(200, {"used": row[0] if row else 0, "limit": limit, "remaining": max(0,limit-(row[0] if row else 0)), "date": day, "plan": user["plan"], "subscription_started_at":user["subscription_started_at"],"subscription_expires_at":user["subscription_expires_at"],"latest_payment":dict(payment) if payment else None})


if __name__ == "__main__":
    # Bind only to the local IPv4 loopback interface for predictable Windows
    # localhost behavior and to keep the development server off the LAN.
    http_server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"SyncShield ready at http://127.0.0.1:{PORT}", flush=True)
    http_server.serve_forever()





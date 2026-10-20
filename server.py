"""SyncShield local SaaS prototype. Run with: python server.py"""
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qs
import hashlib, hmac, json, os, re, secrets, socket, sqlite3, time
import base64, urllib.error, urllib.request

ROOT = Path(__file__).parent
DB = Path(os.environ.get("SYNCSHIELD_DB", str(ROOT / "syncshield.db")))
PORT = int(os.environ.get("PORT", "8000"))
SESSION_SECONDS = 7 * 24 * 60 * 60
PLAN_LIMITS = {"Basic": 3, "Pro": 20}

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS workspaces (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, plan TEXT NOT NULL DEFAULT 'Basic', subscription_status TEXT NOT NULL DEFAULT 'ACTIVE',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 full_name TEXT NOT NULL, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
 password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'Operator',
 onboarding_complete INTEGER NOT NULL DEFAULT 0,
 preferred_format TEXT NOT NULL DEFAULT 'CSV', intended_use TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 expires_at INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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

with db() as con:
    con.executescript(SCHEMA)
    workspace_cols = {r["name"] for r in con.execute("PRAGMA table_info(workspaces)")}
    if "subscription_status" not in workspace_cols:
        con.execute("ALTER TABLE workspaces ADD COLUMN subscription_status TEXT NOT NULL DEFAULT 'ACTIVE'")
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

def public_user(row):
    return {"id": row["id"], "name": row["full_name"], "email": row["email"],
            "role": row["role"], "workspace": row["workspace_name"], "workspaceId": row["workspace_id"],
            "plan": row["plan"], "subscriptionStatus": row["subscription_status"], "onboardingComplete": bool(row["onboarding_complete"]),
            "preferredFormat": row["preferred_format"], "intendedUse": row["intended_use"]}

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
            user = con.execute("""SELECT u.*, w.name workspace_name, w.plan, w.subscription_status FROM sessions s
                JOIN users u ON u.id=s.user_id JOIN workspaces w ON w.id=u.workspace_id
                WHERE s.token_hash=? AND s.expires_at>?""", (token_hash, int(time.time()))).fetchone()
        return (user, token_hash) if user else None

    def set_session(self, user_id):
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with db() as con:
            con.execute("DELETE FROM sessions WHERE expires_at<=?", (int(time.time()),))
            con.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)", (token_hash, user_id, int(time.time()) + SESSION_SECONDS))
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
        if path == "/api/runs": return self.list_runs(auth)
        if path == "/api/admin/diagnostics": return self.admin_diagnostics(auth)
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
                  "/api/onboarding": self.onboarding, "/api/validate": self.validate,
                  "/api/process": self.process, "/api/plan": self.set_plan,
                  "/api/demo/role": self.demo_role, "/api/payments/checkout": self.create_checkout,
                  "/api/baseline": self.save_baseline}
        handler = routes.get(path)
        return handler(data) if handler else self.send_json(404, {"error": "Not found"})

    def signup(self, data):
        name = str(data.get("name", "")).strip(); email = str(data.get("email", "")).strip().lower()
        workspace = str(data.get("company", "")).strip(); password = data.get("password", "")
        if not name or len(name) > 100: return self.send_json(400, {"error": "Enter your name (up to 100 characters)."})
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254: return self.send_json(400, {"error": "Enter a valid work email."})
        if not isinstance(password, str) or len(password) < 10 or len(password) > 128: return self.send_json(400, {"error": "Password must be 10–128 characters."})
        if not workspace or len(workspace) > 100: return self.send_json(400, {"error": "Enter a workspace name (up to 100 characters)."})
        workspace_id, user_id = secrets.token_hex(16), secrets.token_hex(16)
        try:
            with db() as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute("INSERT INTO workspaces(id,name) VALUES(?,?)", (workspace_id, workspace))
                con.execute("INSERT INTO users(id,workspace_id,full_name,email,password_hash) VALUES(?,?,?,?,?)", (user_id, workspace_id, name, email, password_hash(password)))
                con.commit()
        except sqlite3.IntegrityError:
            return self.send_json(409, {"error": "An account with this email already exists."})
        return self.send_json(201, {"userId": user_id, "next": "/onboarding"}, self.set_session(user_id))

    def login(self, data):
        email = str(data.get("email", "")).strip().lower(); password = data.get("password", "")
        with db() as con: user = con.execute("SELECT * FROM users WHERE email=? COLLATE NOCASE", (email,)).fetchone()
        if not user or not isinstance(password, str) or not password_ok(password, user["password_hash"]):
            return self.send_json(401, {"error": "Email or password is incorrect."})
        return self.send_json(200, {"next": "/dashboard" if user["onboarding_complete"] else "/onboarding"}, self.set_session(user["id"]))

    def logout(self, _data):
        auth = self.session()
        if auth:
            with db() as con: con.execute("DELETE FROM sessions WHERE token_hash=?", (auth[1],))
        return self.send_json(200, {"ok": True}, {"Set-Cookie": "ss_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"})

    def onboarding(self, data):
        auth = self.session()
        if not auth: return self.send_json(401, {"error": "Please sign in."})
        name = str(data.get("workspace", "")).strip(); fmt = data.get("format", "CSV")
        use = str(data.get("use", "")).strip(); plan = data.get("plan", "Basic")
        if not name or len(name) > 100 or fmt not in ("CSV", "JSON") or len(use) > 300 or plan not in ("Basic", "Pro"):
            return self.send_json(400, {"error": "Check the workspace name and setup preferences."})
        with db() as con:
            con.execute("UPDATE workspaces SET name=?,plan=? WHERE id=?", (name, plan, auth[0]["workspace_id"]))
            con.execute("UPDATE users SET preferred_format=?,intended_use=?,onboarding_complete=1 WHERE id=?", (fmt, use, auth[0]["id"]))
        return self.send_json(200, {"ok": True, "next": "/dashboard"})

    def set_plan(self, data):
        """Demo-only plan selector; workspace-scoped and available to operators/admins."""
        auth = self.session()
        if not auth: return self.send_json(401, {"error": "Please sign in."})
        user = auth[0]
        if user["role"] == "Viewer": return self.send_json(403, {"error": "Viewer access is read-only."})
        if user["subscription_status"] != "ACTIVE": return self.send_json(403, {"error": "This workspace subscription is suspended."})
        if set(data) != {"plan"} or data.get("plan") not in ("Basic", "Pro"):
            return self.send_json(400, {"error": "Choose Basic or Pro for this demo workspace."})
        with db() as con:
            con.execute("UPDATE workspaces SET plan=? WHERE id=?", (data["plan"], user["workspace_id"]))
            updated = con.execute("""SELECT u.*, w.name workspace_name, w.plan, w.subscription_status
                FROM users u JOIN workspaces w ON w.id=u.workspace_id WHERE u.id=?""", (user["id"],)).fetchone()
        audit_event(user["workspace_id"], user["id"], "plan.demo_changed", {"from":user["plan"],"to":data["plan"]})
        return self.send_json(200, {"user": public_user(updated), "demo_plan": True})

    def demo_role(self, data):
        auth = self.session()
        if not auth: return self.send_json(401, {"error": "Please sign in."})
        if set(data) != {"role"} or data.get("role") not in ("Admin", "Operator", "Viewer"):
            return self.send_json(400, {"error": "Choose Admin, Operator or Viewer."})
        old_role, user = auth[0]["role"], auth[0]
        with db() as con:
            con.execute("UPDATE users SET role=? WHERE id=? AND workspace_id=?",
                        (data["role"], user["id"], user["workspace_id"]))
            updated = con.execute("""SELECT u.*, w.name workspace_name, w.plan, w.subscription_status
                FROM users u JOIN workspaces w ON w.id=u.workspace_id WHERE u.id=?""", (user["id"],)).fetchone()
        audit_event(user["workspace_id"], user["id"], "demo.role_switched", {"from":old_role,"to":data["role"]})
        return self.send_json(200, {"user": public_user(updated), "demo_mode": True})

    def create_checkout(self, data):
        auth = self.session()
        if not auth: return self.send_json(401, {"error": "Please sign in."})
        if auth[0]["role"] == "Viewer":
            audit_event(auth[0]["workspace_id"],auth[0]["id"],"access.denied.payment",{"role":"Viewer"})
            return self.send_json(403, {"error": "Viewer access is read-only; ask a workspace operator to manage billing."})
        if auth[0]["subscription_status"] != "ACTIVE":
            audit_event(auth[0]["workspace_id"],auth[0]["id"],"access.denied.payment",{"subscription_status":"SUSPENDED"})
            return self.send_json(403, {"error": "This workspace subscription is suspended."})
        if set(data) != {"months"} or isinstance(data.get("months"), bool) or data.get("months") not in (1, 3, 12):
            return self.send_json(400, {"error": "Choose an advance period of 1, 3 or 12 months."})
        user = auth[0]; months = data["months"]
        key_id, key_secret = os.environ.get("RAZORPAY_KEY_ID"), os.environ.get("RAZORPAY_KEY_SECRET")
        if not key_id or not key_secret:
            return self.send_json(503, {"error": "Secure payment checkout is not configured. Add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET on the server before accepting payments."})
        amount_minor = 99900 * months
        payload = {"amount": amount_minor, "currency": "INR", "accept_partial": False,
            "description": f"SyncShield Pro · {months} month{'s' if months != 1 else ''}",
            "customer": {"name": user["full_name"], "email": user["email"]},
            "notify": {"sms": False, "email": False}, "reminder_enable": False,
            "notes": {"plan": "Pro", "months": str(months)}}
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
            con.execute("INSERT INTO payment_intents(id,workspace_id,user_id,gateway,plan,months,amount_minor,currency,status) VALUES(?,?,?,?,?,?,?,?,?)",
                (payment_id,user["workspace_id"],user["id"],"Razorpay","Pro",months,amount_minor,"INR","CREATED"))
        audit_event(user["workspace_id"], user["id"], "payment.checkout_created", {"payment_id":payment_id,"months":months,"amount_minor":amount_minor,"currency":"INR"})
        return self.send_json(201, {"payment_id":payment_id,"checkout_url":provider["short_url"],
            "status":"CREATED","amount_minor":amount_minor,"currency":"INR","months":months})

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
            payment = con.execute("SELECT id,status,amount_minor,currency,months,created_at FROM payment_intents WHERE workspace_id=? AND status!='DEMO_PAID' ORDER BY created_at DESC LIMIT 1",(user["workspace_id"],)).fetchone()
        return self.send_json(200, {"service":"SyncShield","database":"healthy" if integrity=="ok" else "attention",
            "database_check":integrity,"tenant":user["workspace_name"],"runs":run_counts["total"],"failed_runs":run_counts["failed"] or 0,
            "latest_payment":dict(payment) if payment else None,
            "events":[{"id":e["id"],"event_type":e["event_type"],"details":json.loads(e["details_json"]),"created_at":e["created_at"],"actor":e["full_name"] or "System"} for e in events]})

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
            payment = con.execute("SELECT id,status,months,amount_minor,currency,created_at FROM payment_intents WHERE workspace_id=? AND status!='DEMO_PAID' ORDER BY created_at DESC LIMIT 1", (user["workspace_id"],)).fetchone()
        limit = PLAN_LIMITS.get(user["plan"], 3)
        return self.send_json(200, {"used": row[0] if row else 0, "limit": limit, "remaining": max(0,limit-(row[0] if row else 0)), "date": day, "plan": user["plan"], "latest_payment":dict(payment) if payment else None})


class DualStackHTTPServer(ThreadingHTTPServer):
    """Accept localhost connections over both IPv6 and IPv4 on Windows/macOS/Linux."""
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


if __name__ == "__main__":
    try:
        http_server = DualStackHTTPServer(("::", PORT), Handler)
    except OSError as exc:
        # Keep local development usable on hosts where IPv6 is disabled.
        print(f"IPv6 bind unavailable ({exc}); falling back to IPv4.", flush=True)
        http_server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"SyncShield ready at http://localhost:{PORT}", flush=True)
    http_server.serve_forever()





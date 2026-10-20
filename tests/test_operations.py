import os
import threading
import unittest
from unittest import mock
from pathlib import Path

_test_db = Path(__file__).resolve().parent.parent / "work" / "syncshield-tests.sqlite3"
_test_db.parent.mkdir(exist_ok=True)
os.environ["SYNCSHIELD_DB"] = str(_test_db)
import server
os.environ.pop("SYNCSHIELD_DB", None)

VALID = [
    {"record_id": "C01", "name": "Anu", "version": 2, "status": "ACTIVE"},
    {"record_id": "C02", "name": "Bala", "version": 1, "status": "ACTIVE"},
]

class OperationsTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        Path(server.DB).unlink(missing_ok=True)

    def setUp(self):
        Path(server.DB).unlink(missing_ok=True)
        with server.db() as con:
            con.executescript(server.SCHEMA)
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS runs_workspace_idempotency_idx ON runs(workspace_id,idempotency_key) WHERE idempotency_key IS NOT NULL")
            con.execute("INSERT INTO workspaces(id,name,plan,subscription_status) VALUES('tenant-a','Tenant A','Basic','ACTIVE')")
            con.execute("INSERT INTO users(id,workspace_id,full_name,email,password_hash) VALUES('user-a','tenant-a','Operator','a@example.test','fixture')")
        self.user = {"id":"user-a", "workspace_id":"tenant-a", "workspace_name":"Tenant A", "role":"Operator", "plan":"Basic", "subscription_status":"ACTIVE"}
        self.handler = object.__new__(server.Handler)
        self.handler.headers = {}
        self.handler.session = lambda: (self.user, "session-hash")
        self.handler.send_json = lambda code, payload, headers=None: (code, payload)

    def call(self, method, data, key=None):
        self.handler.headers = {"Idempotency-Key": key} if key else {}
        return getattr(self.handler, method)(data)

    def test_root_health_route_is_anonymous_and_returns_service_status(self):
        self.handler.path = "/health"
        status, body = self.handler.do_GET()
        self.assertEqual(status, 200)
        self.assertEqual(body, {"status":"ok", "service":"SyncShield"})

    def test_ordered_migrations_are_recorded_and_safe_to_repeat(self):
        with server.db() as con:
            server.apply_migrations(con)
            first = con.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
            server.apply_migrations(con)
            second = con.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        self.assertEqual([r[0] for r in first], ["000_schema_migrations", "001_runs", "002_daily_usage", "003_workspace_baselines", "004_audit_events"])
        self.assertEqual([r[0] for r in second], [r[0] for r in first])

    def test_run_list_is_scoped_to_authenticated_workspace(self):
        self.call("validate", {"rows": VALID}, "private-run")
        with server.db() as con:
            con.execute("INSERT INTO workspaces(id,name) VALUES('tenant-b','Tenant B')")
            con.execute("INSERT INTO users(id,workspace_id,full_name,email,password_hash) VALUES('user-b','tenant-b','Other','b@example.test','fixture')")
        self.user.update(id="user-b", workspace_id="tenant-b", workspace_name="Tenant B")
        status, result = self.handler.list_runs((self.user, "session-b"))
        self.assertEqual(status, 200)
        self.assertEqual(result["runs"], [])
    def test_demo_plan_switch_is_persisted_to_authenticated_workspace(self):
        status, body = self.call("set_plan", {"plan":"Pro"})
        self.assertEqual(status, 200)
        self.assertEqual(body["user"]["plan"], "Pro")
        with server.db() as con:
            self.assertEqual(con.execute("SELECT plan FROM workspaces WHERE id='tenant-a'").fetchone()[0], "Pro")

    def test_viewer_cannot_switch_workspace_demo_plan(self):
        self.user["role"] = "Viewer"
        status, _ = self.call("set_plan", {"plan":"Pro"})
        self.assertEqual(status, 403)
        with server.db() as con:
            self.assertEqual(con.execute("SELECT plan FROM workspaces WHERE id='tenant-a'").fetchone()[0], "Basic")

    def test_checkout_without_provider_credentials_does_not_fake_a_payment(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            status, body = self.call("create_checkout", {"months":3})
        self.assertEqual(status, 503)
        self.assertIn("not configured", body["error"])
        with server.db() as con:
            self.assertEqual(con.execute("SELECT plan FROM workspaces WHERE id='tenant-a'").fetchone()[0], "Basic")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM payment_intents WHERE workspace_id='tenant-a'").fetchone()[0], 0)

    def test_admin_diagnostics_are_denied_to_operator_and_available_to_admin(self):
        self.assertEqual(self.call("admin_diagnostics",(self.user,"session-hash"))[0],403)
        self.user["role"]="Admin"
        status, body = self.call("admin_diagnostics",(self.user,"session-hash"))
        self.assertEqual(status,200)
        self.assertEqual(body["database"],"healthy")
        self.assertIn("access.denied.admin_diagnostics",[e["event_type"] for e in body["events"]])

    def test_valid_fixture_and_expected_validation_counts(self):
        result = server.validate_rows(VALID)
        self.assertEqual((result["valid_rows"], result["invalid_rows"], result["issues"]), (2, 0, []))

    def test_four_rule_failures_are_reported_together(self):
        invalid = VALID + [
            {"record_id":"C01","name":"Anu","version":2,"status":"ACTIVE"},
            {"record_id":"X2","name":"","version":2,"status":"ACTIVE"},
            {"record_id":"X3","name":"Anu","version":0,"status":"ACTIVE"},
            {"record_id":"X4","name":"Anu","version":2,"status":"UNKNOWN"},
        ]
        result = server.validate_rows(invalid)
        self.assertEqual((result["valid_rows"], result["invalid_rows"]), (2, 4))
        self.assertEqual({issue["rule"] for issue in result["issues"]}, {"R1", "R2", "R3", "R4"})

    def test_basic_quota_is_atomic_and_fourth_run_is_rejected(self):
        for n in range(3):
            status, _ = self.call("validate", {"rows": VALID}, f"basic-{n}")
            self.assertEqual(status, 200)
        status, body = self.call("validate", {"rows": VALID}, "basic-fourth")
        self.assertEqual(status, 429)
        self.assertEqual(body["used"], 3)
        with server.db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 3)

    def test_replay_does_not_debit_and_changed_input_conflicts(self):
        first = self.call("validate", {"rows": VALID}, "repeat-key")
        replay = self.call("validate", {"rows": VALID}, "repeat-key")
        conflict = self.call("validate", {"rows": VALID[:1]}, "repeat-key")
        self.assertEqual(first[0], 200)
        self.assertEqual(replay[0], 200)
        self.assertTrue(replay[1]["replayed"])
        self.assertEqual(conflict[0], 409)
        with server.db() as con:
            self.assertEqual(con.execute("SELECT usage_count FROM daily_usage WHERE workspace_id='tenant-a'").fetchone()[0], 1)

    def test_two_simultaneous_same_key_requests_create_one_run(self):
        barrier=threading.Barrier(2)
        def submit():
            h=object.__new__(server.Handler); h.headers={"Idempotency-Key":"parallel-key"}
            h.session=lambda:(self.user,"parallel-session")
            h.send_json=lambda code,payload,headers=None:(code,payload)
            barrier.wait()
            return h.validate({"rows":VALID})
        results=[]
        threads=[threading.Thread(target=lambda:results.append(submit())) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual([r[0] for r in results],[200,200])
        self.assertEqual(sorted(r[1]["replayed"] for r in results),[False,True])
        with server.db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM runs").fetchone()[0],1)
            self.assertEqual(con.execute("SELECT usage_count FROM daily_usage WHERE workspace_id='tenant-a'").fetchone()[0],1)

    def test_concurrent_requests_for_last_quota_slot_accept_exactly_one(self):
        self.call("validate", {"rows":VALID}, "slot-one")
        self.call("validate", {"rows":VALID}, "slot-two")
        barrier=threading.Barrier(2)
        def submit(key):
            h=object.__new__(server.Handler); h.headers={"Idempotency-Key":key}
            h.session=lambda:(self.user,"slot-session")
            h.send_json=lambda code,payload,headers=None:(code,payload)
            barrier.wait()
            return h.validate({"rows":VALID})[0]
        results=[]
        threads=[threading.Thread(target=lambda k=k:results.append(submit(k))) for k in ("last-a","last-b")]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(sorted(results),[200,429])
        with server.db() as con:
            self.assertEqual(con.execute("SELECT usage_count FROM daily_usage WHERE workspace_id='tenant-a'").fetchone()[0],3)

    def test_dashboard_metrics_use_saved_workspace_runs(self):
        self.call("validate", {"rows":VALID}, "metrics-valid")
        invalid=VALID+[
            {"record_id":"C01","name":"Anu","version":2,"status":"ACTIVE"},
            {"record_id":"X2","name":"","version":2,"status":"ACTIVE"},
            {"record_id":"X3","name":"Anu","version":0,"status":"ACTIVE"},
            {"record_id":"X4","name":"Anu","version":2,"status":"UNKNOWN"},
        ]
        self.call("validate", {"rows":invalid}, "metrics-invalid")
        self.user["plan"]="Pro"
        self.call("process", {"rows":VALID}, "metrics-preview")
        status,data=self.handler.dashboard((self.user,"session"))
        self.assertEqual(status,200)
        m=data["metrics"]
        self.assertEqual((m["runs_today"],m["successful_runs"],m["failed_runs"],m["validation_errors"]),(3,3,0,4))
        self.assertEqual((m["rows_processed"],m["created"],m["updated"],m["unchanged"]),(10,1,1,0))
        self.assertEqual(m["validation_success_rate"],60.0)
        self.assertEqual(len(data["daily_runs"]),7)

    def test_preview_has_exact_create_update_summary_and_persists(self):
        self.user.update(plan="Pro")
        status, result = self.call("process", {"rows": VALID}, "preview-key")
        self.assertEqual(status, 201)
        self.assertEqual([(r["record_id"], r["action"]) for r in result["rows"]], [("C01","UPDATE"),("C02","CREATE")])
        self.assertEqual((result["input_rows"],result["created"],result["updated"],result["unchanged"]),(2,1,1,0))
        with server.db() as con:
            saved=con.execute("SELECT status,result_json FROM runs WHERE id=?",(result["run_id"],)).fetchone()
            self.assertEqual(saved["status"],"Completed")
            self.assertIn('"updated": 1',saved["result_json"])

    def test_suspended_workspace_cannot_submit(self):
        self.user["subscription_status"]="SUSPENDED"
        self.assertEqual(self.call("validate", {"rows":VALID})[0],403)
        self.user["plan"]="Pro"
        self.assertEqual(self.call("process", {"rows":VALID})[0],403)
        with server.db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM runs").fetchone()[0],0)

    def test_tenant_override_is_rejected(self):
        status, _ = self.call("validate", {"rows":VALID,"tenant_id":"tenant-b"})
        self.assertEqual(status,400)

    def test_bad_process_inputs_do_not_create_runs_or_debit_quota(self):
        self.user["plan"]="Pro"
        invalid=VALID+[{"record_id":"X","name":"","version":0,"status":"UNKNOWN"}]
        self.assertEqual(self.call("process", {"rows":invalid}, "bad-input")[0],400)
        self.assertEqual(self.call("process", {"rows":VALID,"workspace_id":"tenant-b"}, "override")[0],400)
        self.assertEqual(self.call("process", {"rows":VALID*6}, "too-many")[0],400)
        with server.db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM runs").fetchone()[0],0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM daily_usage").fetchone()[0],0)

    def test_unauthenticated_viewer_and_basic_plan_access(self):
        self.handler.session=lambda:None
        self.assertEqual(self.call("validate", {"rows":VALID})[0],401)
        self.handler.session=lambda:(self.user,"session")
        self.user["role"]="Viewer"
        self.assertEqual(self.call("validate", {"rows":VALID})[0],403)
        self.user["role"]="Operator"
        self.assertEqual(self.call("process", {"rows":VALID})[0],403)
        with server.db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM daily_usage").fetchone()[0],0)

    def test_cross_tenant_run_lookup_returns_not_found(self):
        self.user["plan"]="Pro"
        _, created=self.call("process", {"rows":VALID},"tenant-a-key")
        with server.db() as con:
            con.execute("INSERT INTO workspaces(id,name) VALUES('tenant-b','Tenant B')")
            con.execute("INSERT INTO users(id,workspace_id,full_name,email,password_hash) VALUES('user-b','tenant-b','Other','b@example.test','fixture')")
        self.user.update(id="user-b",workspace_id="tenant-b",plan="Basic")
        status, _ = self.handler.get_run((self.user,"session-b"),created["run_id"])
        self.assertEqual(status,404)

    def test_forced_failure_is_saved_and_replayed_without_second_debit(self):
        self.user["plan"]="Pro"
        os.environ["SYNCSHIELD_FORCE_PROCESS_FAILURE"]="1"
        try:
            status, failed=self.call("process", {"rows":VALID},"failure-key")
            replay_status, replay=self.call("process", {"rows":VALID},"failure-key")
        finally:
            os.environ.pop("SYNCSHIELD_FORCE_PROCESS_FAILURE",None)
        self.assertEqual((status,replay_status),(500,500))
        self.assertEqual(failed["run_id"],replay["run_id"])
        with server.db() as con:
            row=con.execute("SELECT status,error_code FROM runs WHERE id=?",(failed["run_id"],)).fetchone()
            self.assertEqual((row["status"],row["error_code"]),("Failed","PROCESSOR_FAILED"))
            self.assertEqual(con.execute("SELECT usage_count FROM daily_usage WHERE workspace_id='tenant-a'").fetchone()[0],1)

if __name__ == "__main__":
    unittest.main()


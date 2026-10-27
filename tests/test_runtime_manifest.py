import importlib.util
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import verify_runtime as verifier
from bind_roles import plan_bindings


class RuntimeManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.profile = {"roles": {"Explorer": {"model": "m", "effort": "max", "fallbacks": [], "active_index": 0}}}
        self.started = int(time.time())
        self.manifest = {"configuration_sha256": verifier.profile_hash(self.profile),
                         "started_at": self.started, "threads": {"Explorer": "t"}}

    def database(self, created, role="team-explorer"):
        with closing(sqlite3.connect(self.home / "state_5.sqlite")) as db:
            db.execute("CREATE TABLE threads (id,agent_role,created_at)")
            db.execute("INSERT INTO threads VALUES (?,?,?)", ("t", role, created))
            db.commit()

    def check(self):
        with patch.object(verifier, "inspect", return_value={"status": "pass"}):
            return verifier.verify(self.profile, self.manifest, self.home)

    def test_old_same_model_thread_cannot_pass(self):
        self.database(946684800)
        result = self.check()
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["checks"][0]["reason"], "thread_predates_this_test")

    def test_changed_configuration_cannot_pass(self):
        self.profile["roles"]["Explorer"]["effort"] = "low"
        self.assertEqual(self.check()["status"], "incomplete")

    def test_reusing_another_role_cannot_pass(self):
        self.database(self.started, "team-executor")
        self.assertEqual(self.check()["status"], "incomplete")

    def test_current_matching_role_passes(self):
        self.database(self.started)
        self.assertEqual(self.check()["status"], "pass")

    def test_native_role_is_verified_from_prepared_route(self):
        self.database(self.started, "explorer")
        self.manifest["expected_agent_types"] = {"Explorer": "explorer"}
        self.assertEqual(self.check()["status"], "pass")

    def test_different_route_does_not_pass_with_matching_model(self):
        self.database(self.started, "team-explorer")
        self.manifest["expected_agent_types"] = {"Explorer": "explorer"}
        self.assertEqual(self.check()["status"], "incomplete")

    def test_one_default_thread_cannot_prove_two_profiles(self):
        self.profile["roles"]["GeneralWorker"] = dict(self.profile["roles"]["Explorer"])
        self.manifest["configuration_sha256"] = verifier.profile_hash(self.profile)
        self.manifest["threads"]["GeneralWorker"] = "t"
        self.manifest["expected_agent_types"] = {"Explorer": "default", "GeneralWorker": "default"}
        self.database(self.started, "default")
        result = self.check()
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["checks"][1]["reason"], "thread_reused_for_another_role")


class RoleBindingTests(unittest.TestCase):
    def test_preserves_specialist_instructions_and_rebinds_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            path = home / "agents" / "reviewer.toml"
            path.parent.mkdir()
            original = 'name="special-reviewer"\ndescription="review"\ndeveloper_instructions="KEEP SPECIAL INSTRUCTIONS"\n'
            path.write_text(original, encoding="utf8")
            config = {"model": "m", "effort": "max", "fallbacks": [{"model": "b", "effort": "low"}], "active_index": 0}
            profile = {"roles": {name: dict(config) for name in ["Explorer", "Executor", "GeneralWorker", "Reviewer"]}}
            hosts = [{"host_role": "special-reviewer", "file": str(path), "mapped_role": "Reviewer"}]
            outputs = plan_bindings(home, profile, hosts)
            self.assertIn(original, outputs[path].decode())
            path.write_bytes(outputs[path])
            profile["roles"]["Reviewer"]["active_index"] = 1
            rebound = plan_bindings(home, profile, hosts)[path].decode()
            self.assertIn('model = "b"', rebound)
            self.assertIn(original, rebound)

    def test_existing_unmanaged_model_is_not_silently_changed(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            path = home / "existing.toml"
            path.write_text('name="existing"\nmodel="user-custom"\n', encoding="utf8")
            with self.assertRaises(ValueError):
                plan_bindings(home, {}, [{"host_role": "existing", "file": str(path), "mapped_role": "Reviewer"}])
            self.assertIn("user-custom", path.read_text())


if __name__ == "__main__":
    unittest.main()

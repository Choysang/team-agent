import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from external_catalog import discover


class ExternalDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.codex = Path(self.temp.name) / "codex"
        self.codex.mkdir()

    def catalog(self, models):
        (self.codex / "cc-switch-model-catalog.json").write_text(json.dumps({"models": models}), encoding="utf8")

    def test_missing_capabilities_not_invented(self):
        self.catalog([{"slug": "external"}])
        entry = discover(self.codex)["models"][0]
        self.assertEqual(entry["efforts"], [])
        self.assertEqual(entry["capability_status"], "unknown")
        self.assertEqual(entry["dispatch_status"], "discovered_not_verified_callable")

    def test_namespaced_providers(self):
        self.catalog([{"slug": "same", "provider": "one"}, {"slug": "same", "provider": "two"}])
        entries = discover(self.codex)["models"]
        self.assertEqual(len({entry["id"] for entry in entries}), 2)

    def test_error_is_visible(self):
        (self.codex / "cc-switch-model-catalog.json").write_text("invalid", encoding="utf8")
        result = discover(self.codex)
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(result["errors"])

    def test_configured_host_home_isolation(self):
        isolated = Path(self.temp.name) / "other" / "codex"
        self.catalog([{"slug": "external"}])
        self.assertEqual(discover(isolated)["status"], "absent")

    def test_readonly_database_no_secret_output(self):
        switch = self.codex.parent / ".cc-switch"
        switch.mkdir()
        database = switch / "cc-switch.db"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("CREATE TABLE providers (id,app_type,meta,settings_config)")
            settings = {"secret": "TEST_SECRET_MUST_NOT_LEAK", "modelCatalog": {"models": [
                {"model": "external", "reasoningLevels": ["high"]}]}}
            connection.execute("INSERT INTO providers VALUES (?,?,?,?)", ("p", "codex", "{}", json.dumps(settings)))
            connection.commit()
        before = database.read_bytes()
        result = discover(self.codex)
        self.assertEqual(database.read_bytes(), before)
        self.assertNotIn("TEST_SECRET_MUST_NOT_LEAK", json.dumps(result))
        self.assertEqual(result["models"][0]["efforts"], ["high"])


if __name__ == "__main__":
    unittest.main()

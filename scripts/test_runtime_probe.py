import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from runtime_probe import inspect


class RuntimeProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home / "sessions").mkdir()
        self.rollout = self.home / "sessions" / "probe.jsonl"
        with closing(sqlite3.connect(self.home / "state_5.sqlite")) as db:
            db.execute("CREATE TABLE threads (id, rollout_path, agent_path, model, reasoning_effort)")
            db.execute("INSERT INTO threads VALUES (?,?,?,?,?)",
                       ("probe", str(self.rollout), "/test-agent", "model-a", "max"))
            db.commit()

    def write(self, *events):
        meta = {"type": "session_meta", "payload": {"id": "probe"}}
        self.rollout.write_text("\n".join(json.dumps(item) for item in (meta, *events)), encoding="utf8")

    def context(self, model="model-a"):
        return {"type": "turn_context", "payload": {"model": model, "effort": "max", "turn_id": "turn"}}

    def test_actual_output(self):
        self.write(self.context(), {"type": "response_item", "payload": {"type": "agent_message", "content": "done"}})
        self.assertEqual(inspect(self.home, "probe", "model-a", "max")["status"], "pass")

    def test_configured_without_output(self):
        self.write(self.context())
        self.assertEqual(inspect(self.home, "probe", "model-a", "max")["status"], "unverified")

    def test_mismatch(self):
        self.write(self.context("model-b"))
        self.assertEqual(inspect(self.home, "probe", "model-a", "max")["status"], "mismatch")

    def test_unknown_thread(self):
        self.assertEqual(inspect(self.home, "other", "model-a", "max")["status"], "unverified")

    def test_self_report_is_not_evidence(self):
        self.write({"type": "response_item", "payload": {"role": "assistant", "content": "I am model-a max"}})
        self.assertEqual(inspect(self.home, "probe", "model-a", "max")["status"], "unverified")

    def test_partial_log(self):
        self.rollout.write_text('{"type":', encoding="utf8")
        self.assertEqual(inspect(self.home, "probe", "model-a", "max")["status"], "unverified")

    def test_output_before_context(self):
        self.write({"type": "response_item", "payload": {"role": "assistant", "content": "done"}}, self.context())
        self.assertEqual(inspect(self.home, "probe", "model-a", "max")["status"], "unverified")

    def test_missing_effort(self):
        context = self.context()
        del context["payload"]["effort"]
        self.write(context)
        self.assertEqual(inspect(self.home, "probe", "model-a", "max")["status"], "unverified")

    def test_missing_turn_id(self):
        context = self.context()
        del context["payload"]["turn_id"]
        self.write(context)
        self.assertEqual(inspect(self.home, "probe", "model-a", "max")["status"], "unverified")

    def test_wrong_thread_identity(self):
        self.write({"type": "session_meta", "payload": {"id": "other"}}, self.context())
        self.assertEqual(inspect(self.home, "probe", "model-a", "max")["status"], "unverified")


if __name__ == "__main__":
    unittest.main()

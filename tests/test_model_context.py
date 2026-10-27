from __future__ import annotations

from copy import deepcopy
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from model_context import observe


class ContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.host = root / "host"
        self.state = root / "state"
        self.host.mkdir()
        self.original = b'\xef\xbb\xbf# keep this comment\r\nmodel = "parent"\r\n[agents]\r\ndefault_subagent_model = "parent"\r\n'
        (self.host / "config.toml").write_bytes(self.original)
        self.models = [
            {"slug": "luna", "visibility": "list", "context_window": 272000,
             "max_context_window": 872000, "effective_context_window_percent": 95,
             "supported_reasoning_levels": [{"effort": "max"}], "other": {"keep": True}},
            {"slug": "sol", "visibility": "list", "context_window": 500000,
             "max_context_window": 872000, "effective_context_window_percent": 95,
             "supported_reasoning_levels": [{"effort": "max"}]},
        ]
        self.cache()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def cache(self) -> None:
        (self.host / "models_cache.json").write_text(json.dumps({"models": self.models}), encoding="utf8")

    def run_cli(self, *args: str) -> tuple[int, dict]:
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPTS / "team_adapter.py"),
             "--state-dir", str(self.state), "--codex-home", str(self.host), *args],
            capture_output=True, text=True, encoding="utf8", check=False,
        )
        self.assertEqual(result.stderr, "")
        return result.returncode, json.loads(result.stdout)

    def test_default_does_not_create_context_settings(self) -> None:
        code, data = self.run_cli("context")
        self.assertEqual(code, 0, data)
        self.assertEqual(data["status"], "default")
        self.assertFalse(data["automatic_prompt"])
        self.assertEqual((self.host / "config.toml").read_bytes(), self.original)
        self.assertFalse((self.state / "context-overrides.json").exists())
        self.assertFalse((self.state / "context-models.json").exists())

    def test_subagent_request_never_changes_host_window(self) -> None:
        for scope in ([], ["--scope", "subagent"]):
            code, data = self.run_cli("context-set", "--model", "luna", "--window", "800000", *scope)
            self.assertEqual(code, 1)
            self.assertEqual(data["status"], "unsupported")
            self.assertFalse(data["host_settings_changed"])
            self.assertEqual((self.host / "config.toml").read_bytes(), self.original)
            self.assertFalse((self.state / "context-overrides.json").exists())

    def test_luna_notice_is_a_host_scope_suggestion_without_config_write(self) -> None:
        self.models[0]["slug"] = "gpt-6-luna"
        self.cache()
        code, data = self.run_cli("onboarding", "--dispatch-models", "gpt-6-luna")
        self.assertEqual(code, 0, data)
        suggestions = data["context"]["recommendations"]
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["suggested_window"], 800000)
        self.assertEqual(suggestions[0]["current_configured_window"], 272000)
        self.assertEqual(suggestions[0]["scope"], "host_model_root_and_subagent_sessions")
        self.assertTrue(suggestions[0]["requires_user_selection"])
        self.assertEqual((self.host / "config.toml").read_bytes(), self.original)
        self.assertFalse((self.state / "context-overrides.json").exists())

    def test_luna_notice_disappears_after_explicit_host_configuration(self) -> None:
        self.models[0]["slug"] = "gpt-6-luna"
        self.cache()
        code, data = self.run_cli("context-set", "--scope", "host-model", "--model", "gpt-6-luna", "--window", "800000")
        self.assertEqual(code, 0, data)
        _, data = self.run_cli("context", "--model", "gpt-6-luna")
        self.assertEqual(data["recommendations"], [])
        self.assertFalse(data["automatic_prompt"])
        self.assertEqual(data["runtime_status"], "unverified_without_thread_telemetry")

    def test_luna_notice_requires_known_sufficient_limit_and_smaller_window(self) -> None:
        self.models[0]["slug"] = "gpt-6-luna"
        for maximum, window in ((None, 272000), (700000, 272000), (872000, 850000)):
            with self.subTest(maximum=maximum, window=window):
                self.models[0]["max_context_window"] = maximum
                self.models[0]["context_window"] = window
                self.cache()
                _, data = self.run_cli("context", "--model", "gpt-6-luna")
                self.assertEqual(data["recommendations"], [])

    def test_luna_notice_does_not_bypass_manual_host_conflicts(self) -> None:
        self.models[0]["slug"] = "gpt-6-luna"
        self.cache()
        (self.host / "config.toml").write_bytes(b'model_context_window = 500000\n')
        _, data = self.run_cli("context", "--model", "gpt-6-luna")
        self.assertTrue(data["issues"])
        self.assertEqual(data["recommendations"], [])

    def test_onboarding_checks_four_dimensions_with_real_dispatch_intersection(self) -> None:
        self.models[0]["service_tiers"] = [{"id": "priority", "name": "Fast"}]
        self.cache()
        code, data = self.run_cli("onboarding", "--dispatch-models", "luna,not-in-catalog")
        self.assertEqual(code, 0, data)
        check = data["first_use_check"]
        self.assertEqual(check["dimensions"], ["model", "reasoning_effort", "fast", "context_window"])
        self.assertFalse(check["existing_configuration"])
        self.assertEqual(check["selectable_models"], ["luna"])
        self.assertEqual(data["models"][0]["supported_reasoning_levels"], [{"effort": "max"}])
        self.assertTrue(data["models"][0]["fast_advertised"])
        self.assertIsNone(data["models"][1]["fast_advertised"])
        self.assertFalse(data["fast"]["per_subagent_override_supported"])
        self.assertEqual((self.host / "config.toml").read_bytes(), self.original)
        self.assertFalse((self.state / "context-overrides.json").exists())

    def test_onboarding_does_not_guess_callable_models_without_dispatch_enum(self) -> None:
        _, data = self.run_cli("onboarding")
        self.assertFalse(data["first_use_check"]["dispatch_enum_supplied"])
        self.assertEqual(data["first_use_check"]["selectable_models"], [])
        self.assertTrue(all(item["dispatch_supported"] is None for item in data["models"]))

    def test_only_requested_model_changes_and_reset_restores_config_bytes(self) -> None:
        code, data = self.run_cli("context-set", "--scope", "host-model", "--model", "luna", "--window", "800000")
        self.assertEqual(code, 0, data)
        expected = deepcopy(self.models)
        expected[0]["context_window"] = 800000
        catalog = json.loads((self.state / "context-models.json").read_text(encoding="utf8"))
        self.assertEqual(catalog["models"], expected)
        self.assertEqual(json.loads((self.host / "models_cache.json").read_text(encoding="utf8"))["models"], self.models)
        config = tomllib.loads((self.host / "config.toml").read_text(encoding="utf-8-sig"))
        self.assertNotIn("model_context_window", config)
        self.assertNotIn("model_auto_compact_token_limit", config)
        self.assertEqual(config["agents"], {"default_subagent_model": "parent"})
        _, report = self.run_cli("context", "--model", "luna")
        self.assertEqual(report["models"][0]["effective_window_estimate"], 760000)
        code, reset = self.run_cli("context-reset", "--model", "luna")
        self.assertEqual(code, 0, reset)
        self.assertEqual((self.host / "config.toml").read_bytes(), self.original)

    def test_refresh_keeps_other_models_current_and_does_not_drop_new_models(self) -> None:
        self.run_cli("context-set", "--scope", "host-model", "--model", "luna", "--window", "800000")
        self.models[1]["context_window"] = 600000
        added = deepcopy(self.models[1])
        added["slug"] = "new-model"
        self.models.append(added)
        self.cache()
        code, data = self.run_cli("context")
        self.assertEqual(code, 0, data)
        actual = json.loads((self.state / "context-models.json").read_text(encoding="utf8"))["models"]
        expected = deepcopy(self.models)
        expected[0]["context_window"] = 800000
        self.assertEqual(actual, expected)

    def test_reset_last_override_does_not_require_a_model_cache(self) -> None:
        self.run_cli("context-set", "--scope", "host-model", "--model", "luna", "--window", "800000")
        (self.host / "models_cache.json").unlink()
        code, data = self.run_cli("context-reset", "--model", "luna")
        self.assertEqual(code, 0, data)
        self.assertEqual((self.host / "config.toml").read_bytes(), self.original)

    def test_invalid_or_unknown_model_does_not_write_config(self) -> None:
        for model, value in [("luna", "900000"), ("luna", "0"), ("luna", "-1"), ("missing", "800000")]:
            with self.subTest(model=model, value=value):
                code, data = self.run_cli("context-set", "--scope", "host-model", "--model", model, "--window", value)
                self.assertNotEqual(code, 0, data)
                self.assertEqual((self.host / "config.toml").read_bytes(), self.original)
                self.assertFalse((self.state / "context-overrides.json").exists())

    def test_existing_global_or_manual_catalog_requires_reconciliation(self) -> None:
        for setting in ['model_context_window = 300000\n', 'model_catalog_json = "manual.json"\n']:
            with self.subTest(setting=setting):
                original = setting.encode() + self.original[3:]
                (self.host / "config.toml").write_bytes(original)
                code, data = self.run_cli("context-set", "--scope", "host-model", "--model", "luna", "--window", "800000")
                self.assertNotEqual(code, 0, data)
                self.assertEqual((self.host / "config.toml").read_bytes(), original)

    def test_setting_one_model_does_not_erase_another_override(self) -> None:
        self.run_cli("context-set", "--scope", "host-model", "--model", "luna", "--window", "800000")
        code, data = self.run_cli("context-set", "--scope", "host-model", "--model", "sol", "--window", "700000")
        self.assertEqual(code, 0, data)
        self.run_cli("context-reset", "--model", "luna")
        state = json.loads((self.state / "context-overrides.json").read_text(encoding="utf8"))
        self.assertEqual(state["models"], {"sol": 700000})
        self.assertIn("model_catalog_json", tomllib.loads((self.host / "config.toml").read_text(encoding="utf-8-sig")))

    def telemetry(self, events: list[dict]) -> None:
        path = self.host / "sessions" / "probe.jsonl"
        path.parent.mkdir()
        path.write_text("\n".join(json.dumps(item) for item in events) + "\n", encoding="utf8")
        with closing(sqlite3.connect(self.host / "state_5.sqlite")) as db:
            db.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT, model TEXT)")
            db.execute("INSERT INTO threads VALUES (?, ?, ?)", ("probe", str(path), "luna"))
            db.commit()

    def test_context_observation_requires_correct_session_and_current_model(self) -> None:
        self.telemetry([
            {"type": "session_meta", "payload": {"id": "probe"}},
            {"type": "turn_context", "payload": {"model": "luna", "turn_id": "t1"}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {"model_context_window": 760000}}},
        ])
        report = observe(self.host, "probe")
        self.assertEqual(report["status"], "observed")
        self.assertEqual(report["reported_context_window"], 760000)
        self.assertNotIn("long_input_passed", report)

    def test_old_capacity_does_not_prove_a_new_turn(self) -> None:
        self.telemetry([
            {"type": "session_meta", "payload": {"id": "probe"}},
            {"type": "turn_context", "payload": {"model": "luna", "turn_id": "t1"}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {"model_context_window": 760000}}},
            {"type": "turn_context", "payload": {"model": "luna", "turn_id": "t2"}},
        ])
        self.assertEqual(observe(self.host, "probe")["status"], "unverified")

    def test_cli_context_observation_rejects_a_different_model(self) -> None:
        self.telemetry([
            {"type": "session_meta", "payload": {"id": "probe"}},
            {"type": "turn_context", "payload": {"model": "luna", "turn_id": "t1"}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {"model_context_window": 258400}}},
        ])
        code, data = self.run_cli("context", "--model", "sol", "--thread-id", "probe")
        self.assertEqual(code, 1)
        self.assertEqual(data["observation"]["status"], "mismatch")
        self.assertEqual(data["observation"]["reason"], "requested_model_mismatch")

    def test_missing_thread_model_stays_unverified(self) -> None:
        self.telemetry([
            {"type": "session_meta", "payload": {"id": "probe"}},
            {"type": "turn_context", "payload": {"model": "luna", "turn_id": "t1"}},
        ])
        with closing(sqlite3.connect(self.host / "state_5.sqlite")) as db:
            db.execute("UPDATE threads SET model=NULL WHERE id='probe'")
            db.commit()
        code, data = self.run_cli("context", "--model", "luna", "--thread-id", "probe")
        self.assertEqual(code, 1)
        self.assertEqual(data["observation"]["status"], "unverified")
        self.assertEqual(data["observation"]["reason"], "thread_model_unavailable")

    @unittest.skipUnless(os.name == "nt", "Windows extended-length path")
    def test_windows_extended_path_spelling_is_not_an_outside_rollout(self) -> None:
        self.telemetry([
            {"type": "session_meta", "payload": {"id": "probe"}},
            {"type": "turn_context", "payload": {"model": "luna", "turn_id": "t1"}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {"model_context_window": 258400}}},
        ])
        path = self.host / "sessions" / "probe.jsonl"
        with closing(sqlite3.connect(self.host / "state_5.sqlite")) as db:
            db.execute("UPDATE threads SET rollout_path=? WHERE id='probe'", ("\\\\?\\" + str(path),))
            db.commit()
        code, data = self.run_cli("context", "--model", "luna", "--thread-id", "probe")
        self.assertEqual(code, 0, data)
        self.assertEqual(data["observation"]["status"], "observed")


if __name__ == "__main__":
    unittest.main()

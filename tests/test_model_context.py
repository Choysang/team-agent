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

    def legacy_catalog(self, *, active: bool = True) -> None:
        self.state.mkdir(exist_ok=True)
        (self.state / "context-overrides.json").write_text(json.dumps({
            "schema_version": 1, "codex_home": str(self.host.resolve()),
            "models": {"luna": 800000},
        }), encoding="utf8")
        old = deepcopy(self.models)
        old[0]["context_window"] = 800000
        (self.state / "context-models.json").write_text(json.dumps({"models": old}), encoding="utf8")
        pointer = 'model_catalog_json = ' + json.dumps((self.state / "context-models.json").as_posix())
        header = '# team-agent context catalog:start\r\n' + ('' if active else '#') + pointer + '\r\n# team-agent context catalog:end\r\n'
        (self.host / "config.toml").write_bytes(b'\xef\xbb\xbf' + header.encode() + self.original[3:])

    def test_disabled_legacy_catalog_does_not_block_new_model_discovery(self) -> None:
        self.legacy_catalog(active=False)
        before = (self.host / "config.toml").read_bytes()
        self.models.append({**deepcopy(self.models[1]), "slug": "gpt-6.1-terra"})
        self.cache()
        code, data = self.run_cli("models", "--dispatch-models", "gpt-6.1-terra")
        self.assertEqual(code, 0, data)
        self.assertIn("gpt-6.1-terra", [row["slug"] for row in data["models"]])
        self.assertEqual((self.host / "config.toml").read_bytes(), before)

    def test_read_commands_do_not_rewrite_or_reactivate_legacy_catalogs(self) -> None:
        self.legacy_catalog()
        catalog_path = self.state / "context-models.json"
        before = catalog_path.read_bytes()
        self.models.append({**deepcopy(self.models[1]), "slug": "gpt-6.1-terra"})
        self.cache()
        code, data = self.run_cli("context")
        self.assertEqual(code, 0, data)
        self.assertEqual(catalog_path.read_bytes(), before)
        self.assertEqual(data["status"], "legacy_static_catalog")
        self.assertTrue(data["blocks_remote_model_discovery"])

    def test_saved_window_is_not_reported_as_active_after_pointer_is_disabled(self) -> None:
        self.legacy_catalog(active=False)
        code, data = self.run_cli("context", "--model", "luna")
        self.assertEqual(code, 0, data)
        self.assertEqual(data["status"], "inactive")
        self.assertEqual(data["models"][0]["requested_window"], 800000)
        self.assertIsNone(data["models"][0]["configured_window"])
        self.assertEqual(data["models"][0]["effective_window_estimate"], 258400)

    def test_unreadable_legacy_preferences_do_not_block_native_model_listing(self) -> None:
        self.legacy_catalog(active=False)
        state = self.state / "context-overrides.json"
        state.write_text("{broken", encoding="utf8")
        for command in ("models", "onboarding"):
            code, data = self.run_cli(command)
            self.assertEqual(code, 0, data)
            self.assertTrue(data["context"]["issues"])
            self.assertEqual(state.read_text(encoding="utf8"), "{broken")

    def test_retirement_does_not_need_readable_legacy_preferences(self) -> None:
        self.legacy_catalog()
        state = self.state / "context-overrides.json"
        state.write_text("{broken", encoding="utf8")
        code, data = self.run_cli("context-retire")
        self.assertEqual(code, 0, data)
        self.assertEqual((self.host / "config.toml").read_bytes(), self.original)
        self.assertEqual(state.read_text(encoding="utf8"), "{broken")

    def test_broken_legacy_header_does_not_block_read_only_commands(self) -> None:
        self.legacy_catalog(active=False)
        config_path = self.host / "config.toml"
        text = config_path.read_text(encoding="utf-8-sig").replace('# team-agent context catalog:end', '# missing end')
        config_path.write_text(text, encoding="utf8")
        from team_adapter import ROLE_NAMES
        profile = {"schema_version": 2, "roles": {
            name: {"model": "luna", "effort": "max", "fallbacks": [], "active_index": 0}
            for name in ROLE_NAMES
        }}
        (self.state / "profile.json").write_text(json.dumps(profile), encoding="utf8")
        before = config_path.read_bytes()
        for args in (("models",), ("onboarding",), ("resolve", "--role", "Explorer")):
            code, data = self.run_cli(*args)
            self.assertEqual(code, 0, data)
            self.assertTrue(data["context"]["issues"])
            self.assertEqual(config_path.read_bytes(), before)
        code, data = self.run_cli("context-retire")
        self.assertEqual(code, 1, data)
        self.assertEqual(config_path.read_bytes(), before)

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

    def test_luna_does_not_offer_unsupported_static_catalog_activation(self) -> None:
        self.models[0]["slug"] = "gpt-6-luna"
        self.cache()
        code, data = self.run_cli("onboarding", "--dispatch-models", "gpt-6-luna")
        self.assertEqual(code, 0, data)
        self.assertEqual(data["context"]["recommendations"], [])
        self.assertFalse(data["context"]["per_model_override_supported"])
        self.assertEqual((self.host / "config.toml").read_bytes(), self.original)
        self.assertFalse((self.state / "context-overrides.json").exists())

    def test_host_scope_cannot_enable_static_catalog_even_with_explicit_window(self) -> None:
        code, data = self.run_cli("context-set", "--scope", "host-model", "--model", "luna", "--window", "800000")
        self.assertEqual(code, 1, data)
        self.assertEqual(data["status"], "unsupported")
        self.assertEqual(data["reason"], "static_model_catalog_disables_remote_model_discovery")
        self.assertFalse(data["host_settings_changed"])
        self.assertEqual((self.host / "config.toml").read_bytes(), self.original)
        self.assertFalse((self.state / "context-models.json").exists())
        self.assertFalse((self.state / "context-overrides.json").exists())

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

    def test_retirement_restores_only_managed_config_and_keeps_preferences(self) -> None:
        self.legacy_catalog()
        state_path = self.state / "context-overrides.json"
        saved = state_path.read_bytes()
        cache_before = (self.host / "models_cache.json").read_bytes()
        code, data = self.run_cli("context-retire")
        self.assertEqual(code, 0, data)
        self.assertTrue(data["changed"])
        self.assertTrue(data["stored_preferences_retained"])
        self.assertEqual((self.host / "config.toml").read_bytes(), self.original)
        self.assertEqual(state_path.read_bytes(), saved)
        self.assertEqual((self.host / "models_cache.json").read_bytes(), cache_before)
        code, repeated = self.run_cli("context-retire")
        self.assertEqual(code, 0, repeated)
        self.assertFalse(repeated["changed"])

    def test_native_cache_updates_visible_without_context_writes(self) -> None:
        self.legacy_catalog(active=False)
        old_catalog = (self.state / "context-models.json").read_bytes()
        self.models[1]["context_window"] = 600000
        self.models.append({**deepcopy(self.models[1]), "slug": "gpt-6.1-terra"})
        self.cache()
        code, data = self.run_cli("context")
        self.assertEqual(code, 0, data)
        rows = {item["model"]: item for item in data["models"]}
        self.assertEqual(rows["sol"]["catalog_default_window"], 600000)
        self.assertIn("gpt-6.1-terra", rows)
        self.assertEqual((self.state / "context-models.json").read_bytes(), old_catalog)

    def test_successive_unseen_models_remain_visible_without_reconfiguration(self) -> None:
        self.legacy_catalog(active=False)
        from team_adapter import ROLE_NAMES
        profile = {"schema_version": 2, "skip_fallback_prompts": False, "roles": {
            name: {"model": "luna", "effort": "max", "fallbacks": [], "active_index": 0}
            for name in ROLE_NAMES
        }}
        profile_path = self.state / "profile.json"
        profile_path.write_text(json.dumps(profile), encoding="utf8")
        preserved = {path: path.read_bytes() for path in (
            self.host / "config.toml", self.state / "context-models.json", profile_path,
        )}
        code, baseline = self.run_cli("models")
        self.assertEqual(code, 0, baseline)
        self.assertEqual(baseline["catalog_notice"]["status"], "baseline_established")

        # Arbitrary identifiers exercise future releases, without a Terra exception.
        for slug in ("unseen-release-one", "unseen-release-two"):
            self.models.append({**deepcopy(self.models[1]), "slug": slug})
            self.cache()
            cache_bytes = (self.host / "models_cache.json").read_bytes()
            dispatch_slugs = {item["slug"] for item in self.models if item["slug"] != "sol"}
            dispatch = ",".join(sorted(dispatch_slugs | {"dispatch-only-id"}))
            code, listing = self.run_cli("models", "--dispatch-models", dispatch)
            self.assertEqual(code, 0, listing)
            self.assertEqual([item["slug"] for item in listing["models"]],
                             [item["slug"] for item in self.models])
            self.assertTrue(next(item for item in listing["models"] if item["slug"] == slug)["dispatch_supported"])
            self.assertFalse(next(item for item in listing["models"] if item["slug"] == "sol")["dispatch_supported"])
            self.assertEqual(set(listing["first_use_check"]["selectable_models"]), dispatch_slugs)
            notice = listing["catalog_notice"]
            self.assertEqual(notice["status"], "pending_change")
            self.assertEqual([item["slug"] for item in notice["pending"][0]["changes"]["added_models"]], [slug])
            self.assertEqual(notice["current_roles"], profile["roles"])

            code, ack = self.run_cli("catalog-ack", "--fingerprint", notice["fingerprint"], "--decision", "defer")
            self.assertEqual(code, 0, ack)
            code, repeated = self.run_cli("models", "--dispatch-models", dispatch)
            self.assertEqual(code, 0, repeated)
            self.assertEqual(repeated["catalog_notice"]["status"], "unchanged")
            self.assertEqual((self.host / "models_cache.json").read_bytes(), cache_bytes)
            for path, original in preserved.items():
                self.assertEqual(path.read_bytes(), original)

    def test_reset_last_override_does_not_require_a_model_cache(self) -> None:
        self.legacy_catalog()
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

    def test_reset_preserves_other_saved_preferences_without_reactivating_catalog(self) -> None:
        self.legacy_catalog()
        p = self.state / "context-overrides.json"
        state = json.loads(p.read_text(encoding="utf8"))
        state["models"]["sol"] = 700000
        p.write_text(json.dumps(state), encoding="utf8")
        code, data = self.run_cli("context-reset", "--model", "luna")
        self.assertEqual(code, 0, data)
        self.assertEqual(json.loads(p.read_text(encoding="utf8"))["models"], {"sol": 700000})
        self.assertEqual(data["remaining_preferences_inactive"], ["sol"])
        self.assertEqual((self.host / "config.toml").read_bytes(), self.original)

    def test_retirement_never_removes_a_manual_catalog(self) -> None:
        self.legacy_catalog()
        custom = b'model_catalog_json = "manual.json"\n'
        (self.host / "config.toml").write_bytes(custom)
        saved = (self.state / "context-overrides.json").read_bytes()
        code, data = self.run_cli("context-retire")
        self.assertEqual(code, 1, data)
        self.assertEqual((self.host / "config.toml").read_bytes(), custom)
        self.assertEqual((self.state / "context-overrides.json").read_bytes(), saved)

    def test_retirement_of_commented_pointer_never_reenables_it(self) -> None:
        self.legacy_catalog(active=False)
        code, data = self.run_cli("context-retire")
        self.assertEqual(code, 0, data)
        self.assertEqual((self.host / "config.toml").read_bytes(), self.original)

    def test_retirement_refuses_a_matching_pointer_outside_the_managed_header(self) -> None:
        self.legacy_catalog(active=False)
        config_path = self.host / "config.toml"
        text = config_path.read_text(encoding="utf-8-sig")
        outside = 'model_catalog_json = ' + json.dumps((self.state / "context-models.json").as_posix()) + '\n'
        text = text.replace('# keep this comment', outside + '# keep this comment')
        config_path.write_text(text, encoding="utf-8")
        before = config_path.read_bytes()
        code, data = self.run_cli("context-retire")
        self.assertEqual(code, 1, data)
        self.assertEqual(config_path.read_bytes(), before)

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

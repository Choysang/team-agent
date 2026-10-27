from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import tomllib
from unittest.mock import patch

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "team_adapter.py"
ROLE_NAMES = (
    "Explorer", "Executor", "Reviewer", "ExpertAdvisor",
    "Researcher", "GeneralWorker", "Designer",
)


class AdapterRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.state = root / "state"
        self.codex = root / "测试用户" / "codex"
        self.codex.mkdir(parents=True)
        self.models = [
            self.model("gpt-a", ["max", "low"]),
            self.model("gpt-b", ["max"]),
            self.model("gpt-c", ["max"], visibility="hide"),
        ]
        self.write_cache()
        self.profile_path = root / "profile.json"
        self.write_profile()

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def model(slug: str, efforts: list[str], visibility: str = "list") -> dict:
        return {
            "slug": slug,
            "visibility": visibility,
            "supported_reasoning_levels": [
                {"effort": effort, "description": "test"} for effort in efforts
            ],
        }

    def write_cache(self) -> None:
        self.codex.mkdir(parents=True, exist_ok=True)
        (self.codex / "models_cache.json").write_text(
            json.dumps({"models": self.models, "fetched_at": "ignored"}),
            encoding="utf-8",
        )

    def write_profile(self) -> None:
        roles = {}
        for role in ROLE_NAMES:
            roles[role] = {
                "model": "gpt-a",
                "effort": "max",
                "fallbacks": [],
                "active_index": 0,
            }
        roles["Explorer"]["fallbacks"] = [{"model": "gpt-b", "effort": "max"}]
        self.profile_path.write_text(
            json.dumps({"schema_version": 2, "roles": roles}),
            encoding="utf-8",
        )

    def run_cli(self, *args: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        child_env = os.environ.copy()
        child_env.pop("PYTHONUTF8", None)
        child_env["PYTHONIOENCODING"] = "cp1252"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--state-dir", str(self.state),
                "--codex-home", str(self.codex),
                *args,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=child_env,
            check=False,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"stdout was not one JSON value: {result.stdout!r}; stderr={result.stderr!r}; {exc}")
        self.assertEqual(result.stderr, "")
        return result, payload

    def test_models_sync_verify_and_dynamic_resolve(self) -> None:
        agents = self.codex / "agents"
        agents.mkdir()
        (agents / "python-reviewer.toml").write_text(
            'name = "python-reviewer"\ndescription = "review"\ndeveloper_instructions = "review"\n',
            encoding="utf-8",
        )
        result, models = self.run_cli("models")
        self.assertEqual(result.returncode, 0)
        self.assertEqual([item["slug"] for item in models["models"]], ["gpt-a", "gpt-b"])

        result, synced = self.run_cli("sync", "--profile", str(self.profile_path))
        self.assertEqual(result.returncode, 0, synced)
        self.assertEqual(synced["runtime_status"], "runtime_unverified")
        for role in ROLE_NAMES:
            path = agents / f"team-{role.lower()}.toml"
            config = tomllib.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(config["model"], "gpt-a")
            self.assertNotIn("readonly", config)
            self.assertNotIn("fork_turns", config)
            self.assertNotIn("disallowed_tools", config)

        result, verified = self.run_cli("verify")
        self.assertEqual(result.returncode, 0, verified)
        self.assertEqual(verified["status"], "configured")
        self.assertEqual(verified["runtime_status"], "runtime_unverified")

        result, resolved = self.run_cli("resolve", "--role", "python-reviewer")
        self.assertEqual(result.returncode, 0, resolved)
        self.assertEqual(resolved["role"], "Reviewer")
        self.assertEqual(resolved["agent_type"], "python-reviewer")
        self.assertEqual(resolved["fork_turns"], "none")
        self.assertEqual(resolved["current_session_status"], "unverified")
        self.assertFalse(resolved["requires_reload"])
        self.assertTrue(resolved["coverage"]["total_host_roles"] >= 1)

        result, unknown = self.run_cli("resolve", "--role", "unlisted-role")
        self.assertEqual(result.returncode, 0, unknown)
        self.assertEqual(unknown["role"], "GeneralWorker")
        self.assertEqual(unknown["agent_type"], "default")
        self.assertTrue(unknown["fallback"])
        self.assertTrue(unknown["requires_explicit_model"])

        _, native_explorer = self.run_cli("resolve", "--role", "explorer")
        self.assertEqual(native_explorer["role"], "Explorer")
        self.assertEqual(native_explorer["agent_type"], "explorer")
        self.assertEqual(native_explorer["native_agent_type"], "explorer")
        self.assertTrue(native_explorer["requires_explicit_model"])
        self.assertIn(
            "native_builtin_ignores_same_named_toml_without_explicit_model",
            native_explorer["warnings"],
        )
        self.assertFalse(native_explorer["requires_reload"])

        _, worker = self.run_cli("resolve", "--role", "worker")
        self.assertEqual(worker["role"], "Executor")
        self.assertEqual(worker["agent_type"], "worker")
        self.assertEqual(worker["native_agent_type"], "worker")
        self.assertTrue(worker["requires_explicit_model"])
        self.assertFalse(worker["requires_reload"])
        _, default = self.run_cli("resolve", "--role", "default")
        self.assertEqual(default["role"], "GeneralWorker")
        self.assertEqual(default["agent_type"], "default")
        self.assertTrue(default["requires_explicit_model"])
        self.assertFalse(default["requires_reload"])

        _, team_explorer = self.run_cli("resolve", "--role", "Explorer")
        self.assertEqual(team_explorer["agent_type"], "explorer")
        self.assertEqual(team_explorer["native_agent_type"], "explorer")
        self.assertTrue(team_explorer["requires_explicit_model"])
        self.assertEqual(team_explorer["current_session_status"], "unverified")
        self.assertEqual(len(team_explorer["profile_revision"]), 64)
        self.assertFalse(team_explorer["skip_fallback_prompts"])

        (agents / "docs-lookup.toml").write_text(
            'name = "docs-lookup"\ndescription = "docs"\ndeveloper_instructions = "research"\n',
            encoding="utf-8",
        )
        (agents / "build-resolver.toml").write_text(
            'name = "build-resolver"\ndescription = "build"\ndeveloper_instructions = "execute"\n'
            'model = "gpt-b"\nmodel_reasoning_effort = "max"\n',
            encoding="utf-8",
        )
        _, docs = self.run_cli("resolve", "--role", "docs-lookup")
        self.assertEqual(docs["role"], "Researcher")
        _, build = self.run_cli("resolve", "--role", "build-resolver")
        self.assertEqual(build["role"], "Executor")
        self.assertEqual(build["host_override"]["model"], "gpt-b")
        self.assertIn("host_agent_has_explicit_model_or_effort_override", build["warnings"])

    def test_external_discovery_does_not_enter_callable_catalog(self) -> None:
        external_file = self.codex / "cc-switch-model-catalog.json"
        external_file.write_text(json.dumps({
            "models": [{
                "slug": "external-only",
                "provider": "test-provider",
                "host": "codex",
                "supported_reasoning_levels": [{"effort": "high"}],
            }]
        }), encoding="utf-8")

        result, models = self.run_cli("models")
        self.assertEqual(result.returncode, 0, models)
        self.assertEqual([item["slug"] for item in models["models"]], ["gpt-a", "gpt-b"])
        self.assertEqual(models["external_discovery"]["status"], "ok")
        self.assertEqual(
            [item["model"] for item in models["external_discovery"]["models"]],
            ["external-only"],
        )
        self.assertEqual(
            models["external_discovery"]["models"][0]["dispatch_status"],
            "discovered_not_verified_callable",
        )

        profile = json.loads(self.profile_path.read_text(encoding="utf-8"))
        profile["roles"]["Explorer"]["model"] = "external-only"
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        result, error = self.run_cli("sync", "--profile", str(self.profile_path))
        self.assertEqual(result.returncode, 1)
        self.assertIn("not visibility=list", error["error"])

    def test_empty_native_catalog_stays_unavailable_with_external_models(self) -> None:
        self.models = [self.model("hidden-only", ["high"], visibility="hide")]
        self.write_cache()
        (self.codex / "cc-switch-model-catalog.json").write_text(json.dumps({
            "models": [{
                "slug": "external-only",
                "provider": "test-provider",
                "host": "codex",
                "supported_reasoning_levels": [{"effort": "high"}],
            }]
        }), encoding="utf-8")

        result, payload = self.run_cli("models")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["models"], [])
        self.assertEqual(payload["external_discovery"]["models"][0]["model"], "external-only")

    def test_loaded_agent_types_are_evidence_for_resolve_and_verify(self) -> None:
        self.run_cli("sync", "--profile", str(self.profile_path))
        loaded = ",".join([
            "explorer", "worker", "default",
            *("team-" + role.lower() for role in ROLE_NAMES),
        ])
        _, resolved = self.run_cli(
            "resolve", "--role", "Explorer", "--loaded-agent-types", loaded
        )
        self.assertEqual(resolved["current_session_status"], "loaded")
        self.assertFalse(resolved["requires_reload"])
        self.assertEqual(resolved["loaded_agent_types_source"], "caller_supplied_current_session")
        _, native_resolved = self.run_cli(
            "resolve", "--role", "explorer", "--loaded-agent-types", loaded
        )
        self.assertEqual(native_resolved["current_session_status"], "loaded")
        self.assertEqual(native_resolved["agent_type"], "explorer")
        self.assertEqual(native_resolved["native_agent_type"], "explorer")
        self.assertTrue(native_resolved["requires_explicit_model"])
        self.assertFalse(native_resolved["requires_reload"])

        for native_role in (
            "worker",
            "default",
        ):
            _, routed = self.run_cli(
                "resolve", "--role", native_role, "--loaded-agent-types", loaded
            )
            self.assertEqual(routed["agent_type"], native_role)
            self.assertEqual(routed["native_agent_type"], native_role)
            self.assertTrue(routed["requires_explicit_model"])

        _, native_without_route = self.run_cli(
            "resolve", "--role", "worker", "--loaded-agent-types", "team-explorer"
        )
        self.assertEqual(native_without_route["agent_type"], "worker")
        self.assertEqual(native_without_route["current_session_status"], "not_loaded")
        self.assertTrue(native_without_route["requires_explicit_model"])

        _, explicit_team = self.run_cli(
            "resolve", "--role", "team-explorer", "--loaded-agent-types", loaded
        )
        self.assertEqual(explicit_team["agent_type"], "team-explorer")
        self.assertFalse(explicit_team["requires_explicit_model"])
        self.assertEqual(explicit_team["current_session_status"], "loaded")

        _, missing = self.run_cli(
            "resolve", "--role", "Explorer", "--loaded-agent-types", "team-executor"
        )
        self.assertEqual(missing["current_session_status"], "not_loaded")
        self.assertFalse(missing["requires_reload"])

        result, verified = self.run_cli("verify", "--loaded-agent-types", loaded)
        self.assertEqual(result.returncode, 0, verified)
        self.assertEqual(verified["runtime_status"], "runtime_loaded_roles_verified")
        self.assertTrue(all(item["runtime_loaded"] for item in verified["checks"]))
        self.assertEqual(len(verified["profile_revision"]), 64)
        self.assertEqual(verified["loaded_agent_types_source"], "caller_supplied_current_session")

        result, native_only = self.run_cli(
            "verify", "--loaded-agent-types", "default,worker,explorer"
        )
        self.assertEqual(result.returncode, 0, native_only)
        self.assertEqual(native_only["runtime_status"], "runtime_loaded_roles_verified")
        self.assertTrue(all(item["runtime_loaded"] for item in native_only["checks"]))
        self.assertEqual(
            {item["runtime_agent_type"] for item in native_only["checks"]},
            {"default", "worker", "explorer"},
        )

        result, incomplete = self.run_cli(
            "verify", "--loaded-agent-types", "team-explorer,team-executor"
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(incomplete["runtime_status"], "runtime_loaded_roles_incomplete")

    def test_logical_roles_prefer_native_and_available_specialists(self) -> None:
        agents = self.codex / "agents"
        agents.mkdir()
        for specialist in ("code-reviewer", "architect", "planner", "docs-lookup"):
            (agents / f"{specialist}.toml").write_text(
                f'name = "{specialist}"\ndescription = "available specialist"\n',
                encoding="utf-8",
            )
        result, synced = self.run_cli("sync", "--profile", str(self.profile_path))
        self.assertEqual(result.returncode, 0, synced)

        loaded = ",".join([
            "default", "worker", "explorer",
            *("team-" + role.lower() for role in ROLE_NAMES),
            "code-reviewer", "architect", "planner", "docs-lookup",
        ])
        expected = {
            "Explorer": ("explorer", "explorer", True),
            "Executor": ("worker", "worker", True),
            "GeneralWorker": ("default", "default", True),
            "Reviewer": ("code-reviewer", None, True),
            "ExpertAdvisor": ("architect", None, True),
            "Researcher": ("docs-lookup", None, True),
            "Designer": ("default", "default", True),
            "unknown-function": ("default", "default", True),
        }
        for role, (agent_type, native_agent_type, explicit_model) in expected.items():
            with self.subTest(role=role):
                result, resolved = self.run_cli(
                    "resolve", "--role", role, "--loaded-agent-types", loaded
                )
                self.assertEqual(result.returncode, 0, resolved)
                self.assertEqual(resolved["agent_type"], agent_type)
                self.assertEqual(resolved["native_agent_type"], native_agent_type)
                self.assertEqual(resolved["requires_explicit_model"], explicit_model)

        _, planner_fallback = self.run_cli(
            "resolve", "--role", "ExpertAdvisor",
            "--loaded-agent-types", "planner,default",
        )
        self.assertEqual(planner_fallback["agent_type"], "planner")
        self.assertEqual(planner_fallback["current_session_status"], "loaded")

    def test_loaded_specialists_do_not_require_local_templates(self) -> None:
        self.run_cli("sync", "--profile", str(self.profile_path))
        for role, agent_type in (
            ("Reviewer", "code-reviewer"),
            ("ExpertAdvisor", "architect"),
            ("Researcher", "docs-lookup"),
        ):
            with self.subTest(role=role):
                result, resolved = self.run_cli(
                    "resolve", "--role", role,
                    "--loaded-agent-types", f"{agent_type},default,explorer",
                )
                self.assertEqual(result.returncode, 0, resolved)
                self.assertEqual(resolved["agent_type"], agent_type)
                self.assertEqual(resolved["current_session_status"], "loaded")
                self.assertEqual(resolved["host_override"], {})
                self.assertTrue(resolved["requires_explicit_model"])
        _, explicit = self.run_cli(
            "resolve", "--role", "python-reviewer",
            "--loaded-agent-types", "python-reviewer,default",
        )
        self.assertEqual(explicit["agent_type"], "python-reviewer")
        self.assertEqual(explicit["role"], "Reviewer")
        self.assertEqual(explicit["current_session_status"], "loaded")

    def test_specialist_identity_comes_from_name_and_loaded_types(self) -> None:
        agents = self.codex / "agents"
        agents.mkdir()
        (agents / "code-reviewer.toml").write_text(
            'name = "custom-reviewer"\ndescription = "renamed reviewer"\n',
            encoding="utf-8",
        )
        (agents / "arbitrary-name.toml").write_text(
            'name = "architect"\ndescription = "real architect"\n'
            'model = "gpt-a"\nmodel_reasoning_effort = "max"\n',
            encoding="utf-8",
        )
        self.run_cli("sync", "--profile", str(self.profile_path))
        _, loaded = self.run_cli(
            "resolve", "--role", "Reviewer",
            "--loaded-agent-types", "code-reviewer,default",
        )
        self.assertEqual(loaded["agent_type"], "code-reviewer")
        self.assertEqual(loaded["host_override"], {})
        _, explicit_loaded = self.run_cli(
            "resolve", "--role", "code-reviewer",
            "--loaded-agent-types", "code-reviewer,default",
        )
        self.assertEqual(explicit_loaded["agent_type"], "code-reviewer")
        self.assertEqual(explicit_loaded["current_session_status"], "loaded")
        _, no_loaded_evidence = self.run_cli("resolve", "--role", "Reviewer")
        self.assertEqual(no_loaded_evidence["agent_type"], "default")
        _, architect = self.run_cli(
            "resolve", "--role", "ExpertAdvisor",
            "--loaded-agent-types", "architect,default",
        )
        self.assertEqual(architect["agent_type"], "architect")
        self.assertFalse(architect["requires_explicit_model"])

    def test_missing_or_unloaded_specialists_fall_back_but_explicit_specialists_remain(self) -> None:
        agents = self.codex / "agents"
        agents.mkdir()
        for specialist in ("code-reviewer", "python-reviewer"):
            (agents / f"{specialist}.toml").write_text(
                f'name = "{specialist}"\ndescription = "preserved specialist"\n'
                'developer_instructions = "Keep this specialist task."\n',
                encoding="utf-8",
            )
        result, synced = self.run_cli("sync", "--profile", str(self.profile_path))
        self.assertEqual(result.returncode, 0, synced)
        loaded_team_roles = ",".join("team-" + role.lower() for role in ROLE_NAMES)

        for role, native_type in (
            ("Reviewer", "default"),
            ("ExpertAdvisor", "default"),
            ("Researcher", "explorer"),
        ):
            with self.subTest(role=role):
                _, fallback = self.run_cli(
                    "resolve", "--role", role,
                    "--loaded-agent-types", loaded_team_roles,
                )
                self.assertEqual(fallback["agent_type"], native_type)
                self.assertTrue(fallback["requires_explicit_model"])

        _, explicit_candidate = self.run_cli(
            "resolve", "--role", "code-reviewer",
            "--loaded-agent-types", loaded_team_roles,
        )
        self.assertEqual(explicit_candidate["agent_type"], "code-reviewer")
        self.assertEqual(explicit_candidate["current_session_status"], "not_loaded")
        _, explicit_specialist = self.run_cli(
            "resolve", "--role", "python-reviewer",
            "--loaded-agent-types", loaded_team_roles,
        )
        self.assertEqual(explicit_specialist["role"], "Reviewer")
        self.assertEqual(explicit_specialist["agent_type"], "python-reviewer")
        self.assertEqual(explicit_specialist["current_session_status"], "not_loaded")

    def test_native_agent_filenames_match_resolve_even_with_conflicting_names(self) -> None:
        agents = self.codex / "agents"
        agents.mkdir()
        conflicts = {
            "default": ("reviewer", "GeneralWorker"),
            "worker": ("explorer", "Executor"),
            "explorer": ("reviewer", "Explorer"),
        }
        for stem, (name, _) in conflicts.items():
            (agents / f"{stem}.toml").write_text(
                f'name = "{name}"\ndescription = "host role"\n',
                encoding="utf-8",
            )

        spec = importlib.util.spec_from_file_location("team_adapter_native_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        for stem, (name, expected) in conflicts.items():
            self.assertEqual(module.infer_host_role(name, stem), expected)

        result, synced = self.run_cli("sync", "--profile", str(self.profile_path))
        self.assertEqual(result.returncode, 0, synced)
        _, native_coverage = self.run_cli("resolve", "--role", "default")
        host_roles = {
            Path(item["file"]).stem: item["mapped_role"]
            for item in native_coverage["coverage"]["host_roles"]
        }
        self.assertEqual(host_roles, {stem: expected for stem, (_, expected) in conflicts.items()})
        for native_role, expected in (
            ("default", "GeneralWorker"),
            ("worker", "Executor"),
            ("explorer", "Explorer"),
        ):
            _, resolved = self.run_cli("resolve", "--role", native_role)
            self.assertEqual(resolved["role"], expected)

    def test_preference_command_preserves_profile_and_role_override_wins(self) -> None:
        self.run_cli("sync", "--profile", str(self.profile_path))
        state_profile = self.state / "profile.json"
        payload = json.loads(state_profile.read_text(encoding="utf-8"))
        payload["custom_setting"] = {"keep": True}
        payload["roles"]["Explorer"]["skip_fallback_prompts"] = False
        state_profile.write_text(json.dumps(payload), encoding="utf-8")
        catalog_state = self.state / "catalog_state.json"
        catalog_before = catalog_state.read_bytes()

        _, models = self.run_cli("models")
        prefs = models["fallback_prompt_preferences"]
        self.assertFalse(prefs["global"])
        self.assertFalse(prefs["roles"]["Explorer"]["effective"])
        self.assertFalse(prefs["roles"]["Executor"]["effective"])

        result, global_update = self.run_cli(
            "preference", "--role", "all", "--skip-fallback-prompts", "true"
        )
        self.assertEqual(result.returncode, 0, global_update)
        _, resolved_explorer = self.run_cli("resolve", "--role", "Explorer")
        _, resolved_executor = self.run_cli("resolve", "--role", "Executor")
        self.assertFalse(resolved_explorer["skip_fallback_prompts"])
        self.assertTrue(resolved_executor["skip_fallback_prompts"])

        result, role_update = self.run_cli(
            "preference", "--role", "Explorer", "--skip-fallback-prompts", "true"
        )
        self.assertEqual(result.returncode, 0, role_update)
        _, resolved_explorer = self.run_cli("resolve", "--role", "Explorer")
        self.assertTrue(resolved_explorer["skip_fallback_prompts"])

        result, role_reset = self.run_cli(
            "preference", "--role", "Explorer", "--skip-fallback-prompts", "false"
        )
        self.assertEqual(result.returncode, 0, role_reset)
        _, resolved_explorer = self.run_cli("resolve", "--role", "Explorer")
        self.assertFalse(resolved_explorer["skip_fallback_prompts"])

        updated = json.loads(state_profile.read_text(encoding="utf-8"))
        self.assertEqual(updated["custom_setting"], {"keep": True})
        self.assertEqual(updated["roles"]["Explorer"]["model"], "gpt-a")
        self.assertEqual(updated["roles"]["Explorer"]["fallbacks"], [
            {"model": "gpt-b", "effort": "max"}
        ])
        self.assertEqual(updated["roles"]["Explorer"]["skip_fallback_prompts"], False)
        self.assertEqual(updated["skip_fallback_prompts"], True)
        self.assertEqual(catalog_state.read_bytes(), catalog_before)
        self.assertNotIn("skip_fallback_prompts", updated["roles"]["Executor"])

    def test_enabled_host_bindings_follow_sync_and_failover(self) -> None:
        agents = self.codex / "agents"
        agents.mkdir(parents=True)
        specialist = agents / "python-reviewer.toml"
        specialist.write_text(
            'name = "python-reviewer"\n'
            'description = "Keep this specialist description"\n'
            'developer_instructions = "Preserve these review instructions."\n',
            encoding="utf-8",
        )
        self.state.mkdir(parents=True)
        (self.state / "host-bindings.json").write_text(
            json.dumps({"enabled": True}), encoding="utf-8"
        )
        profile = json.loads(self.profile_path.read_text(encoding="utf-8"))
        profile["roles"]["Explorer"]["fallbacks"] = [
            {"model": "gpt-b", "effort": "max"}
        ]
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")

        result, synced = self.run_cli("sync", "--profile", str(self.profile_path))
        self.assertEqual(result.returncode, 0, synced)
        specialist_text = specialist.read_text(encoding="utf-8")
        self.assertIn("Preserve these review instructions.", specialist_text)
        self.assertIn("model = \"gpt-a\"", specialist_text)
        self.assertIn("model_reasoning_effort = \"max\"", specialist_text)
        self.assertIn("model = \"gpt-a\"", (agents / "explorer.toml").read_text(encoding="utf-8"))

        result, rebound = self.run_cli("sync", "--profile", str(self.profile_path))
        self.assertEqual(result.returncode, 0, rebound)
        self.assertIn("model = \"gpt-a\"", (agents / "explorer.toml").read_text(encoding="utf-8"))
        self.assertIn("Preserve these review instructions.", specialist.read_text(encoding="utf-8"))

        result, failed_over = self.run_cli(
            "failover", "--role", "Explorer", "--reason", "quota_exceeded", "--event-id", "bind-1"
        )
        self.assertEqual(result.returncode, 0, failed_over)
        native_explorer = (agents / "explorer.toml").read_text(encoding="utf-8")
        self.assertIn("model = \"gpt-b\"", native_explorer)
        self.assertIn("Preserve these review instructions.", specialist.read_text(encoding="utf-8"))

    def test_failover_is_per_role_idempotent_and_non_cycling(self) -> None:
        profile = json.loads(self.profile_path.read_text(encoding="utf-8"))
        profile["roles"]["Explorer"]["fallbacks"] = [
            {"model": "gpt-a", "effort": "low"},
            {"model": "gpt-b", "effort": "max"},
        ]
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        self.run_cli("sync", "--profile", str(self.profile_path))
        result, first = self.run_cli(
            "failover", "--role", "Explorer", "--reason", "quota_exceeded", "--event-id", "e-1"
        )
        self.assertEqual(result.returncode, 0, first)
        self.assertEqual(first["active_model"], "gpt-a")
        self.assertEqual(first["active_effort"], "low")
        _, replay = self.run_cli(
            "failover", "--role", "Explorer", "--reason", "quota_exceeded", "--event-id", "e-1"
        )
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["active_effort"], "low")
        result, second = self.run_cli(
            "failover", "--role", "Explorer", "--reason", "rate_limit", "--event-id", "e-2"
        )
        self.assertEqual(result.returncode, 0, second)
        self.assertEqual(second["active_model"], "gpt-b")
        self.assertEqual(second["active_effort"], "max")
        _, second_replay = self.run_cli(
            "failover", "--role", "Explorer", "--reason", "rate_limit", "--event-id", "e-2"
        )
        self.assertTrue(second_replay["idempotent_replay"])
        self.assertEqual(second_replay["active_effort"], "max")
        result, exhausted = self.run_cli(
            "failover", "--role", "Explorer", "--reason", "rate_limit", "--event-id", "e-4"
        )
        self.assertEqual(result.returncode, 0, exhausted)
        self.assertEqual(exhausted["code"], "no_fallback_remaining")
        result, global_block = self.run_cli(
            "failover", "--role", "Executor", "--reason", "workspace_quota_exhausted", "--event-id", "e-3"
        )
        self.assertEqual(result.returncode, 0, global_block)
        self.assertEqual(global_block["code"], "workspace_quota_blocked")
        profile = json.loads((self.state / "profile.json").read_text(encoding="utf-8"))
        self.assertEqual(profile["roles"]["Explorer"]["active_index"], 2)
        self.assertEqual(profile["roles"]["Executor"]["active_index"], 0)

    def test_concurrent_failovers_preserve_both_roles_and_events(self) -> None:
        profile = json.loads(self.profile_path.read_text(encoding="utf-8"))
        for role in ("Explorer", "Executor"):
            profile["roles"][role]["fallbacks"] = [
                {"model": "gpt-b", "effort": "max"}
            ]
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        result, synced = self.run_cli("sync", "--profile", str(self.profile_path))
        self.assertEqual(result.returncode, 0, synced)

        # Widen the read-modify-write window after each CLI reads profile.json.
        # With no transaction lock both processes read the same old profile/events.
        hook_dir = Path(self.temp.name) / "site-hook"
        hook_dir.mkdir()
        target = str((self.state / "profile.json").resolve())
        (hook_dir / "sitecustomize.py").write_text(
            "from pathlib import Path\n"
            "import time\n"
            "_original_read_text = Path.read_text\n"
            f"_target = Path({target!r})\n"
            "def _delayed_read_text(self, *args, **kwargs):\n"
            "    value = _original_read_text(self, *args, **kwargs)\n"
            "    if self.resolve() == _target:\n"
            "        time.sleep(0.8)\n"
            "    return value\n"
            "Path.read_text = _delayed_read_text\n",
            encoding="utf-8",
        )
        child_env = os.environ.copy()
        child_env["PYTHONIOENCODING"] = "utf-8"
        existing_pythonpath = child_env.get("PYTHONPATH")
        child_env["PYTHONPATH"] = str(hook_dir) + (
            os.pathsep + existing_pythonpath if existing_pythonpath else ""
        )
        processes = [
            subprocess.Popen(
                [
                    sys.executable, str(SCRIPT),
                    "--state-dir", str(self.state),
                    "--codex-home", str(self.codex),
                    "failover", "--role", role,
                    "--reason", "quota_exceeded",
                    "--event-id", event_id,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=child_env,
            )
            for role, event_id in (("Explorer", "parallel-explorer"), ("Executor", "parallel-executor"))
        ]
        completed = [process.communicate(timeout=15) for process in processes]
        payloads = []
        for process, (stdout, stderr) in zip(processes, completed):
            self.assertEqual(process.returncode, 0, f"stdout={stdout!r}; stderr={stderr!r}")
            self.assertEqual(stderr, "")
            payloads.append(json.loads(stdout))
        self.assertEqual([item["status"] for item in payloads], ["failed_over", "failed_over"])

        saved_profile = json.loads((self.state / "profile.json").read_text(encoding="utf-8"))
        self.assertEqual(saved_profile["roles"]["Explorer"]["active_index"], 1)
        self.assertEqual(saved_profile["roles"]["Executor"]["active_index"], 1)
        events = json.loads((self.state / "failover_events.json").read_text(encoding="utf-8"))
        self.assertEqual(set(events["events"]), {"parallel-explorer", "parallel-executor"})
        self.assertEqual(
            tomllib.loads((self.codex / "agents" / "team-explorer.toml").read_text(encoding="utf-8"))["model"],
            "gpt-b",
        )
        self.assertEqual(
            tomllib.loads((self.codex / "agents" / "team-executor.toml").read_text(encoding="utf-8"))["model"],
            "gpt-b",
        )

    def test_catalog_semantics_pending_ack_and_profile_context(self) -> None:
        _, baseline = self.run_cli("models")
        self.assertEqual(baseline["catalog_notice"]["status"], "baseline_established")
        self.run_cli("sync", "--profile", str(self.profile_path))

        self.models.append(self.model("gpt-new", ["low", "max"]))
        self.models[0]["supported_reasoning_levels"].append(
            {"effort": "xhigh", "description": "changed"}
        )
        self.write_cache()
        _, pending = self.run_cli("dashboard")
        notice = pending["catalog_notice"]
        self.assertEqual(notice["status"], "pending_change")
        self.assertEqual(len(notice["current_roles"]), len(ROLE_NAMES))
        self.assertEqual(
            notice["dispatch_compatibility"],
            "not_verified_against_current_session_spawn_interface",
        )
        self.assertEqual(len(notice["pending"]), 1)
        fingerprint = notice["fingerprint"]

        _, repeated = self.run_cli("dashboard")
        self.assertEqual(len(repeated["catalog_notice"]["pending"]), 1)

        self.models.reverse()
        for model in self.models:
            model["supported_reasoning_levels"].reverse()
        self.write_cache()
        _, reordered = self.run_cli("dashboard")
        self.assertEqual(len(reordered["catalog_notice"]["pending"]), 1)

        result, ack = self.run_cli(
            "catalog-ack", "--fingerprint", fingerprint,
            "--decision", "selected", "--roles", "Explorer,Designer",
        )
        self.assertEqual(result.returncode, 0, ack)
        self.assertEqual(ack["profile_updated"], False)
        self.assertEqual(ack["selected_roles"], ["Explorer", "Designer"])
        _, quiet = self.run_cli("dashboard")
        self.assertEqual(quiet["catalog_notice"]["status"], "unchanged")
        profile = json.loads((self.state / "profile.json").read_text(encoding="utf-8"))
        self.assertEqual(profile["roles"]["Designer"]["model"], "gpt-a")

    def test_invalid_cache_does_not_reset_state_and_read_only_commands_continue(self) -> None:
        self.run_cli("models")
        self.run_cli("sync", "--profile", str(self.profile_path))
        self.models.append(self.model("gpt-new", ["low", "max"]))
        self.write_cache()
        _, pending = self.run_cli("dashboard")
        fingerprint = pending["catalog_notice"]["fingerprint"]
        before = (self.state / "catalog_state.json").read_bytes()
        (self.codex / "models_cache.json").write_text('{"models":[]}', encoding="utf-8")

        result, dashboard = self.run_cli("dashboard")
        self.assertEqual(result.returncode, 0, dashboard)
        self.assertEqual(dashboard["catalog_notice"]["status"], "unavailable")
        self.assertEqual((self.state / "catalog_state.json").read_bytes(), before)

        result, logged = self.run_cli(
            "log", "--role", "Executor", "--model", "gpt-a", "--turns", "2",
            "--input-tokens", "123", "--output-tokens", "45",
        )
        self.assertEqual(result.returncode, 0, logged)
        self.assertEqual(logged["entry"]["observed"]["input_tokens"], 123)
        self.assertNotIn("multiplier", logged["entry"])

        result, models = self.run_cli("models")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(models["status"], "unavailable")
        self.assertIn("测试用户", models["error"])
        self.assertEqual((self.state / "catalog_state.json").read_bytes(), before)

        result, ack = self.run_cli(
            "catalog-ack", "--fingerprint", fingerprint, "--decision", "defer"
        )
        self.assertEqual(result.returncode, 0, ack)
        self.assertEqual(ack["status"], "acknowledged")
        state = json.loads((self.state / "catalog_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["acknowledged"][fingerprint]["decision"], "defer")

    def test_sync_prevalidates_all_roles_before_writing(self) -> None:
        self.run_cli("models")
        payload = json.loads(self.profile_path.read_text(encoding="utf-8"))
        payload["roles"]["Designer"]["effort"] = "unsupported"
        self.profile_path.write_text(json.dumps(payload), encoding="utf-8")
        result, error = self.run_cli("sync", "--profile", str(self.profile_path))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(error["status"], "error")
        self.assertFalse((self.codex / "agents" / "team-explorer.toml").exists())
        self.assertFalse((self.state / "profile.json").exists())

    def test_multi_file_sync_rolls_back_prior_replacements(self) -> None:
        result, payload = self.run_cli("sync", "--profile", str(self.profile_path))
        self.assertEqual(result.returncode, 0, payload)
        agents = self.codex / "agents"
        explorer = agents / "team-explorer.toml"
        executor = agents / "team-executor.toml"
        original_explorer = explorer.read_bytes()
        original_executor = executor.read_bytes()

        spec = importlib.util.spec_from_file_location("team_adapter_under_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        real_write = module._atomic_write
        failed = False

        def fail_executor_once(path: Path, data: bytes) -> None:
            nonlocal failed
            if path == executor and not failed:
                failed = True
                raise OSError("simulated replacement failure")
            real_write(path, data)

        with patch.object(module, "_atomic_write", side_effect=fail_executor_once):
            with self.assertRaises(module.AdapterError):
                module.commit_files(
                    {
                        explorer: module.agent_toml("Explorer", "gpt-b", "max"),
                        executor: module.agent_toml("Executor", "gpt-b", "max"),
                    },
                    self.state,
                )
        self.assertEqual(explorer.read_bytes(), original_explorer)
        self.assertEqual(executor.read_bytes(), original_executor)


if __name__ == "__main__":
    unittest.main()


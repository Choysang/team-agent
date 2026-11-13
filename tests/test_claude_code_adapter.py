from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "claude_code_adapter.py"
START = "<!-- team-agent:claude-code routing:start -->"


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.state = root / "state"
        self.home = root / "测试用户" / ".claude"
        (self.home / "agents").mkdir(parents=True)
        self.profile = root / "profile.json"
        # The fixture emulates a host exposing these current tool definitions.
        self.tool_models = "sonnet,opus,haiku,fable"
        self.tool_efforts = "low,medium,high,xhigh,max"
        self.write_profile({
            "Explorer": self.role("haiku", "max"),
            "Executor": self.role("sonnet", "high", [("opus", "medium"), ("fable", "high")]),
        })

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def role(model, effort, fallbacks=()):
        return {"model": model, "effort": effort, "active_index": 0,
                "fallbacks": [{"model": m, "effort": e} for m, e in fallbacks]}

    def write_profile(self, roles, **extra):
        self.profile.write_text(json.dumps({"schema_version": 2, "roles": roles, **extra}), encoding="utf-8")

    def run_cli(self, *args, env=None, host_version="2.1.321", supply_dispatch=True):
        args = list(args)
        for flag, attr in (("--dispatch-models", "tool_models"), ("--dispatch-efforts", "tool_efforts")):
            if flag in args:
                setattr(self, attr, args[args.index(flag) + 1])
            elif "sync" in args and supply_dispatch:
                args.extend((flag, getattr(self, attr)))
        if "--host-version" not in args and host_version is not None:
            args.extend(("--host-version", host_version))
        command = [sys.executable, str(SCRIPT), "--state-dir", str(self.state),
                   "--claude-home", str(self.home), *args]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", env=env)
        return completed.returncode, json.loads(completed.stdout.strip().splitlines()[-1])

    def claude_md(self) -> str:
        return (self.home / "CLAUDE.md").read_text(encoding="utf-8")

    def write_agent(self, agent_id, alias, effort, model_id, ts, agent_type="Explore", text=True):
        folder = self.home / "projects" / "p" / "s" / "subagents"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"agent-{agent_id}.meta.json").write_text(json.dumps(
            {"agentType": agent_type, "model": alias, "effort": effort}), encoding="utf-8")
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))
        content = [{"type": "text", "text": "pong"}] if text else [{"type": "thinking", "thinking": ""}]
        line = {"type": "assistant", "timestamp": stamp, "requestedModel": model_id, "effort": effort,
                "message": {"model": model_id, "content": content}}
        (folder / f"agent-{agent_id}.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")


class ClaudeCodeAdapterTests(Fixture):
    def test_sync_fills_unanswered_roles_with_inherit_and_keeps_user_text(self):
        (self.home / "CLAUDE.md").write_text("# 我的规则\n保留我\n", encoding="utf-8")
        code, out = self.run_cli("sync", "--profile", str(self.profile))
        self.assertEqual(code, 0, out)
        text = self.claude_md()
        self.assertTrue(text.startswith("# 我的规则\n保留我\n\n" + START))
        self.assertIn("| Explorer | 查文件、调用链、证据定位（只读） | `Explore` | `haiku` | `max` | 无 |", text)
        self.assertIn("| Reviewer | 独立审查与风险核验 | `team-reviewer` | 不传（继承） | 不传（继承） | 无 |", text)
        saved = json.loads((self.state / "profile.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["roles"]["Designer"]["model"], "inherit")
        # Re-sync is a no-op and never duplicates the block.
        self.run_cli("sync", "--profile", str(self.profile))
        self.assertEqual(self.claude_md().count(START), 1)

    def test_rejects_unknown_model_and_unsupported_effort(self):
        self.write_profile({"Explorer": self.role("gpt-x", "high")})
        code, out = self.run_cli("sync", "--profile", str(self.profile))
        self.assertEqual(code, 1)
        self.assertIn("gpt-x", out["error"])
        self.write_profile({"Explorer": self.role("haiku", "turbo")})
        code, out = self.run_cli("sync", "--profile", str(self.profile))
        self.assertIn("turbo", out["error"])
        self.assertFalse((self.home / "CLAUDE.md").exists())

    def test_dispatch_enum_limits_selectable_models(self):
        self.run_cli("models", "--dispatch-models", "sonnet,haiku")
        code, out = self.run_cli("sync", "--profile", str(self.profile))
        self.assertEqual(code, 1)  # Executor fallback uses opus/fable, not in enum
        self.assertIn("opus", out["error"])

    def test_cached_enum_is_not_presented_as_current_selection(self):
        _, current = self.run_cli("models", "--dispatch-models", "sonnet,haiku",
                                  "--dispatch-efforts", "low,medium,high")
        self.assertEqual(current["first_use_check"]["selectable_models"], ["sonnet", "haiku", "inherit"])
        _, cached = self.run_cli("models")
        self.assertTrue(cached["dispatch_enum"]["refresh_required"])
        self.assertEqual(cached["dispatch_enum"]["source"], "cached_dispatch_tool")
        self.assertEqual(cached["first_use_check"]["selectable_models"], ["inherit"])
        self.assertEqual(cached["first_use_check"]["current_roles"], None)

    def test_unverified_defaults_are_not_callable_candidates(self):
        _, out = self.run_cli("onboarding")
        self.assertEqual(out["first_use_check"]["selectable_models"], ["inherit"])
        self.assertFalse(out["first_use_check"]["selection_ready"])

    def test_sync_cannot_create_choices_from_unverified_defaults_or_cache(self):
        code, out = self.run_cli("sync", "--profile", str(self.profile),
                                 host_version=None, supply_dispatch=False)
        self.assertEqual(code, 1, out)
        self.assertIn("requires CURRENT", out["error"])
        self.assertFalse((self.home / "CLAUDE.md").exists())
        self.run_cli("models", "--dispatch-models", self.tool_models, "--dispatch-efforts", self.tool_efforts)
        code, out = self.run_cli("sync", "--profile", str(self.profile), supply_dispatch=False)
        self.assertEqual(code, 1, out)
        self.assertFalse((self.state / "profile.json").exists())

    def test_omitting_version_does_not_promote_cached_host_to_latest(self):
        self.run_cli("models", "--host-version", "2.1.292", "--dispatch-models", "haiku",
                     "--dispatch-efforts", "low,medium,high,xhigh,max")
        _, out = self.run_cli("models", host_version=None)
        self.assertEqual(out["models"][0]["resolves_to"], "claude-haiku-4-5")
        self.assertEqual(out["models"][0]["resolution_source"], "cached_host_version")
        self.assertEqual(out["catalog_notice"]["status"], "unchanged")
        self.assertFalse(out["first_use_check"]["selection_ready"])

    def test_unknown_version_does_not_invent_latest_capabilities(self):
        _, out = self.run_cli("models", "--dispatch-models", "fable", "--dispatch-efforts", "medium,high",
                              host_version=None)
        self.assertIsNone(out["models"][0]["resolves_to"])
        self.assertIsNone(out["models"][0]["supported_reasoning_levels"])
        self.assertFalse(out["first_use_check"]["selection_ready"])

    def test_inherit_cannot_bypass_empty_effort_enum(self):
        self.write_profile({"Explorer": self.role("inherit", "max")})
        code, out = self.run_cli("sync", "--profile", str(self.profile),
                                 "--dispatch-models", "haiku", "--dispatch-efforts", "")
        self.assertEqual(code, 1, out)
        self.assertFalse((self.state / "profile.json").exists())

    def test_current_enum_on_resolve_detects_successive_unknown_models(self):
        self.run_cli("sync", "--profile", str(self.profile))
        original = {p: p.read_bytes() for p in [self.state / "profile.json", self.home / "CLAUDE.md"]}
        for alias in ("future-model-a", "future-model-b"):
            code, out = self.run_cli("resolve", "--role", "Explorer", "--dispatch-models",
                                     f"sonnet,opus,haiku,fable,{alias}", "--dispatch-efforts", "medium,high")
            self.assertEqual(code, 0, out)
            notice = out["catalog_notice"]
            self.assertEqual(notice["status"], "pending_change")
            added = [m["slug"] for change in notice["pending"] for m in change["changes"]["added_models"]]
            self.assertIn(alias, added)
            self.run_cli("catalog-ack", "--fingerprint", notice["fingerprint"], "--decision", "defer")
        self.assertEqual({p: p.read_bytes() for p in original}, original)

    def test_explicit_empty_effort_enum_does_not_restore_default_tiers(self):
        code, out = self.run_cli("models", "--dispatch-models", "haiku", "--dispatch-efforts", "")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["models"][0]["supported_reasoning_levels"], [])
        self.assertEqual(out["dispatch_enum"]["efforts"], [])

    def test_alias_resolution_respects_actual_host_version(self):
        for version, expected, default in (("2.1.308", "claude-fable-5-1", "high"),
                                           ("2.1.321", "claude-fable-5-2", "medium")):
            code, out = self.run_cli("models", "--host-version", version, "--dispatch-models", "fable",
                                     "--dispatch-efforts", "low,medium,high,xhigh,max")
            self.assertEqual(code, 0, out)
            model = out["models"][0]
            self.assertEqual((model["resolves_to"], model["default_effort"]), (expected, default))
        _, out = self.run_cli("models", "--host-version", "2.1.292", "--dispatch-models", "haiku",
                              "--dispatch-efforts", "low,medium,high,xhigh,max")
        self.assertEqual(out["models"][0]["resolves_to"], "claude-haiku-4-5")
        self.assertEqual(out["models"][0]["supported_reasoning_levels"], [])

    def test_alias_pin_precedes_old_telemetry_and_keeps_model_capabilities(self):
        self.write_agent("old-fable", "fable", "max", "claude-fable-5-2", time.time())
        settings = self.home / "settings.json"
        settings.write_text(json.dumps({"env": {"ANTHROPIC_DEFAULT_FABLE_MODEL": "claude-fable-5-1"}}),
                            encoding="utf-8")
        before = settings.read_bytes()
        _, out = self.run_cli("models", "--dispatch-models", "fable", "--dispatch-efforts", "medium,high,max")
        model = out["models"][0]
        self.assertEqual((model["resolves_to"], model["resolution_source"]),
                         ("claude-fable-5-1", "host_alias_pin"))
        self.assertEqual(model["supported_reasoning_levels"], ["medium", "high", "max"])
        self.assertEqual(settings.read_bytes(), before)

    def test_unknown_resolved_model_does_not_borrow_alias_context(self):
        self.write_agent("custom", "haiku", "medium", "provider-custom-model", time.time())
        _, out = self.run_cli("models", host_version=None)
        model = next(m for m in out["models"] if m["slug"] == "haiku")
        self.assertIsNone(model["supported_reasoning_levels"])
        self.assertIsNone(model["context"])
        self.assertIsNone(model["fast_supported"])

    def test_current_version_and_historical_observations_are_separate(self):
        self.write_agent("old-fable", "fable", "high", "claude-fable-5-1", time.time())
        _, out = self.run_cli("models", "--dispatch-models", "fable", "--dispatch-efforts", "medium,high")
        self.assertEqual(out["models"][0]["resolves_to"], "claude-fable-5-2")
        self.assertEqual(out["models"][0]["last_observed_model"], "claude-fable-5-1")
        self.write_agent("future-fable", "fable", "high", "claude-fable-future", time.time() + 2)
        _, out = self.run_cli("models", "--dispatch-models", "fable", "--dispatch-efforts", "medium,high")
        self.assertEqual(out["catalog_notice"]["status"], "pending_change")
        self.assertEqual(out["models"][0]["last_observed_model"], "claude-fable-future")

    def test_pinned_new_model_is_not_selectable_on_old_host(self):
        (self.home / "settings.json").write_text(json.dumps({
            "env": {"ANTHROPIC_DEFAULT_FABLE_MODEL": "claude-fable-5-2"}}), encoding="utf-8")
        _, out = self.run_cli("models", "--host-version", "2.1.308", "--dispatch-models", "fable",
                              "--dispatch-efforts", "medium,high")
        self.assertFalse(out["models"][0]["host_version_supported"])
        self.assertEqual(out["first_use_check"]["selectable_models"], ["inherit"])
        self.write_profile({"Explorer": self.role("fable", "high")})
        code, out = self.run_cli("sync", "--profile", str(self.profile), "--host-version", "2.1.308")
        self.assertEqual(code, 1, out)

    def test_unknown_provider_effort_changes_are_not_suppressed(self):
        self.run_cli("--provider", "other", "models", "--dispatch-models", "haiku", "--dispatch-efforts", "medium")
        _, out = self.run_cli("--provider", "other", "models", "--dispatch-models", "haiku",
                              "--dispatch-efforts", "medium,high,future-tier")
        self.assertEqual(out["catalog_notice"]["status"], "pending_change")
        self.assertIsNone(out["models"][0]["supported_reasoning_levels"])
        self.assertIn("future-tier", out["models"][0]["tool_effort_levels"])

    def test_effort_changes_are_detected_even_without_model_aliases(self):
        self.run_cli("models", "--dispatch-models", "", "--dispatch-efforts", "medium")
        _, out = self.run_cli("models", "--dispatch-models", "", "--dispatch-efforts", "medium,high")
        self.assertEqual(out["catalog_notice"]["status"], "pending_change")
        self.assertEqual(out["catalog_notice"]["pending"][0]["changes"]["dispatch_efforts"],
                         {"previous": ["medium"], "current": ["high", "medium"]})

    def test_provider_boolean_flag_keeps_json_response(self):
        (self.home / "settings.json").write_text(json.dumps({
            "env": {"CLAUDE_CODE_USE_BEDROCK": True}}), encoding="utf-8")
        code, out = self.run_cli("models")
        self.assertEqual(code, 0, out)
        self.assertIsNone(out["models"][0]["supported_reasoning_levels"])

    def test_old_fingerprint_order_is_migrated_without_empty_notice(self):
        _, out = self.run_cli("models", "--dispatch-models", self.tool_models, "--dispatch-efforts", self.tool_efforts)
        path = self.state / "catalog_state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        for model in state["last_seen_models"]:
            for key in ("tool_effort_levels", "last_observed_model", "observed_at", "observed_differs_from_reference"):
                model.pop(key, None)
        old_core = [{"slug": m["slug"], "resolves_to": m["resolves_to"],
                     "levels": m["supported_reasoning_levels"]} for m in state["last_seen_models"]]
        state["last_seen_fingerprint"] = hashlib.sha256(json.dumps(old_core, sort_keys=True).encode()).hexdigest()
        path.write_text(json.dumps(state), encoding="utf-8")
        _, out = self.run_cli("models", "--dispatch-models", self.tool_models, "--dispatch-efforts", self.tool_efforts)
        self.assertEqual(out["catalog_notice"]["pending"], [])

    def test_gateway_is_not_treated_as_direct_anthropic(self):
        (self.home / "settings.json").write_text(json.dumps({
            "env": {"ANTHROPIC_BASE_URL": "https://gateway.example/v1"}}), encoding="utf-8")
        _, out = self.run_cli("models", "--dispatch-models", "sonnet", "--dispatch-efforts", "medium,high")
        self.assertIsNone(out["models"][0]["supported_reasoning_levels"])
        self.assertIsNone(out["models"][0]["context"])

    def test_enum_reordering_does_not_prompt_and_cross_model_effort_is_explicit(self):
        self.run_cli("models", "--dispatch-models", "haiku,sonnet", "--dispatch-efforts", "low,medium,high")
        _, out = self.run_cli("models", "--dispatch-models", "sonnet,haiku", "--dispatch-efforts", "high,low,medium")
        self.assertEqual(out["catalog_notice"]["status"], "unchanged")
        self.assertEqual(out["first_use_check"]["effort_when_model_changes"], "host_model_default_unless_explicit")

    def test_other_provider_has_unknown_efforts_but_inherit_still_allowed(self):
        code, out = self.run_cli("--provider", "other", "models")
        self.assertIsNone(out["models"][0]["supported_reasoning_levels"])
        self.write_profile({"Explorer": self.role("haiku", "inherit")})
        code, out = self.run_cli("--provider", "other", "sync", "--profile", str(self.profile))
        self.assertEqual(code, 0, out)

    def test_incomplete_block_is_not_overwritten(self):
        (self.home / "CLAUDE.md").write_text(START + "\nbroken\n", encoding="utf-8")
        code, out = self.run_cli("sync", "--profile", str(self.profile))
        self.assertEqual(code, 1)
        self.assertIn("incomplete", out["error"])

    def test_resolve_prefers_specialist_and_builtins(self):
        (self.home / "agents" / "code-reviewer.md").write_text(
            "---\nname: code-reviewer\ndescription: x\n---\nbody\n", encoding="utf-8")
        self.run_cli("sync", "--profile", str(self.profile))
        _, out = self.run_cli("resolve", "--role", "Reviewer")
        self.assertEqual(out["agent_type"], "code-reviewer")
        _, out = self.run_cli("resolve", "--role", "Reviewer", "--loaded-agent-types", "Explore,general-purpose")
        self.assertEqual(out["agent_type"], "team-reviewer")  # specialist file exists but not loaded
        _, out = self.run_cli("resolve", "--role", "Explorer")
        self.assertEqual((out["agent_type"], out["dispatch_parameters"]), ("Explore", {"model": "haiku", "effort": "max"}))
        _, out = self.run_cli("resolve", "--role", "ExpertAdvisor")
        self.assertEqual((out["agent_type"], out["dispatch_parameters"]), ("Plan", {}))
        _, out = self.run_cli("resolve", "--role", "something-unknown")
        self.assertTrue(out["fallback"])
        self.assertEqual(out["role"], "GeneralWorker")

    def test_environment_overrides_are_blocking(self):
        self.run_cli("sync", "--profile", str(self.profile))
        (self.home / "settings.json").write_text(json.dumps({
            "env": {"CLAUDE_CODE_EFFORT_LEVEL": "low"}, "availableModels": ["sonnet"]}), encoding="utf-8")
        code, out = self.run_cli("verify")
        self.assertEqual(code, 1)
        self.assertEqual(out["blocking"][0]["code"], "effort_env_overrides_all_roles")
        _, out = self.run_cli("models")
        codes = {c["code"] for c in out["environment_checks"]}
        self.assertIn("model_outside_available_models", codes)

    def test_verify_detects_hand_edited_block(self):
        self.run_cli("sync", "--profile", str(self.profile))
        self.assertEqual(self.run_cli("verify")[0], 0)
        (self.home / "CLAUDE.md").write_text(self.claude_md().replace("`max`", "`low`"), encoding="utf-8")
        code, out = self.run_cli("verify")
        self.assertEqual(code, 1)
        self.assertIn("routing_block_missing_or_stale", out["problems"])

    def test_failover_chain_idempotent_no_wrap_and_rewrites_block(self):
        self.run_cli("sync", "--profile", str(self.profile))
        _, first = self.run_cli("failover", "--role", "Executor", "--reason", "quota_exceeded", "--event-id", "a")
        self.assertEqual((first["status"], first["active_model"]), ("failed_over", "opus"))
        _, replay = self.run_cli("failover", "--role", "Executor", "--reason", "quota_exceeded", "--event-id", "a")
        self.assertTrue(replay["idempotent_replay"])
        self.assertIn("| `opus` | `medium` | fable/high |", self.claude_md())
        _, net = self.run_cli("failover", "--role", "Executor", "--reason", "network", "--event-id", "b")
        self.assertEqual(net["code"], "reason_not_switchable")
        _, ws = self.run_cli("failover", "--role", "Executor", "--reason", "workspace_quota_exceeded", "--event-id", "c")
        self.assertEqual(ws["code"], "workspace_quota_blocked")
        self.run_cli("failover", "--role", "Executor", "--reason", "rate_limit", "--event-id", "d")
        _, end = self.run_cli("failover", "--role", "Executor", "--reason", "rate_limit", "--event-id", "e")
        self.assertEqual(end["code"], "no_fallback_remaining")
        self.assertEqual(self.run_cli("verify")[0], 0)

    def test_preference_is_role_specific_and_reversible(self):
        self.run_cli("sync", "--profile", str(self.profile))
        _, out = self.run_cli("preference", "--role", "Explorer", "--skip-fallback-prompts", "true")
        self.assertTrue(out["effective"])
        _, out = self.run_cli("resolve", "--role", "Executor")
        self.assertFalse(out["skip_fallback_prompts"])
        _, out = self.run_cli("preference", "--role", "Explorer", "--skip-fallback-prompts", "false")
        self.assertFalse(out["effective"])

    def test_bind_adds_managed_frontmatter_and_refuses_unmanaged_conflict(self):
        reviewer = self.home / "agents" / "python-reviewer.md"
        reviewer.write_text("---\nname: python-reviewer\ndescription: x\n---\nbody\n", encoding="utf-8")
        self.write_profile({"Reviewer": self.role("opus", "high")})
        self.run_cli("sync", "--profile", str(self.profile))
        code, out = self.run_cli("bind")
        self.assertEqual(code, 0, out)
        text = reviewer.read_text(encoding="utf-8")
        self.assertIn("model: opus  # team-agent managed", text)
        self.assertTrue(text.endswith("---\nbody\n"))
        self.write_profile({"Reviewer": self.role("sonnet", "low")})
        self.run_cli("sync", "--profile", str(self.profile))
        self.assertIn("model: sonnet  # team-agent managed", reviewer.read_text(encoding="utf-8"))
        self.assertEqual(reviewer.read_text(encoding="utf-8").count("team-agent managed"), 2)
        (self.home / "agents" / "go-reviewer.md").write_text(
            "---\nname: go-reviewer\ndescription: x\nmodel: haiku\n---\n", encoding="utf-8")
        code, out = self.run_cli("sync", "--profile", str(self.profile))
        self.assertEqual(code, 1)
        self.assertIn("reconciliation", out["error"])

    def test_catalog_change_detected_once_from_observed_resolution(self):
        self.run_cli("models")
        self.write_agent("old1", "haiku", "low", "claude-haiku-6-0", time.time())
        _, out = self.run_cli("models", host_version=None)
        notice = out["catalog_notice"]
        self.assertEqual(notice["status"], "pending_change")
        changed = notice["pending"][0]["changes"]["changed_models"][0]
        self.assertEqual((changed["slug"], changed["last_observed_model"]), ("haiku", "claude-haiku-6-0"))
        self.assertEqual(changed["resolves_to"], "claude-haiku-5-5")  # reference is not historical telemetry
        _, out = self.run_cli("catalog-ack", "--fingerprint", notice["fingerprint"], "--decision", "defer")
        self.assertEqual(out["status"], "acknowledged")
        _, out = self.run_cli("models")
        self.assertEqual(out["catalog_notice"]["status"], "unchanged")
        code, out = self.run_cli("resolve", "--role", "Explorer")
        self.assertEqual(code, 1)  # no profile synced in this test

    def test_resolve_keeps_dispatching_when_alias_moves_to_unknown_model(self):
        self.run_cli("sync", "--profile", str(self.profile))
        self.write_agent("n1", "haiku", "max", "claude-haiku-6-0", time.time())
        code, out = self.run_cli("resolve", "--role", "Explorer", host_version=None)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["dispatch_parameters"], {"model": "haiku", "effort": "max"})
        self.assertIn("historical_model_differs_from_reference", {w["code"] for w in out["warnings"]})
        code, out = self.run_cli("verify")
        self.assertEqual(code, 0, out)  # static reference matches; actual dispatch still needs a fresh probe
        self.assertEqual(out["runtime_status"], "runtime_unverified")

    def test_probe_requires_fresh_distinct_matching_agents(self):
        self.run_cli("sync", "--profile", str(self.profile))
        manifest = Path(self.temp.name) / "m.json"
        self.run_cli("probe", "--prepare", str(manifest))
        now = time.time() + 2
        self.write_agent("e1", "haiku", "max", "claude-haiku-5-5", now)
        self.write_agent("x1", "sonnet", "high", "claude-sonnet-5-5", now, agent_type="general-purpose")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["agents"] = {"Explorer": "e1", "Executor": "x1"}
        manifest.write_text(json.dumps(data), encoding="utf-8")
        code, out = self.run_cli("probe", "--check", str(manifest))
        self.assertEqual((code, out["status"]), (0, "pass"), out)
        self.assertEqual({c["status"] for c in out["checks"]}, {"pass", "not_configured"})
        # Same agent cannot prove two roles; effort downgrade is a mismatch.
        data["agents"] = {"Explorer": "e1", "Executor": "e1"}
        manifest.write_text(json.dumps(data), encoding="utf-8")
        _, out = self.run_cli("probe", "--check", str(manifest))
        self.assertEqual(out["status"], "incomplete")
        self.write_agent("x2", "sonnet", "high", "claude-sonnet-5-5", now, agent_type="general-purpose")
        line = json.loads((self.home / "projects/p/s/subagents/agent-x2.jsonl").read_text())
        line["effort"] = "medium"
        (self.home / "projects/p/s/subagents/agent-x2.jsonl").write_text(json.dumps(line))
        data["agents"] = {"Explorer": "e1", "Executor": "x2"}
        manifest.write_text(json.dumps(data), encoding="utf-8")
        _, out = self.run_cli("probe", "--check", str(manifest))
        executor = next(c for c in out["checks"] if c["role"] == "Executor")
        self.assertEqual(executor["reason"], "observed_effort_mismatch")
        # Old agents and config changes after prepare do not count.
        self.write_agent("old", "sonnet", "high", "claude-sonnet-5-5", now - 3600, agent_type="general-purpose")
        data["agents"] = {"Explorer": "e1", "Executor": "old"}
        manifest.write_text(json.dumps(data), encoding="utf-8")
        _, out = self.run_cli("probe", "--check", str(manifest))
        executor = next(c for c in out["checks"] if c["role"] == "Executor")
        self.assertEqual(executor["reason"], "agent_predates_this_test")
        self.write_profile({"Explorer": self.role("haiku", "low")})
        self.run_cli("sync", "--profile", str(self.profile))
        _, out = self.run_cli("probe", "--check", str(manifest))
        self.assertEqual(out["reason"], "configuration_changed_since_prepare")

    def test_codex_state_is_untouched(self):
        codex_state = Path(self.temp.name) / "codex-state"
        codex_state.mkdir()
        (codex_state / "profile.json").write_text("codex", encoding="utf-8")
        self.run_cli("sync", "--profile", str(self.profile))
        self.assertEqual((codex_state / "profile.json").read_text(encoding="utf-8"), "codex")
        self.assertFalse((self.state / ".." / "codex-state" / "catalog_state.json").exists())


class ClaudeCodeTeamFilesAndHookTests(Fixture):
    def test_team_files_routing_and_router_copy(self):
        self.write_profile({"Reviewer": self.role("opus", "medium"), "Explorer": self.role("haiku", "max")})
        (self.home / "agents" / "python-reviewer.md").write_text(
            "---\nname: python-reviewer\ndescription: x\n---\nbody\n", encoding="utf-8")
        code, out = self.run_cli("sync", "--profile", str(self.profile))
        self.assertEqual(code, 0, out)
        reviewer = (self.home / "agents" / "team-reviewer.md").read_text(encoding="utf-8")
        self.assertIn("disallowedTools: Write, Edit, NotebookEdit", reviewer)
        self.assertIn("model: opus\neffort: medium\n---", reviewer)
        designer = (self.home / "agents" / "team-designer.md").read_text(encoding="utf-8")
        self.assertNotIn("model:", designer)  # inherit -> no field
        routing = json.loads((self.state / "routing.json").read_text(encoding="utf-8"))
        self.assertNotIn("policy", routing)
        self.assertEqual(routing["agents"]["explore"]["model"], "haiku")
        self.assertEqual(routing["agents"]["python-reviewer"]["role"], "Reviewer")
        self.assertEqual(routing["agents"]["general-purpose"]["role"], "Executor")
        self.assertNotIn("team-reviewer", {a["host_role"] for a in self.run_cli("models")[1]["agents"]})
        self.assertEqual(self.run_cli("verify")[0], 0)
        (self.home / "agents" / "team-reviewer.md").write_text("tampered", encoding="utf-8")
        code, out = self.run_cli("verify")
        self.assertEqual(code, 1)

    def test_foreign_team_file_is_not_overwritten(self):
        (self.home / "agents" / "team-reviewer.md").write_text("---\nname: team-reviewer\n---\nmine\n", encoding="utf-8")
        code, out = self.run_cli("sync", "--profile", str(self.profile))
        self.assertEqual(code, 1)
        self.assertIn("not generated by team-agent", out["error"])


class RecoveryAndStatsTests(Fixture):
    def test_unavailable_unrelated_fallback_does_not_block_switch_or_recovery(self):
        self.write_profile({"Explorer": self.role("haiku", "medium", [("sonnet", "high")]),
                            "Executor": self.role("opus", "medium", [("fable", "high")])})
        self.run_cli("sync", "--profile", str(self.profile))
        _, before = self.run_cli("models")
        executor = before["first_use_check"]["current_roles"]["Executor"]
        code, out = self.run_cli("failover", "--role", "Explorer", "--reason", "overloaded", "--event-id", "switch",
                                 "--dispatch-models", "haiku,sonnet,opus", "--dispatch-efforts", self.tool_efforts)
        self.assertEqual((code, out["status"], out["active_model"]), (0, "failed_over", "sonnet"), out)
        self.assertIn({"role": "Executor", "model": "fable", "effort": "high"}, out["unavailable_saved_choices"])
        code, out = self.run_cli("recover", "--role", "Explorer", "--force")
        self.assertEqual((code, out["restored_to_primary"]), (0, ["Explorer"]), out)
        _, after = self.run_cli("models")
        self.assertEqual(after["first_use_check"]["current_roles"]["Executor"], executor)

    def test_unavailable_activation_target_does_not_modify_profile(self):
        self.write_profile({"Explorer": self.role("haiku", "medium", [("sonnet", "high")])})
        self.run_cli("sync", "--profile", str(self.profile))
        path = self.state / "profile.json"
        before = path.read_bytes()
        code, out = self.run_cli("failover", "--role", "Explorer", "--reason", "overloaded", "--event-id", "switch",
                                 "--dispatch-models", "haiku", "--dispatch-efforts", self.tool_efforts)
        self.assertEqual(code, 1, out)
        self.assertIn("activation sonnet/high", out["error"])
        self.assertEqual(path.read_bytes(), before)

    def test_classify_messages(self):
        cases = {"429 Too Many Requests": ("rate_limit", True), "API Error: 529 Overloaded": ("overloaded", True),
                 "Your organization's usage limit has been reached": ("workspace_quota_exceeded", False),
                 "credit balance is too low": ("quota_exceeded", True), "ECONNRESET": ("network", False),
                 "401 invalid api key": ("auth", False), "weird": ("unknown", False)}
        for text, expected in cases.items():
            _, out = self.run_cli("classify", "--text", text)
            self.assertEqual((out["reason"], out["switch"]), expected, text)

    def test_failover_sets_cooldown_and_recover_waits_then_restores(self):
        self.run_cli("sync", "--profile", str(self.profile))
        _, out = self.run_cli("failover", "--role", "Executor", "--reason", "overloaded", "--event-id", "x1")
        self.assertEqual(out["active_model"], "opus")
        self.assertIn("primary_retry_after", out)
        _, out = self.run_cli("resolve", "--role", "Executor")
        self.assertFalse(out["recovery"]["primary_recoverable"])
        _, out = self.run_cli("recover", "--role", "Executor")
        self.assertEqual((out["restored_to_primary"], list(out["still_cooling"])), ([], ["Executor"]))
        cooldowns = json.loads((self.state / "cooldowns.json").read_text(encoding="utf-8"))
        cooldowns["Executor"]["until"] = 0
        (self.state / "cooldowns.json").write_text(json.dumps(cooldowns), encoding="utf-8")
        _, out = self.run_cli("recover", "--role", "all")
        self.assertEqual(out["restored_to_primary"], ["Executor"])
        self.assertIn("| Executor | 实现、修复、测试、交付 | `general-purpose` | `sonnet` | `high` |", self.claude_md())
        self.assertEqual(self.run_cli("verify")[0], 0)

    def test_server_error_is_not_switched(self):
        self.run_cli("sync", "--profile", str(self.profile))
        _, out = self.run_cli("failover", "--role", "Executor", "--reason", "server_error", "--event-id", "s1")
        self.assertEqual(out["code"], "reason_not_switchable")
        self.assertIn("retry", out["advice"])

    def test_stats_groups_and_flags_deviations(self):
        self.run_cli("sync", "--profile", str(self.profile))
        now = time.time()
        self.write_agent("ok1", "haiku", "max", "claude-haiku-5-5", now)
        self.write_agent("bad1", None, "medium", "claude-opus-5-5", now, agent_type="general-purpose")
        _, out = self.run_cli("stats", "--days", "1")
        self.assertEqual(out["by_agent_type"]["Explore"]["dispatches"], 1)
        self.assertEqual(out["by_agent_type"]["general-purpose"]["role"], "Executor")
        self.assertEqual(out["deviations"], [{"agent_type": "general-purpose", "expected_model": "sonnet",
                                              "observed": ["claude-opus-5-5"]}])


class LadderAndModTests(Fixture):
    def ladder(self, *args):
        _, out = self.run_cli("ladder", *args)
        return out

    def test_same_model_effort_first_then_stronger_models_from_medium(self):
        self.write_profile({"Executor": self.role("sonnet", "high"), "Reviewer": self.role("opus", "low")})
        self.run_cli("sync", "--profile", str(self.profile))
        rungs = [r["model"] + "/" + r["effort"] for r in self.ladder("--role", "Executor")["ladder"]]
        self.assertEqual(rungs[:4], ["sonnet/high", "sonnet/xhigh", "sonnet/max", "opus/medium"])
        self.assertEqual(rungs[-1], "fable/max")
        self.assertNotIn("opus/low", rungs)
        rungs = [r["model"] + "/" + r["effort"] for r in self.ladder("--role", "Reviewer")["ladder"]]
        self.assertEqual(rungs[:2], ["opus/low", "opus/medium"])

    def test_from_overrides_start_and_top_rung_has_no_next(self):
        self.run_cli("sync", "--profile", str(self.profile))
        out = self.ladder("--role", "Explorer", "--from", "opus/max")
        self.assertEqual(out["next"], {"model": "fable", "effort": "medium"})
        out = self.ladder("--role", "Explorer", "--from", "fable/max")
        self.assertIsNone(out["next"])
        out = self.ladder("--role", "Designer")
        self.assertEqual(out["status"], "error")  # inherit needs --from
        out = self.ladder("--role", "Designer", "--from", "sonnet")
        self.assertEqual(out["next"], {"model": "sonnet", "effort": "high"})

    def test_ladder_respects_dispatch_enum(self):
        self.run_cli("models", "--dispatch-models", "haiku,sonnet")
        self.write_profile({"Explorer": self.role("haiku", "max")})
        self.run_cli("sync", "--profile", str(self.profile))
        rungs = [r["model"] for r in self.ladder("--role", "Explorer")["ladder"]]
        self.assertEqual(set(rungs), {"haiku", "sonnet"})

    def test_mod_status_accepts_a_linked_skill_dir(self):
        (self.home / "skills").mkdir()
        try:
            (self.home / "skills" / SKILL_DIR.name).symlink_to(SKILL_DIR, target_is_directory=True)
        except OSError as exc:  # Windows without Developer Mode or admin rights
            self.skipTest(f"cannot create symlinks here: {exc}")
        self.run_cli("sync", "--profile", str(self.profile))
        _, out = self.run_cli("verify")
        self.assertEqual((out["mod"]["will_load"], out["mod"]["how"]), (True, "skills_folder"))

    def test_mod_status_reports_how_it_loads(self):
        self.run_cli("sync", "--profile", str(self.profile))
        _, out = self.run_cli("verify")
        self.assertTrue(out["mod"]["packaged"])
        self.assertFalse(out["mod"]["will_load"])
        (self.home / "settings.json").write_text(json.dumps({"env": {"CLAUDE_CODE_PLUGIN_DIRS": str(SKILL_DIR)}}),
                                                 encoding="utf-8")
        _, out = self.run_cli("verify")
        self.assertEqual(out["mod"]["how"], "CLAUDE_CODE_PLUGIN_DIRS")


if __name__ == "__main__":
    unittest.main()

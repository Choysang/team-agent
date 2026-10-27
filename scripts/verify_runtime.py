"""Verify all configured role calls against a manifest of fresh host thread IDs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from runtime_probe import inspect


def profile_hash(profile: dict) -> str:
    return hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify(profile: dict, manifest: dict, codex_home: Path) -> dict:
    fingerprint = profile_hash(profile)
    started_at = manifest.get("started_at")
    if manifest.get("configuration_sha256") != fingerprint or not isinstance(started_at, (float, int)):
        return {"status": "incomplete", "reason": "test_manifest_missing_or_configuration_changed", "checks": []}
    threads = manifest.get("threads", {})
    checks = []
    seen_threads = set()
    for role, config in profile["roles"].items():
        choice = [config, *config["fallbacks"]][config["active_index"]]
        thread_id = threads.get(role)
        if not isinstance(thread_id, str) or not thread_id:
            checks.append({"role": role, "status": "unverified", "reason": "no_thread_supplied"})
            continue
        if thread_id in seen_threads:
            checks.append({"role": role, "status": "unverified", "reason": "thread_reused_for_another_role"})
            continue
        seen_threads.add(thread_id)
        result = inspect(codex_home, thread_id, choice["model"], choice["effort"])
        result["role"] = role
        # Prevent one successful same-model thread from proving unrelated roles.
        try:
            with closing(sqlite3.connect((codex_home / "state_5.sqlite").as_uri() + "?mode=ro", uri=True)) as db:
                row = db.execute("SELECT agent_role, created_at FROM threads WHERE id=?", (thread_id,)).fetchone()
            expected_role = manifest.get("expected_agent_types", {}).get(role, "team-" + role.lower())
            if not row or row[0] != expected_role:
                result.update(status="unverified", reason="thread_role_identity_not_verified")
            elif not isinstance(row[1], (int, float)) or row[1] < started_at:
                result.update(status="unverified", reason="thread_predates_this_test")
            result["expected_agent_type"] = expected_role
            result["observed_agent_type"] = row[0] if row else None
        except (OSError, sqlite3.Error):
            result.update(status="unverified", reason="thread_role_telemetry_unavailable")
        checks.append(result)
    return {"status": "pass" if checks and all(item["status"] == "pass" for item in checks) else "incomplete",
            "configuration_sha256": fingerprint, "checks": checks,
            "routing_policy": manifest.get("routing_policy", "legacy_team_roles"),
            "scope": "host_selected_role_model_and_effort_not_task_quality_or_server_weights"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex"))
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".team-agent")
    parser.add_argument("--loaded-agent-types", help="Comma-separated agent types actually exposed in this session")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--threads", type=Path, help="Prepared test manifest completed with role thread IDs")
    mode.add_argument("--prepare", type=Path, help="Create a fresh manifest BEFORE dispatching role probes")
    args = parser.parse_args()
    try:
        profile = json.loads((args.state_dir / "profile.json").read_text(encoding="utf-8-sig"))
        if args.prepare:
            from team_adapter import resolve_role
            loaded = {item.strip().casefold() for item in args.loaded_agent_types.split(",") if item.strip()} if args.loaded_agent_types else None
            expected_agent_types = {
                role: resolve_role(role, args.codex_home.resolve(), loaded_agent_types=loaded)["agent_type"]
                for role in profile["roles"]
            }
            args.prepare.parent.mkdir(parents=True, exist_ok=True)
            args.prepare.write_text(json.dumps({"started_at": int(time.time()),
                "configuration_sha256": profile_hash(profile), "expected_agent_types": expected_agent_types,
                "routing_policy": "host_roles_first", "threads": {}}, indent=2) + "\n", encoding="utf8")
            print(json.dumps({"status": "prepared", "manifest": str(args.prepare)}))
            return 0
        threads = json.loads(args.threads.read_text(encoding="utf-8-sig"))
        report = verify(profile, threads, args.codex_home.resolve())
        output = args.state_dir / "runtime-verification.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 0 if report["status"] == "pass" else 1
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        print(json.dumps({"status": "unverified", "error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

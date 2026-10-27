"""Read exact Codex thread telemetry; never trust model self-identification."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path


def canonical_path(path: Path) -> Path:
    """Resolve links, then normalize Windows extended-length path spelling."""
    resolved = path.resolve()
    text = str(resolved)
    if os.name == "nt":
        if text.startswith("\\\\?\\UNC\\"):
            return Path("\\\\" + text[8:])
        if text.startswith("\\\\?\\"):
            return Path(text[4:])
    return resolved


def inspect(codex_home: Path, thread_id: str, model: str, effort: str) -> dict:
    result = {"thread_id": thread_id, "expected": {"model": model, "effort": effort},
              "status": "unverified", "evidence": "host_telemetry"}
    database = codex_home / "state_5.sqlite"
    if not database.is_file():
        return {**result, "reason": "host_database_unavailable"}
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT id, rollout_path, agent_path, model, reasoning_effort FROM threads WHERE id = ?",
                (thread_id,),
            ).fetchone()
        if row is None:
            return {**result, "reason": "thread_not_found"}
        result["agent_path"] = row["agent_path"]
        result["observed"] = {"model": row["model"], "effort": row["reasoning_effort"]}
        if not all(result["observed"].values()):
            return {**result, "reason": "thread_configuration_incomplete"}
        if result["observed"] != result["expected"]:
            return {**result, "status": "mismatch", "reason": "thread_configuration_mismatch"}
        rollout = canonical_path(Path(row["rollout_path"]))
        if not rollout.is_relative_to(canonical_path(codex_home / "sessions")):
            return {**result, "reason": "rollout_outside_sessions"}
        matching_turns = []
        assistant_outputs = 0
        session_matches = False
        active_turn = None
        with rollout.open(encoding="utf-8") as stream:
            for line in stream:
                item = json.loads(line)
                payload = item.get("payload", {})
                if item.get("type") == "session_meta":
                    if payload.get("id") != thread_id:
                        return {**result, "reason": "rollout_thread_identity_mismatch"}
                    session_matches = True
                if item.get("type") == "turn_context":
                    actual = {"model": payload.get("model"),
                              "effort": payload.get("effort", payload.get("reasoning_effort"))}
                    active_turn = payload.get("turn_id")
                    if not all(actual.values()) or not active_turn:
                        return {**result, "reason": "turn_configuration_incomplete"}
                    if actual != result["expected"]:
                        return {**result, "status": "mismatch", "reason": "turn_configuration_mismatch",
                                "observed_turn": actual}
                    matching_turns.append(active_turn)
                if active_turn and item.get("type") == "response_item" and payload.get("content") and (
                    payload.get("role") == "assistant" or payload.get("type") == "agent_message"
                ):
                    assistant_outputs += 1
        result.update(turn_ids=matching_turns, assistant_outputs=assistant_outputs)
        if session_matches and matching_turns and assistant_outputs:
            result["status"] = "pass"
            result["scope"] = "host_selected_model_and_effort_with_observed_assistant_output"
        else:
            result["reason"] = "no_completed_model_output_observed"
        return result
    except (OSError, sqlite3.Error, ValueError, TypeError) as error:
        return {**result, "reason": "telemetry_unavailable", "error_type": type(error).__name__}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True)
    args = parser.parse_args()
    result = inspect(args.codex_home.resolve(), args.thread_id, args.model, args.effort)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

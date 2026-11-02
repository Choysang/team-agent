"""Inspect native Codex context and retire legacy static-catalog overrides."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from contextlib import closing
import tomllib

from runtime_probe import canonical_path

START = "# team-agent context catalog:start"
END = "# team-agent context catalog:end"


def _positive(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _paths(state_dir: Path) -> tuple[Path, Path]:
    return state_dir / "context-overrides.json", state_dir / "context-models.json"


def _state(state_dir: Path, codex_home: Path) -> dict:
    path, _ = _paths(state_dir)
    if not path.exists():
        return {"schema_version": 1, "codex_home": str(codex_home.resolve()), "models": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("schema_version") != 1
            or data.get("codex_home") != str(codex_home.resolve())
            or not isinstance(data.get("models"), dict)
            or any(not isinstance(k, str) or not _positive(v) for k, v in data["models"].items())):
        raise ValueError("invalid context state or different CODEX_HOME; state was preserved")
    return data


def _config(codex_home: Path) -> tuple[bytes, str, dict]:
    path = codex_home / "config.toml"
    original = path.read_bytes() if path.exists() else b""
    text = original.decode("utf-8-sig")
    return original, text, tomllib.loads(text)


def _unmanaged_text(text: str) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != START:
        return text, False
    for index, line in enumerate(lines[1:], 1):
        if line.rstrip("\r\n") == END:
            return "".join(lines[index + 1:]), True
    raise ValueError("incomplete managed context catalog header")


def _base(codex_home: Path) -> dict:
    payload = json.loads((codex_home / "models_cache.json").read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ValueError("invalid models cache")
    models = payload["models"]
    if not models or any(not isinstance(item, dict) or not isinstance(item.get("slug"), str) for item in models):
        raise ValueError("invalid model entries")
    if len({item["slug"] for item in models}) != len(models):
        raise ValueError("duplicate model identifiers")
    return {"models": models}


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _retirement_outputs(state_dir: Path, codex_home: Path) -> dict[Path, bytes]:
    original, text, config = _config(codex_home)
    base_text, managed = _unmanaged_text(text)
    _, catalog_path = _paths(state_dir)
    pointer = config.get("model_catalog_json")
    if pointer is not None and (
        not managed or not isinstance(pointer, str)
        or Path(pointer).resolve() != catalog_path.resolve()
    ):
        raise ValueError("unmanaged model_catalog_json was preserved; reconcile it before retirement")
    if managed and "model_catalog_json" in tomllib.loads(base_text):
        raise ValueError("model_catalog_json outside the managed header was preserved")
    if not managed:
        return {}
    encoding = "utf-8-sig" if original.startswith(b"\xef\xbb\xbf") else "utf-8"
    return {codex_home / "config.toml": base_text.encode(encoding)}


def retire(state_dir: Path, codex_home: Path) -> dict:
    """Explicitly remove only our legacy pointer, retaining saved preferences."""
    outputs = _retirement_outputs(state_dir, codex_home)
    from team_adapter import commit_files
    backups = commit_files(outputs, state_dir) if outputs else []
    return {"status": "inactive" if _paths(state_dir)[0].exists() else "default",
            "changed": bool(outputs), "model_discovery": "native",
            "stored_preferences_retained": True, "restart_required": bool(outputs),
            "backup_files": backups}


def configure(state_dir: Path, codex_home: Path, model: str, window: int | None) -> dict:
    if window is not None:
        return {"status": "unsupported", "model": model,
                "requested_context_window": window, "host_settings_changed": False,
                "reason": "static_model_catalog_disables_remote_model_discovery",
                "alternative": "keep_native_model_catalog_and_context_defaults"}
    state = _state(state_dir, codex_home)
    if model not in state["models"]:
        return {"status": "default", "changed": False}
    updated = deepcopy(state)
    updated["models"].pop(model)
    outputs = _retirement_outputs(state_dir, codex_home)
    retired = bool(outputs)
    state_path, _ = _paths(state_dir)
    outputs[state_path] = _json_bytes(updated)
    from team_adapter import commit_files
    backups = commit_files(outputs, state_dir)
    return {"status": "inactive" if updated["models"] else "default",
            "changed": True, "model": model, "requested_context_window": None,
            "model_discovery": "native", "retired_shared_catalog": retired,
            "remaining_preferences_inactive": sorted(updated["models"]),
            "restart_required": retired, "backup_files": backups}


def report(state_dir: Path, codex_home: Path, model: str | None = None) -> dict:
    issues = []
    try:
        state = _state(state_dir, codex_home)
    except (OSError, ValueError) as error:
        state = {"models": {}}
        issues.append(f"legacy context preferences were not applied: {error}")
    _, text, config = _config(codex_home)
    try:
        _, managed = _unmanaged_text(text)
    except ValueError as error:
        managed = False
        issues.append(f"legacy context header needs repair: {error}")
    pointer = config.get("model_catalog_json")
    if pointer is not None:
        issues.append("model_catalog_json disables remote model discovery")
    if "model_context_window" in config:
        issues.append("global model_context_window is not a per-model override")
    rows = []
    for item in _base(codex_home)["models"]:
        slug = item["slug"]
        if model is not None and slug != model:
            continue
        window = item.get("context_window") if pointer is None else None
        percent = item.get("effective_context_window_percent")
        estimate = window * percent // 100 if _positive(window) and _positive(percent) and percent <= 100 else None
        rows.append({"model": slug, "catalog_default_window": item.get("context_window"),
                     "catalog_maximum_window": item.get("max_context_window"),
                     "requested_window": state["models"].get(slug),
                     "configured_window": None, "effective_window_estimate": estimate,
                     "estimate_source": "catalog_metadata_not_runtime_telemetry"})
    if model is not None and not rows:
        raise ValueError(f"unknown model: {model}")
    status = ("legacy_static_catalog" if managed else "external_catalog") if pointer is not None else (
        "inactive" if state["models"] else "default")
    return {"status": status, "scope": "native_model_catalog",
            "subagent_window_override_supported": False, "per_model_override_supported": False,
            "host_model_override_scope": None, "automatic_prompt": False, "recommendations": [],
            "blocks_remote_model_discovery": pointer is not None,
            "migration_command": "context-retire" if managed else None,
            "global_context_window": config.get("model_context_window"),
            "global_auto_compact_token_limit": config.get("model_auto_compact_token_limit"),
            "models": rows, "issues": issues, "startup_only": True,
            "runtime_status": "unverified_without_thread_telemetry"}


def observe(codex_home: Path, thread_id: str, expected_model: str | None = None) -> dict:
    """Read reported context capacity, without pretending to test long-input quality."""
    result = {"status": "unverified", "thread_id": thread_id}
    try:
        with closing(sqlite3.connect((codex_home / "state_5.sqlite").resolve().as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute("SELECT rollout_path,model FROM threads WHERE id=?", (thread_id,)).fetchone()
        if not row:
            return {**result, "reason": "thread_not_found"}
        if not isinstance(row[1], str) or not row[1]:
            return {**result, "reason": "thread_model_unavailable"}
        if expected_model is not None and row[1] != expected_model:
            return {**result, "status": "mismatch", "reason": "requested_model_mismatch",
                    "expected_model": expected_model, "observed_model": row[1]}
        path = canonical_path(Path(row[0]))
        if not path.is_relative_to(canonical_path(codex_home / "sessions")):
            return {**result, "reason": "rollout_outside_sessions"}
        session_ok = False
        turn_model = None
        observed = None
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("invalid telemetry event")
                payload = event.get("payload", {})
                if not isinstance(payload, dict):
                    raise ValueError("invalid telemetry payload")
                if event.get("type") == "session_meta":
                    if payload.get("id") != thread_id:
                        return {**result, "reason": "thread_identity_mismatch"}
                    session_ok = True
                elif event.get("type") == "turn_context":
                    turn_model = payload.get("model") if payload.get("turn_id") else None
                    observed = None
                elif event.get("type") == "event_msg" and payload.get("type") == "token_count":
                    info = payload.get("info") or {}
                    if not isinstance(info, dict):
                        raise ValueError("invalid telemetry token count")
                    window = info.get("model_context_window")
                    if turn_model and turn_model == row[1] and _positive(window):
                        observed = window
        if session_ok and observed is not None:
            return {**result, "status": "observed", "model": row[1],
                    "reported_context_window": observed,
                    "scope": "host_reported_capacity_not_long_input_acceptance_or_quality"}
        return {**result, "reason": "no_matching_context_observation"}
    except (OSError, sqlite3.Error, ValueError, TypeError) as error:
        return {**result, "reason": "telemetry_unavailable", "error_type": type(error).__name__}

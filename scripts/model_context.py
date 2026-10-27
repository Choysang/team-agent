"""Opt-in, model-scoped Codex context metadata; never rewrite global limits."""
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


def _check_pointer(config: dict, managed: bool, catalog_path: Path, active: bool) -> None:
    pointer = config.get("model_catalog_json")
    if active:
        if not managed or not isinstance(pointer, str) or Path(pointer).resolve() != catalog_path.resolve():
            raise ValueError("managed context catalog is no longer selected; reconcile config before updating")
    elif managed or pointer is not None:
        raise ValueError("existing model_catalog_json needs reconciliation; it was not overwritten")
    if "model_context_window" in config:
        raise ValueError("global model_context_window would override model-scoped settings; it was not changed")


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


def render(codex_home: Path, overrides: dict[str, int]) -> dict:
    catalog = deepcopy(_base(codex_home))
    models = {item["slug"]: item for item in catalog["models"]}
    for slug, window in overrides.items():
        if slug not in models:
            raise ValueError(f"configured context model is absent from current catalog: {slug}")
        model = models[slug]
        maximum = model.get("max_context_window")
        if not _positive(maximum):
            maximum = model.get("context_window")
        if not _positive(window) or not _positive(maximum) or window > maximum:
            raise ValueError(f"window for {slug} must be a positive integer within its catalog limit {maximum}")
        model["context_window"] = window
    return catalog


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def refresh(state_dir: Path, codex_home: Path) -> dict:
    """Rebase explicit overrides on the current catalog without freezing other models."""
    state = _state(state_dir, codex_home)
    if not state["models"]:
        return {"status": "default", "changed": False}
    _, text, config = _config(codex_home)
    _, managed = _unmanaged_text(text)
    _, catalog_path = _paths(state_dir)
    _check_pointer(config, managed, catalog_path, True)
    content = _json_bytes(render(codex_home, state["models"]))
    changed = not catalog_path.exists() or catalog_path.read_bytes() != content
    if changed:
        from team_adapter import commit_files
        commit_files({catalog_path: content}, state_dir)
    return {"status": "configured", "changed": changed, "startup_only": True}


def configure(state_dir: Path, codex_home: Path, model: str, window: int | None) -> dict:
    state = _state(state_dir, codex_home)
    if window is None and model not in state["models"]:
        return {"status": "default", "changed": False}
    original, text, config = _config(codex_home)
    base_text, managed = _unmanaged_text(text)
    state_path, catalog_path = _paths(state_dir)
    _check_pointer(config, managed, catalog_path, bool(state["models"]))
    updated = deepcopy(state)
    if window is None:
        updated["models"].pop(model)
    else:
        updated["models"][model] = window
    catalog = render(codex_home, updated["models"]) if updated["models"] else None
    newline = "\r\n" if "\r\n" in text else "\n"
    header = ""
    if updated["models"]:
        header = newline.join([START,
            "model_catalog_json = " + json.dumps(catalog_path.resolve().as_posix(), ensure_ascii=False),
            END, ""])
    rewritten = header + base_text
    tomllib.loads(rewritten)
    encoding = "utf-8-sig" if original.startswith(b"\xef\xbb\xbf") else "utf-8"
    from team_adapter import commit_files
    outputs = {codex_home / "config.toml": rewritten.encode(encoding),
               state_path: _json_bytes(updated)}
    if catalog is not None:
        outputs = {catalog_path: _json_bytes(catalog), **outputs}
    changed = any(not p.exists() or p.read_bytes() != data for p, data in outputs.items())
    backups = commit_files(outputs, state_dir)
    return {"status": "configured" if updated["models"] else "default", "changed": changed,
            "model": model, "requested_context_window": window,
            "scope": "host_model_catalog_including_root_and_subagent_sessions",
            "startup_only": True, "runtime_status": "unverified", "backup_files": backups}


def report(state_dir: Path, codex_home: Path, model: str | None = None) -> dict:
    state = _state(state_dir, codex_home)
    _, text, config = _config(codex_home)
    _, managed = _unmanaged_text(text)
    _, catalog_path = _paths(state_dir)
    issues = []
    try:
        _check_pointer(config, managed, catalog_path, bool(state["models"]))
    except ValueError as error:
        issues.append(str(error))
    rows = []
    for item in _base(codex_home)["models"]:
        slug = item["slug"]
        if model is not None and slug != model:
            continue
        window = state["models"].get(slug, item.get("context_window"))
        percent = item.get("effective_context_window_percent")
        estimate = window * percent // 100 if _positive(window) and _positive(percent) and percent <= 100 else None
        rows.append({"model": slug, "catalog_default_window": item.get("context_window"),
                     "catalog_maximum_window": item.get("max_context_window"),
                     "configured_window": state["models"].get(slug),
                     "effective_window_estimate": estimate,
                     "estimate_source": "catalog_metadata_not_runtime_telemetry"})
    if model is not None and not rows:
        raise ValueError(f"unknown model: {model}")
    recommendations = []
    if not issues:
        for row in rows:
            current = row["configured_window"] or row["catalog_default_window"]
            maximum = row["catalog_maximum_window"]
            if (row["model"] == "gpt-6-luna" and _positive(current) and current < 800000
                    and _positive(maximum) and maximum >= 800000):
                recommendations.append({
                    "id": "luna_context_800k", "model": "gpt-6-luna",
                    "current_configured_window": current, "suggested_window": 800000,
                    "scope": "host_model_root_and_subagent_sessions",
                    "requires_user_selection": True,
                    "reason": "more_context_space_for_long_tasks_not_guaranteed_quality_improvement",
                    "evidence": "host_configuration_and_catalog_not_live_thread",
                })
    return {"status": "needs_reconciliation" if issues else "configured" if state["models"] else "default",
            "scope": "per_model_startup_catalog_not_per_role_spawn_parameter",
            "subagent_window_override_supported": False,
            "host_model_override_scope": "root_and_subagent_sessions_using_the_model",
            "automatic_prompt": bool(recommendations), "recommendations": recommendations,
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

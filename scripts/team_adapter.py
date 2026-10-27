#!/usr/bin/env python3
"""Compile a versioned role profile into Codex standalone-agent TOML files."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
from external_catalog import discover as discover_external_catalog
from state_lock import state_lock

ROLE_NAMES = (
    "Explorer", "Executor", "Reviewer", "ExpertAdvisor",
    "Researcher", "GeneralWorker", "Designer",
)
ROLE_TEXT: dict[str, tuple[str, str]] = {
    "Explorer": (
        "Explores code and gathers evidence for the assigned task.",
        "Inspect relevant files and report verified findings, paths, and open questions.",
    ),
    "Executor": (
        "Implements the assigned change in the authorized files.",
        "Make the requested change, preserve unrelated work, and report what changed and how it was checked.",
    ),
    "Reviewer": (
        "Reviews the assigned change for concrete defects and risks.",
        "Review the relevant diff and report only actionable findings with file and line references.",
    ),
    "ExpertAdvisor": (
        "Provides focused expert analysis for the assigned task.",
        "Analyze the question using available evidence and state uncertainty where it remains.",
    ),
    "Researcher": (
        "Researches the assigned topic and gathers source-backed evidence.",
        "Use reliable sources, distinguish evidence from inference, and report citations or exact source paths.",
    ),
    "GeneralWorker": (
        "Handles an assigned subtask that has no more specific role.",
        "Complete the assigned subtask within its stated scope and report the result clearly.",
    ),
    "Designer": (
        "Develops visual and interaction direction for the assigned task.",
        "Inspect the existing product context and provide or implement a coherent design within the stated scope.",
    ),
}
ROLE_FIELDS = {"model", "effort", "fallbacks", "active_index", "skip_fallback_prompts"}
REQUIRED_ROLE_FIELDS = ROLE_FIELDS - {"skip_fallback_prompts"}
PROFILE_SCHEMA_VERSION = 2
CATALOG_STATE_VERSION = 1
SWITCHABLE_REASONS = {"quota_exceeded", "rate_limit"}
GLOBAL_QUOTA_REASONS = {
    "global_quota_exceeded", "global_quota_insufficient",
    "workspace_quota_exceeded", "workspace_quota_insufficient",
    "workspace_quota_exhausted",
}
CATALOG_NOTICE: dict[str, Any] | None = None
CATALOG_ERROR: str | None = None


class AdapterError(Exception):
    """An expected user-facing adapter error."""


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        emit({"status": "error", "error": message})
        raise SystemExit(2)


def emit(value: Any) -> None:
    payload = dict(value) if isinstance(value, dict) else {"result": value}
    if CATALOG_NOTICE is not None:
        payload.setdefault("catalog_notice", CATALOG_NOTICE)
    line = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    binary_stdout = getattr(sys.stdout, "buffer", None)
    if binary_stdout is not None:
        binary_stdout.write(line.encode("utf-8"))
        binary_stdout.flush()
    else:
        sys.stdout.write(line)
        sys.stdout.flush()


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AdapterError(f"{label} not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError(f"cannot read {label} at {path}: {exc}") from exc


def _cache_payload(codex_home: Path) -> dict[str, Any]:
    path = codex_home / "models_cache.json"
    payload = _read_json(path, "models_cache.json")
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise AdapterError(f"invalid models cache schema: {path}")
    return payload


def _model_efforts(item: Mapping[str, Any]) -> set[str]:
    levels = item.get("supported_reasoning_levels")
    if not isinstance(levels, list):
        raise AdapterError(f"invalid supported_reasoning_levels for model {item.get('slug')}")
    efforts: set[str] = set()
    for level in levels:
        effort = level.get("effort") if isinstance(level, dict) else level
        if isinstance(effort, str) and effort:
            efforts.add(effort)
    return efforts


def load_catalog(codex_home: Path) -> dict[str, set[str]]:
    payload = _cache_payload(codex_home)
    catalog: dict[str, set[str]] = {}
    for item in payload["models"]:
        if not isinstance(item, dict) or item.get("visibility") != "list":
            continue
        slug = item.get("slug")
        if isinstance(slug, str) and slug:
            catalog[slug] = _model_efforts(item)
    if not catalog:
        raise AdapterError(
            "models_cache.json contains no visibility=list models; catalog state was preserved: "
            + str(codex_home / "models_cache.json")
        )
    return catalog


def _fast_advertised(item: Mapping[str, Any]) -> bool | None:
    tiers = item.get("service_tiers")
    speeds = item.get("additional_speed_tiers")
    if not isinstance(tiers, list) and not isinstance(speeds, list):
        return None
    names = {tier.get("id") for tier in (tiers if isinstance(tiers, list) else [])
             if isinstance(tier, dict) and isinstance(tier.get("id"), str)}
    names.update(tier for tier in (speeds if isinstance(speeds, list) else []) if isinstance(tier, str))
    return bool(names & {"fast", "priority"})


def public_models(codex_home: Path) -> list[dict[str, Any]]:
    payload = _cache_payload(codex_home)
    result = [
        {
            "slug": item.get("slug"),
            "supported_reasoning_levels": item.get("supported_reasoning_levels"),
            "context_window": item.get("context_window"),
            "max_context_window": item.get("max_context_window"),
            "effective_context_window_percent": item.get("effective_context_window_percent"),
            "fast_advertised": _fast_advertised(item),
            "service_tiers": item.get("service_tiers"),
        }
        for item in payload["models"]
        if isinstance(item, dict) and item.get("visibility") == "list"
    ]
    if not result:
        raise AdapterError(
            "models_cache.json contains no visibility=list models; catalog state was preserved: "
            + str(codex_home / "models_cache.json")
        )
    return result


def semantic_catalog(codex_home: Path) -> tuple[dict[str, set[str]], list[dict[str, Any]], str]:
    catalog = load_catalog(codex_home)
    models = [
        {"slug": slug, "supported_reasoning_levels": sorted(efforts)}
        for slug, efforts in sorted(catalog.items())
    ]
    encoded = json.dumps(models, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return catalog, models, fingerprint


def _catalog_changes(
    previous: list[dict[str, Any]], current: list[dict[str, Any]]
) -> dict[str, Any]:
    old = {item["slug"]: set(item["supported_reasoning_levels"]) for item in previous}
    new = {item["slug"]: set(item["supported_reasoning_levels"]) for item in current}
    added = [
        {"slug": slug, "supported_reasoning_levels": sorted(new[slug])}
        for slug in sorted(new.keys() - old.keys())
    ]
    removed = [
        {"slug": slug, "supported_reasoning_levels": sorted(old[slug])}
        for slug in sorted(old.keys() - new.keys())
    ]
    changed = [
        {
            "slug": slug,
            "previous_reasoning_levels": sorted(old[slug]),
            "supported_reasoning_levels": sorted(new[slug]),
            "added_reasoning_levels": sorted(new[slug] - old[slug]),
            "removed_reasoning_levels": sorted(old[slug] - new[slug]),
        }
        for slug in sorted(old.keys() & new.keys())
        if old[slug] != new[slug]
    ]
    return {"added_models": added, "removed_models": removed, "changed_models": changed}


def _catalog_state_path(state_dir: Path) -> Path:
    return state_dir / "catalog_state.json"


def _read_catalog_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = _read_json(path, "catalog state")
    if (
        not isinstance(payload, dict)
        or payload.get("version") != CATALOG_STATE_VERSION
        or not isinstance(payload.get("last_seen_models"), list)
        or not isinstance(payload.get("last_seen_fingerprint"), str)
        or not isinstance(payload.get("pending"), list)
        or not isinstance(payload.get("acknowledged"), dict)
    ):
        raise AdapterError(f"invalid catalog state; refusing to reset it: {path}")
    return payload


def _profile_roles_for_notice(state_dir: Path) -> dict[str, Any] | None:
    path = state_dir / "profile.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    roles = payload.get("roles") if isinstance(payload, dict) else None
    return roles if isinstance(roles, dict) else None


def _notice_from_state(state: Mapping[str, Any], state_dir: Path) -> dict[str, Any]:
    pending = list(state["pending"])
    notice: dict[str, Any] = {
        "status": "pending_change" if pending else "unchanged",
        "fingerprint": state["last_seen_fingerprint"],
        "pending": pending,
    }
    if pending:
        notice["current_roles"] = _profile_roles_for_notice(state_dir)
        notice["current_models"] = state["last_seen_models"]
        notice["dispatch_compatibility"] = (
            "not_verified_against_current_session_spawn_interface"
        )
    return notice


def check_catalog(state_dir: Path, codex_home: Path) -> dict[str, Any]:
    global CATALOG_ERROR
    try:
        _, models, fingerprint = semantic_catalog(codex_home)
    except AdapterError as exc:
        CATALOG_ERROR = str(exc)
        try:
            state = _read_catalog_state(_catalog_state_path(state_dir))
        except AdapterError:
            state = None
        notice: dict[str, Any] = {
            "status": "unavailable",
            "error": CATALOG_ERROR,
            "pending": list(state["pending"]) if state else [],
        }
        if state and state["pending"]:
            notice["fingerprint"] = state["last_seen_fingerprint"]
            notice["current_roles"] = _profile_roles_for_notice(state_dir)
            notice["current_models"] = state["last_seen_models"]
            notice["dispatch_compatibility"] = (
                "not_verified_against_current_session_spawn_interface"
            )
        return notice

    CATALOG_ERROR = None
    path = _catalog_state_path(state_dir)
    state = _read_catalog_state(path)
    if state is None:
        state = {
            "version": CATALOG_STATE_VERSION,
            "baseline_fingerprint": fingerprint,
            "last_seen_fingerprint": fingerprint,
            "last_seen_models": models,
            "acknowledged": {},
            "pending": [],
        }
        commit_files({
            path: (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        }, state_dir)
        return {
            "status": "baseline_established",
            "fingerprint": fingerprint,
            "pending": [],
        }

    pending = list(state["pending"])
    acknowledged = state["acknowledged"]
    if fingerprint != state["last_seen_fingerprint"]:
        known_pending = {item.get("fingerprint") for item in pending if isinstance(item, dict)}
        if fingerprint not in acknowledged and fingerprint not in known_pending:
            pending.append({
                "fingerprint": fingerprint,
                "previous_fingerprint": state["last_seen_fingerprint"],
                "changes": _catalog_changes(state["last_seen_models"], models),
            })
        state["last_seen_fingerprint"] = fingerprint
        state["last_seen_models"] = models
        state["pending"] = pending
        commit_files({
            path: (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        }, state_dir)
    return _notice_from_state(state, state_dir)


def _validate_model_effort(
    model: Any, effort: Any, label: str, catalog: Mapping[str, set[str]] | None
) -> dict[str, str]:
    if not isinstance(model, str) or not model:
        raise AdapterError(f"{label}.model must be a non-empty model slug")
    if not isinstance(effort, str) or not effort:
        raise AdapterError(f"{label}.effort must be a non-empty reasoning level")
    if catalog is not None:
        if model not in catalog:
            raise AdapterError(f"{label}.model is not visibility=list in models_cache.json: {model}")
        if effort not in catalog[model]:
            supported = ", ".join(sorted(catalog[model])) or "(none listed)"
            raise AdapterError(
                f"{label}.effort {effort!r} is unsupported for {model}; supported: {supported}"
            )
    return {"model": model, "effort": effort}


def normalize_profile(payload: Any, catalog: Mapping[str, set[str]] | None) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise AdapterError(f"profile must be a JSON object with schema_version={PROFILE_SCHEMA_VERSION}")
    roles = payload.get("roles")
    if not isinstance(roles, dict):
        raise AdapterError("profile.roles must be an object")
    global_skip_fallback_prompts = payload.get("skip_fallback_prompts", False)
    if not isinstance(global_skip_fallback_prompts, bool):
        raise AdapterError("profile.skip_fallback_prompts must be a boolean")
    missing = [role for role in ROLE_NAMES if role not in roles]
    extra = [role for role in roles if role not in ROLE_NAMES]
    if missing or extra:
        details = []
        if missing:
            details.append("missing roles: " + ", ".join(missing))
        if extra:
            details.append("unknown roles: " + ", ".join(map(str, extra)))
        raise AdapterError("; ".join(details))

    normalized_roles: dict[str, Any] = {}
    for role_name in ROLE_NAMES:
        role = roles[role_name]
        if (
            not isinstance(role, dict)
            or not REQUIRED_ROLE_FIELDS.issubset(role)
            or set(role) - ROLE_FIELDS
        ):
            raise AdapterError(
                f"roles.{role_name} must contain model, effort, fallbacks, active_index, "
                "and optional skip_fallback_prompts"
            )
        if "skip_fallback_prompts" in role and not isinstance(role["skip_fallback_prompts"], bool):
            raise AdapterError(f"roles.{role_name}.skip_fallback_prompts must be a boolean")
        primary = _validate_model_effort(
            role["model"], role["effort"], f"roles.{role_name}", catalog
        )
        fallbacks = role["fallbacks"]
        if not isinstance(fallbacks, list):
            raise AdapterError(f"roles.{role_name}.fallbacks must be an array")
        normalized_fallbacks = []
        for index, fallback in enumerate(fallbacks):
            if not isinstance(fallback, dict) or set(fallback) != {"model", "effort"}:
                raise AdapterError(
                    f"roles.{role_name}.fallbacks[{index}] must contain exactly model and effort"
                )
            normalized_fallbacks.append(_validate_model_effort(
                fallback["model"], fallback["effort"],
                f"roles.{role_name}.fallbacks[{index}]", catalog,
            ))
        active_index = role["active_index"]
        if isinstance(active_index, bool) or not isinstance(active_index, int):
            raise AdapterError(f"roles.{role_name}.active_index must be an integer")
        if active_index < 0 or active_index > len(normalized_fallbacks):
            raise AdapterError(
                f"roles.{role_name}.active_index must be between 0 and {len(normalized_fallbacks)}"
            )
        normalized_role = {
            "model": primary["model"],
            "effort": primary["effort"],
            "fallbacks": normalized_fallbacks,
            "active_index": active_index,
        }
        if "skip_fallback_prompts" in role:
            normalized_role["skip_fallback_prompts"] = role["skip_fallback_prompts"]
        normalized_roles[role_name] = normalized_role
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "skip_fallback_prompts": global_skip_fallback_prompts,
        "roles": normalized_roles,
    }


def profile_revision(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def effective_fallback_prompt_settings(profile: Mapping[str, Any]) -> dict[str, Any]:
    global_setting = bool(profile.get("skip_fallback_prompts", False))
    roles = profile.get("roles", {})
    per_role: dict[str, Any] = {}
    for role_name in ROLE_NAMES:
        role = roles.get(role_name, {}) if isinstance(roles, Mapping) else {}
        override = role.get("skip_fallback_prompts") if isinstance(role, Mapping) else None
        per_role[role_name] = {
            "override": override,
            "effective": global_setting if override is None else override,
        }
    return {
        "global": global_setting,
        "roles": per_role,
    }


def serialize_profile(profile: Mapping[str, Any]) -> bytes:
    return (json.dumps(profile, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def agent_toml(role_name: str, model: str, effort: str) -> bytes:
    description, instructions = ROLE_TEXT[role_name]
    text = (
        f"name = {_toml_string('team-' + role_name.lower())}\n"
        f"description = {_toml_string(description)}\n"
        f"developer_instructions = {_toml_string(instructions)}\n"
        f"model = {_toml_string(model)}\n"
        f"model_reasoning_effort = {_toml_string(effort)}\n"
    )
    return text.encode("utf-8")


def active_choice(role: Mapping[str, Any]) -> dict[str, str]:
    chain = [{"model": role["model"], "effort": role["effort"]}, *role["fallbacks"]]
    return chain[role["active_index"]]


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".team-agent-", suffix=".tmp", dir=path.parent, delete=False
        ) as handle:
            temporary_name = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except OSError:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise


def commit_files(outputs: Mapping[Path, bytes], state_dir: Path) -> list[str]:
    changed: dict[Path, bytes] = {}
    originals: dict[Path, bytes | None] = {}
    for path, data in outputs.items():
        if path.is_symlink():
            raise AdapterError(f"refusing to replace symlink: {path}")
        if path.exists() and not path.is_file():
            raise AdapterError(f"output path exists and is not a regular file: {path}")
        previous = path.read_bytes() if path.exists() else None
        if previous != data:
            changed[path] = data
            originals[path] = previous

    backups: list[str] = []
    try:
        for path, original in originals.items():
            if original is None:
                continue
            digest = hashlib.sha256(original).hexdigest()[:12]
            backup = state_dir / "backups" / f"{path.name}.{digest}.bak"
            if backup.exists():
                if not backup.is_file() or backup.read_bytes() != original:
                    raise AdapterError(f"backup path collision: {backup}")
            else:
                _atomic_write(backup, original)
            backups.append(str(backup))

        replaced: list[Path] = []
        try:
            for path, data in changed.items():
                _atomic_write(path, data)
                replaced.append(path)
        except OSError as exc:
            restore_errors = []
            for path in reversed(replaced):
                try:
                    original = originals[path]
                    if original is None:
                        path.unlink(missing_ok=True)
                    else:
                        _atomic_write(path, original)
                except OSError as restore_exc:
                    restore_errors.append(f"{path}: {restore_exc}")
            detail = f"write failed: {exc}"
            if restore_errors:
                detail += "; rollback failed: " + "; ".join(restore_errors)
            raise AdapterError(detail) from exc
    except AdapterError:
        raise
    except OSError as exc:
        raise AdapterError(f"cannot create backup or write outputs: {exc}") from exc
    return backups


def _agent_paths(codex_home: Path) -> tuple[Path, dict[str, Path]]:
    agents_dir = codex_home / "agents"
    if agents_dir.is_symlink():
        raise AdapterError(f"refusing to write through symlinked agents directory: {agents_dir}")
    return agents_dir, {
        role: agents_dir / f"team-{role.lower()}.toml" for role in ROLE_NAMES
    }


def _automatic_binding_outputs(
    state_dir: Path, codex_home: Path, profile: dict[str, Any]
) -> dict[Path, bytes]:
    marker = state_dir / "host-bindings.json"
    if not marker.exists():
        return {}
    binding_state = _read_json(marker, "host binding state")
    if not isinstance(binding_state, dict) or binding_state.get("enabled") is not True:
        return {}
    try:
        from bind_roles import plan_bindings

        return plan_bindings(codex_home, profile, scan_host_agents(codex_home))
    except (OSError, ValueError, KeyError) as exc:
        raise AdapterError(f"cannot update managed host bindings: {exc}") from exc


def _profile_file(state_dir: Path) -> Path:
    return state_dir / "profile.json"


def _load_current_profile(
    state_dir: Path, codex_home: Path, require_catalog: bool = False
) -> dict[str, Any]:
    payload = _read_json(_profile_file(state_dir), "profile")
    catalog = None
    if require_catalog:
        if CATALOG_ERROR:
            raise AdapterError("model catalog unavailable; dispatch/configuration validation blocked: " + CATALOG_ERROR)
        catalog = load_catalog(codex_home)
    return normalize_profile(payload, catalog)


def _load_profile_document(
    state_dir: Path, codex_home: Path, require_catalog: bool = False
) -> tuple[dict[str, Any], str]:
    payload = _read_json(_profile_file(state_dir), "profile")
    catalog = None
    if require_catalog:
        if CATALOG_ERROR:
            raise AdapterError(
                "model catalog unavailable; dispatch/configuration validation blocked: "
                + CATALOG_ERROR
            )
        catalog = load_catalog(codex_home)
    return normalize_profile(payload, catalog), profile_revision(payload)


def _fallback_prompt_preferences(state_dir: Path) -> dict[str, Any]:
    profile_path = _profile_file(state_dir)
    if profile_path.exists():
        profile = normalize_profile(_read_json(profile_path, "profile"), None)
    else:
        profile = {"skip_fallback_prompts": False, "roles": {}}
    return effective_fallback_prompt_settings(profile)


def cmd_models(
    codex_home: Path, state_dir: Path, dispatch_models: set[str] | None = None,
) -> int:
    external_discovery = discover_external_catalog(codex_home)
    if CATALOG_ERROR:
        emit({
            "status": "unavailable",
            "error": "model catalog unavailable: " + CATALOG_ERROR,
            "models": [],
            "external_discovery": external_discovery,
            "fallback_prompt_preferences": _fallback_prompt_preferences(state_dir),
        })
        return 1
    try:
        native_models = public_models(codex_home)
    except AdapterError as exc:
        emit({
            "status": "unavailable",
            "error": str(exc),
            "models": [],
            "external_discovery": external_discovery,
            "fallback_prompt_preferences": _fallback_prompt_preferences(state_dir),
        })
        return 1
    for item in native_models:
        item["dispatch_supported"] = (
            item["slug"] in dispatch_models if dispatch_models is not None else None
        )
    from model_context import report as context_report
    config_path = codex_home / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8-sig")) if config_path.exists() else {}
    current_roles = _profile_roles_for_notice(state_dir)
    emit({
        "status": "ok",
        "models": native_models,
        "external_discovery": external_discovery,
        "fallback_prompt_preferences": _fallback_prompt_preferences(state_dir),
        "first_use_check": {
            "dimensions": ["model", "reasoning_effort", "fast", "context_window"],
            "existing_configuration": _profile_file(state_dir).exists(),
            "configuration_readable": current_roles is not None,
            "unconfigured_roles": [role for role in ROLE_NAMES if role not in current_roles] if current_roles is not None else None,
            "current_roles": current_roles,
            "dispatch_enum_supplied": dispatch_models is not None,
            "selectable_models": [item["slug"] for item in native_models if item["dispatch_supported"] is True],
            "unset_optional_settings": "preserve_host_defaults_without_prompting",
        },
        "fast": {
            "scope": "root_session_preference_inherited_by_subagents",
            "per_subagent_override_supported": False,
            "configured_service_tier": config.get("service_tier"),
            "fast_mode_feature": config.get("features", {}).get("fast_mode"),
            "runtime_status": "unverified",
        },
        "context": context_report(state_dir, codex_home),
    })
    return 0


def cmd_sync(profile_path: Path, state_dir: Path, codex_home: Path) -> int:
    if CATALOG_ERROR:
        raise AdapterError("model catalog unavailable; sync blocked: " + CATALOG_ERROR)
    catalog = load_catalog(codex_home)
    profile = normalize_profile(_read_json(profile_path, "profile"), catalog)
    _, paths = _agent_paths(codex_home)
    outputs: dict[Path, bytes] = {}
    for role_name in ROLE_NAMES:
        choice = active_choice(profile["roles"][role_name])
        outputs[paths[role_name]] = agent_toml(role_name, choice["model"], choice["effort"])
    outputs[_profile_file(state_dir)] = serialize_profile(profile)
    outputs.update(_automatic_binding_outputs(state_dir, codex_home, profile))
    backups = commit_files(outputs, state_dir)
    emit({
        "status": "ok",
        "configured": list(ROLE_NAMES),
        "runtime_status": "runtime_unverified",
        "backup_files": backups,
        "profile": str(_profile_file(state_dir)),
        "codex_agents_dir": str(codex_home / "agents"),
    })
    return 0


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def _tokens(text: str) -> set[str]:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return set(re.findall(r"[a-z0-9]+", separated.casefold()))


@lru_cache(maxsize=1)
def reviewed_role_families() -> dict[str, dict[str, str]]:
    path = Path(__file__).resolve().parents[1] / "references" / "role-families.json"
    data = _read_json(path, "reviewed role families")
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("groups"), list):
        raise AdapterError("invalid reviewed role families schema")
    entries: dict[str, dict[str, str]] = {}
    for group in data["groups"]:
        if not isinstance(group, dict) or not isinstance(group.get("agents"), list):
            raise AdapterError("invalid reviewed role family")
        overrides = group.get("overrides", {})
        if not isinstance(overrides, dict):
            raise AdapterError("invalid reviewed role overrides")
        for name in group["agents"]:
            profile = overrides.get(name, group.get("default_profile"))
            if not isinstance(name, str) or not name or name.casefold() in entries or profile not in ROLE_NAMES:
                raise AdapterError("invalid or duplicate reviewed role mapping")
            if not isinstance(group.get("id"), str) or not isinstance(group.get("label"), str):
                raise AdapterError("reviewed role family requires id and label")
            entries[name.casefold()] = {
                "profile": profile, "family": group["id"], "family_label": group["label"],
            }
    return entries


def reviewed_role_family(name: str, stem: str) -> dict[str, str] | None:
    entries = reviewed_role_families()
    return entries.get(name.casefold()) or entries.get(stem.casefold())


def infer_host_role(name: str, stem: str) -> str | None:
    native_agent_roles = {
        "worker": "Executor",
        "default": "GeneralWorker",
        "explorer": "Explorer",
    }
    native_role = native_agent_roles.get(_norm(stem))
    if native_role is not None:
        return native_role
    reviewed = reviewed_role_family(name, stem)
    if reviewed is not None:
        return reviewed["profile"]
    for candidate in (name, stem):
        normalized = _norm(candidate)
        for role in ROLE_NAMES:
            if normalized == _norm(role):
                return role
    tokens = _tokens(name) | _tokens(stem)
    if "default" in tokens:
        return "GeneralWorker"
    if "worker" in tokens:
        return "Executor"
    if {"docs", "lookup"} <= tokens:
        return "Researcher"
    if {"build", "resolver"} <= tokens:
        return "Executor"
    priorities = (
        ("Reviewer", {"reviewer", "review"}),
        ("Explorer", {"explorer"}),
        ("Researcher", {"researcher", "research"}),
        ("Designer", {"designer", "design"}),
        ("Executor", {"executor", "implementer"}),
        ("ExpertAdvisor", {"expertadvisor", "advisor", "planner", "architect"}),
        ("GeneralWorker", {"generalworker", "general"}),
    )
    for role, indicators in priorities:
        if tokens & indicators:
            return role
    return None


def scan_host_agents(codex_home: Path) -> list[dict[str, Any]]:
    agents_dir = codex_home / "agents"
    if not agents_dir.is_dir():
        return []
    result = []
    for path in sorted(agents_dir.glob("*.toml")):
        if path.stem.casefold().startswith("team-"):
            continue
        try:
            with path.open("rb") as handle:
                data = tomllib.load(handle)
            name = data.get("name")
            if not isinstance(name, str) or not name:
                name = path.stem
            mapped = infer_host_role(name, path.stem)
            family = reviewed_role_family(name, path.stem)
            result.append({
                "host_role": name,
                "file": str(path),
                "mapped_role": mapped or "GeneralWorker",
                "coverage": "mapped" if mapped else "fallback",
                "family": family["family"] if family else None,
                "family_label": family["family_label"] if family else None,
                "classification_source": (
                    "builtin" if path.stem in ("default", "worker", "explorer")
                    else "reviewed_instructions" if family else "name_hint" if mapped else "fallback"
                ),
                "explicit_override": {
                    key: data[key]
                    for key in ("model", "model_reasoning_effort")
                    if isinstance(data.get(key), str) and data[key]
                },
            })
        except (OSError, tomllib.TOMLDecodeError) as exc:
            result.append({
                "host_role": path.stem,
                "file": str(path),
                "mapped_role": "GeneralWorker",
                "coverage": "fallback",
                "error": str(exc),
            })
    return result


def role_coverage(codex_home: Path) -> dict[str, Any]:
    entries = scan_host_agents(codex_home)
    mapped = sum(item["coverage"] == "mapped" for item in entries)
    family_counts: dict[str, int] = {}
    for item in entries:
        if item.get("family"):
            family_counts[item["family"]] = family_counts.get(item["family"], 0) + 1
    return {
        "total_host_roles": len(entries),
        "mapped_host_roles": mapped,
        "fallback_host_roles": len(entries) - mapped,
        "reviewed_specialist_roles": sum(item.get("classification_source") == "reviewed_instructions" for item in entries),
        "family_counts": family_counts,
        "host_roles": entries,
    }


def resolve_role(
    requested: str,
    codex_home: Path,
    loaded_agent_types: set[str] | None = None,
) -> dict[str, Any]:
    """Resolve a role to the exact host agent type that should be dispatched.

    ``loaded_agent_types`` is current-session evidence when supplied. Without
    it, a matching file can guide a candidate choice, but runtime loading stays
    unverified.
    """
    loaded = (
        {item.casefold() for item in loaded_agent_types}
        if loaded_agent_types is not None else None
    )
    normalized = _norm(requested)
    native_roles = {
        "explorer": "Explorer",
        "worker": "Executor",
        "default": "GeneralWorker",
    }
    native_fallbacks = {
        "Explorer": "explorer",
        "Executor": "worker",
        "Reviewer": "default",
        "ExpertAdvisor": "default",
        "Researcher": "explorer",
        "GeneralWorker": "default",
        "Designer": "default",
    }
    specialist_candidates = {
        "Reviewer": ("code-reviewer",),
        "ExpertAdvisor": ("architect", "planner", "code-architect"),
        "Researcher": ("docs-lookup",),
    }

    def session_status(agent_type: str, *, generated: bool = True) -> str:
        if loaded is None:
            return "unverified"
        if not generated:
            return "agent_not_generated"
        return "loaded" if agent_type.casefold() in loaded else "not_loaded"

    def native_resolution(role: str, native_type: str, fallback: bool) -> dict[str, Any]:
        team_type = "team-" + role.lower()
        return {
            "role": role,
            "fallback": fallback,
            "host_role": requested if requested.casefold() == native_type else None,
            "agent_type": native_type,
            "native_agent_type": native_type,
            "team_agent_type": team_type,
            "requires_explicit_model": True,
            "requires_sync": False,
            "requires_reload": False,
            "current_session_status": session_status(native_type),
            "host_override": {},
        }

    def team_resolution(role: str, *, fallback: bool = False) -> dict[str, Any]:
        agent_type = "team-" + role.lower()
        configured = (codex_home / "agents" / f"{agent_type}.toml").is_file()
        status = session_status(agent_type, generated=configured)
        return {
            "role": role,
            "fallback": fallback,
            "host_role": None,
            "agent_type": agent_type,
            "native_agent_type": None,
            "team_agent_type": agent_type,
            "requires_explicit_model": False,
            "requires_sync": not configured,
            "requires_reload": configured and (loaded is None or status != "loaded"),
            "current_session_status": status,
            "host_override": {},
        }

    def host_agent_resolution(match: Mapping[str, Any]) -> dict[str, Any]:
        agent_type = str(match["host_role"])
        status = session_status(agent_type)
        overrides = match.get("explicit_override", {})
        return {
            "role": str(match["mapped_role"]),
            "fallback": match["coverage"] == "fallback",
            "host_role": agent_type,
            "agent_type": agent_type,
            "native_agent_type": None,
            "team_agent_type": None,
            "requires_explicit_model": not all(
                overrides.get(key) for key in ("model", "model_reasoning_effort")
            ),
            "requires_sync": False,
            "requires_reload": loaded is not None and status != "loaded",
            "current_session_status": status,
            "host_override": overrides,
        }

    # Explicit native type requests always stay native, even when the related
    # team-* alias is present in the caller's loaded-type list.
    if requested in native_roles:
        native_type = requested
        return native_resolution(native_roles[native_type], native_type, False)

    # Keep explicitly requested team aliases working for existing callers.
    for role in ROLE_NAMES:
        if normalized == _norm("team-" + role.lower()):
            return team_resolution(role)

    host_agents = scan_host_agents(codex_home)
    role = next((item for item in ROLE_NAMES if normalized == _norm(item)), None)
    if role is not None:
        # A profile role is a logical function request. Resolve it through its
        # preferred specialist/native route before looking for other agents
        # whose names merely happen to equal the function label.
        for candidate in specialist_candidates.get(role, ()):
            if loaded is not None and candidate.casefold() not in loaded:
                continue
            candidate_match = next(
                (
                    item for item in host_agents
                    if str(item["host_role"]).casefold() == candidate.casefold()
                    and "error" not in item
                ),
                None,
            )
            if candidate_match is not None:
                return host_agent_resolution({**candidate_match, "mapped_role": role})
            if loaded is not None:
                # Current tool metadata proves the type is available even if
                # its configuration came from somewhere other than this folder.
                return host_agent_resolution({
                    "host_role": candidate,
                    "mapped_role": role,
                    "coverage": "mapped",
                })
        return native_resolution(role, native_fallbacks[role], False)

    matches = [
        item for item in host_agents
        if _norm(str(item["host_role"])) == normalized
    ]
    if matches:
        # An explicit specialist name (for example python-reviewer) remains
        # explicit, even when its inferred profile family is Reviewer.
        return host_agent_resolution(matches[0])

    if loaded is not None:
        loaded_match = next((item for item in loaded if _norm(item) == normalized), None)
        if loaded_match is not None:
            mapped = infer_host_role(loaded_match, loaded_match)
            return host_agent_resolution({
                "host_role": loaded_match,
                "mapped_role": mapped or "GeneralWorker",
                "coverage": "mapped" if mapped else "fallback",
            })

    filename_matches = [
        item for item in host_agents
        if _norm(Path(str(item["file"])).stem) == normalized
    ]
    if filename_matches:
        return host_agent_resolution(filename_matches[0])

    return native_resolution("GeneralWorker", "default", True)


def cmd_resolve(
    role_arg: str,
    state_dir: Path,
    codex_home: Path,
    loaded_agent_types: set[str] | None = None,
) -> int:
    profile, revision = _load_profile_document(
        state_dir, codex_home, require_catalog=True
    )
    resolved = resolve_role(role_arg, codex_home, loaded_agent_types)
    role_name = resolved["role"]
    choice = active_choice(profile["roles"][role_name])
    current_session_status = resolved["current_session_status"]
    prompt_preferences = effective_fallback_prompt_settings(profile)
    warnings = []
    if resolved["host_override"]:
        warnings.append("host_agent_has_explicit_model_or_effort_override")
    if resolved["native_agent_type"]:
        warnings.append("native_builtin_ignores_same_named_toml_without_explicit_model")
    elif resolved["requires_explicit_model"]:
        warnings.append("host_agent_model_defaults_unverified_use_explicit_parameters")
    from model_context import report as context_report
    emit({
        "status": "ok",
        "requested_role": role_arg,
        "role": role_name,
        "host_role": resolved["host_role"],
        "fallback": resolved["fallback"],
        "model": choice["model"],
        "effort": choice["effort"],
        "agent_type": resolved["agent_type"],
        "native_agent_type": resolved["native_agent_type"],
        "team_agent_type": resolved["team_agent_type"],
        "requires_explicit_model": resolved["requires_explicit_model"],
        "fork_turns": "none",
        "context": context_report(state_dir, codex_home, choice["model"]),
        "requires_sync": resolved["requires_sync"],
        "requires_reload": resolved["requires_reload"],
        "current_session_status": current_session_status,
        "loaded_agent_types": sorted(loaded_agent_types) if loaded_agent_types is not None else None,
        "loaded_agent_types_source": (
            "caller_supplied_current_session" if loaded_agent_types is not None else None
        ),
        "profile_revision": revision,
        "skip_fallback_prompts": prompt_preferences["roles"][role_name]["effective"],
        "skip_fallback_prompts_override": prompt_preferences["roles"][role_name]["override"],
        "global_skip_fallback_prompts": prompt_preferences["global"],
        "host_override": resolved["host_override"],
        "warnings": warnings,
        "coverage": role_coverage(codex_home),
    })
    return 0


def _check_profile_agent(
    role_name: str,
    role: Mapping[str, Any],
    path: Path,
    runtime_loaded: bool | None = None,
    runtime_agent_type: str | None = None,
) -> dict[str, Any]:
    choice = active_choice(role)
    expected = tomllib.loads(
        agent_toml(role_name, choice["model"], choice["effort"]).decode("utf-8")
    )
    try:
        with path.open("rb") as handle:
            actual = tomllib.load(handle)
        matches = actual == expected
        error = None
    except FileNotFoundError:
        matches = False
        error = "missing"
    except (OSError, tomllib.TOMLDecodeError) as exc:
        matches = False
        error = str(exc)
    return {
        "role": role_name,
        "file": str(path),
        "status": "configured" if matches else "mismatch",
        "matches_profile": matches,
        "error": error,
        "runtime_loaded": runtime_loaded,
        "runtime_agent_type": runtime_agent_type,
    }


def cmd_verify(
    state_dir: Path,
    codex_home: Path,
    loaded_agent_types: set[str] | None = None,
) -> int:
    # The file check is authoritative for generated TOML. A supplied current
    # tool list verifies loaded names, but cannot prove that an agent executed.
    profile, revision = _load_profile_document(state_dir, codex_home)
    _, paths = _agent_paths(codex_home)
    checks = []
    for role in ROLE_NAMES:
        resolved = resolve_role(role, codex_home, loaded_agent_types)
        runtime_agent_type = resolved["agent_type"]
        runtime_loaded = (
            runtime_agent_type.casefold() in loaded_agent_types
            if loaded_agent_types is not None else None
        )
        checks.append(_check_profile_agent(
            role,
            profile["roles"][role],
            paths[role],
            runtime_loaded,
            runtime_agent_type,
        ))
    configured = all(item["matches_profile"] for item in checks)
    loaded_all = all(item["runtime_loaded"] for item in checks) if loaded_agent_types is not None else None
    runtime_status = (
        "runtime_unverified" if loaded_agent_types is None
        else "runtime_loaded_roles_verified" if loaded_all
        else "runtime_loaded_roles_incomplete"
    )
    emit({
        "status": "configured" if configured else "mismatch",
        "runtime_status": runtime_status,
        "profile_revision": revision,
        "loaded_agent_types": sorted(loaded_agent_types) if loaded_agent_types is not None else None,
        "loaded_agent_types_source": (
            "caller_supplied_current_session" if loaded_agent_types is not None else None
        ),
        "checks": checks,
        "coverage": role_coverage(codex_home),
    })
    return 0 if configured and (loaded_all is not False) else 1


def cmd_preference(
    role_arg: str, skip_fallback_prompts: bool, state_dir: Path
) -> int:
    profile_path = _profile_file(state_dir)
    raw_profile = _read_json(profile_path, "profile")
    normalize_profile(raw_profile, None)
    updated = json.loads(json.dumps(raw_profile))
    if role_arg.casefold() == "all":
        updated["skip_fallback_prompts"] = skip_fallback_prompts
        target_role = "all"
    else:
        target_role = next(
            (role for role in ROLE_NAMES if role.casefold() == role_arg.casefold()),
            None,
        )
        if target_role is None:
            raise AdapterError(
                "role must be one of " + ", ".join(ROLE_NAMES) + " or all"
            )
        updated["roles"][target_role]["skip_fallback_prompts"] = skip_fallback_prompts
    normalize_profile(updated, None)
    backups = commit_files(
        {profile_path: serialize_profile(updated)}, state_dir
    )
    after = normalize_profile(updated, None)
    emit({
        "status": "ok",
        "updated_role": target_role,
        "skip_fallback_prompts": skip_fallback_prompts,
        "effective_skip_fallback_prompts": (
            after["skip_fallback_prompts"]
            if target_role == "all"
            else effective_fallback_prompt_settings(after)["roles"][target_role]["effective"]
        ),
        "profile_revision": profile_revision(updated),
        "backup_files": backups,
    })
    return 0


def _load_events(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "events": {}}
    payload = _read_json(path, "failover event store")
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or not isinstance(payload.get("events"), dict)
    ):
        raise AdapterError(f"invalid failover event store: {path}")
    return payload


def cmd_failover(
    role_arg: str, reason_arg: str, event_id: str, state_dir: Path, codex_home: Path
) -> int:
    if not event_id or len(event_id) > 256:
        raise AdapterError("event-id must be non-empty and at most 256 characters")
    reason = reason_arg.strip().casefold()
    event_path = state_dir / "failover_events.json"
    events = _load_events(event_path)
    event_table = events["events"]
    request = {"role": role_arg, "reason": reason}
    if event_id in event_table:
        existing = event_table[event_id]
        if existing.get("request") != request:
            raise AdapterError("event-id was already used with a different role or reason")
        emit({**existing["result"], "idempotent_replay": True})
        return 0

    profile = _load_current_profile(state_dir, codex_home)
    resolved = resolve_role(role_arg, codex_home)
    role_name = resolved["role"]
    used_fallback = resolved["fallback"]
    host_role = resolved["host_role"]
    role = profile["roles"][role_name]
    active = active_choice(role)
    if reason in GLOBAL_QUOTA_REASONS:
        result = {
            "status": "blocked", "code": "workspace_quota_blocked",
            "role": role_name, "fallback": used_fallback, "host_role": host_role,
            "active_model": active["model"], "reason": reason,
        }
        new_profile = profile
    elif reason not in SWITCHABLE_REASONS:
        result = {
            "status": "blocked", "code": "reason_not_switchable",
            "role": role_name, "fallback": used_fallback, "host_role": host_role,
            "active_model": active["model"], "reason": reason,
        }
        new_profile = profile
    elif role["active_index"] >= len(role["fallbacks"]):
        result = {
            "status": "blocked", "code": "no_fallback_remaining",
            "role": role_name, "fallback": used_fallback, "host_role": host_role,
            "active_model": active["model"], "reason": reason,
        }
        new_profile = profile
    elif CATALOG_ERROR:
        result = {
            "status": "blocked", "code": "model_catalog_unavailable",
            "role": role_name, "fallback": used_fallback, "host_role": host_role,
            "active_model": active["model"], "reason": reason,
        }
        new_profile = profile
    else:
        catalog = load_catalog(codex_home)
        new_profile = json.loads(json.dumps(profile))
        next_role = new_profile["roles"][role_name]
        previous = active_choice(next_role)
        next_role["active_index"] += 1
        choice = active_choice(next_role)
        normalize_profile(new_profile, catalog)
        result = {
            "status": "failed_over", "role": role_name,
            "fallback": used_fallback, "host_role": host_role,
            "previous_model": previous["model"], "active_model": choice["model"],
            "active_effort": choice["effort"], "active_index": next_role["active_index"],
            "reason": reason,
        }

    event_table[event_id] = {"request": request, "result": result}
    outputs: dict[Path, bytes] = {
        event_path: (json.dumps(events, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    }
    if result["status"] == "failed_over":
        _, paths = _agent_paths(codex_home)
        outputs[_profile_file(state_dir)] = serialize_profile(new_profile)
        outputs[paths[role_name]] = agent_toml(
            role_name, result["active_model"], result["active_effort"]
        )
        outputs.update(_automatic_binding_outputs(state_dir, codex_home, new_profile))
    backups = commit_files(outputs, state_dir)
    emit({**result, "event_id": event_id, "backup_files": backups})
    return 0


def cmd_catalog_ack(
    fingerprint: str, decision: str, roles_arg: str, state_dir: Path
) -> int:
    if not fingerprint:
        raise AdapterError("fingerprint must be non-empty")
    path = _catalog_state_path(state_dir)
    state = _read_catalog_state(path)
    if state is None:
        raise AdapterError("catalog baseline has not been established")
    if fingerprint in state["acknowledged"]:
        existing = state["acknowledged"][fingerprint]
        emit({
            "status": "already_acknowledged",
            "fingerprint": fingerprint,
            "decision": existing.get("decision"),
            "selected_roles": existing.get("selected_roles", []),
        })
        return 0
    if not any(item.get("fingerprint") == fingerprint for item in state["pending"]):
        raise AdapterError("fingerprint is not pending")
    if decision == "selected":
        selected = [value.strip() for value in roles_arg.split(",") if value.strip()]
        unknown = [value for value in selected if value not in ROLE_NAMES]
        if unknown:
            raise AdapterError("unknown selected role(s): " + ", ".join(unknown))
        if not selected:
            raise AdapterError("decision=selected requires at least one --roles value")
        if len(set(selected)) != len(selected):
            raise AdapterError("--roles contains duplicates")
    elif decision == "defer":
        if roles_arg.strip():
            raise AdapterError("decision=defer does not accept --roles")
        selected = []
    else:
        raise AdapterError("decision must be selected or defer")

    state["acknowledged"][fingerprint] = {
        "decision": decision,
        "selected_roles": selected,
        "acknowledged_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    state["pending"] = [
        item for item in state["pending"] if item.get("fingerprint") != fingerprint
    ]
    commit_files({
        path: (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    }, state_dir)
    global CATALOG_NOTICE
    CATALOG_NOTICE = _notice_from_state(state, state_dir)
    emit({
        "status": "acknowledged",
        "fingerprint": fingerprint,
        "decision": decision,
        "selected_roles": selected,
        "profile_updated": False,
    })
    return 0


def cmd_log(
    role: str, model: str, turns: int, input_tokens: int | None,
    output_tokens: int | None, requests: int | None, duration_ms: int | None,
    files: str, summary: str, state_dir: Path,
) -> int:
    counts = {
        "turns": turns, "input_tokens": input_tokens, "output_tokens": output_tokens,
        "requests": requests, "duration_ms": duration_ms,
    }
    for field, value in counts.items():
        if value is not None and value < 0:
            raise AdapterError(f"{field} must be zero or greater")
    if not role.strip() or not model.strip():
        raise AdapterError("role and model must be non-empty")
    entry = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "role": role,
        "model": model,
        "observed": counts,
        "files": [value.strip() for value in files.split(",") if value.strip()],
        "summary": summary,
    }
    path = state_dir / "ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise AdapterError(f"cannot append telemetry log at {path}: {exc}") from exc
    emit({"status": "logged", "entry": entry})
    return 0


def _load_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    records.append(value)
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError(f"cannot read telemetry log at {path}: {exc}") from exc
    return records


def cmd_dashboard(state_dir: Path, codex_home: Path) -> int:
    profile_path = _profile_file(state_dir)
    profile = normalize_profile(
        _read_json(profile_path, "profile"), None
    ) if profile_path.exists() else {"schema_version": PROFILE_SCHEMA_VERSION, "roles": {}}
    records = _load_ledger(state_dir / "ledger.jsonl")
    cards = []
    for role_name, role in profile["roles"].items():
        choice = active_choice(role)
        remaining = role["fallbacks"][role["active_index"]:]
        fallback_text = ", ".join(
            html.escape(item["model"] + " (" + item["effort"] + ")") for item in remaining
        ) or "无"
        cards.append(
            "<article><h3>" + html.escape(role_name) + "</h3>"
            "<p>配置模型：<code>" + html.escape(choice["model"]) + "</code></p>"
            "<p>推理强度：" + html.escape(choice["effort"]) + "</p>"
            "<p>剩余备选：" + fallback_text + "</p>"
            "<p class='muted'>运行时加载：未验证</p></article>"
        )
    rows = []
    for item in reversed(records[-50:]):
        observed = item.get("observed", {})
        rows.append(
            "<tr><td>" + html.escape(str(item.get("timestamp", ""))[:19]) + "</td>"
            "<td>" + html.escape(str(item.get("role", ""))) + "</td>"
            "<td><code>" + html.escape(str(item.get("model", ""))) + "</code></td>"
            "<td>" + html.escape(str(observed.get("turns", ""))) + "</td>"
            "<td>" + html.escape(str(observed.get("input_tokens", ""))) + "</td>"
            "<td>" + html.escape(str(observed.get("output_tokens", ""))) + "</td>"
            "<td>" + html.escape(str(item.get("summary", ""))) + "</td></tr>"
        )
    coverage = role_coverage(codex_home)
    document = (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<title>Team Agent</title><style>"
        "body{font-family:system-ui,sans-serif;background:#111827;color:#f3f4f6;margin:24px}"
        ".cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}"
        "article,table{background:#1f2937;border:1px solid #374151;border-radius:8px;padding:14px}"
        ".muted{color:#fbbf24}code{color:#7dd3fc}table{width:100%;border-collapse:collapse}"
        "td,th{padding:8px;border-bottom:1px solid #374151;text-align:left}"
        "</style></head><body><h1>Team Agent 配置状态</h1>"
        "<p>运行时加载状态：<strong>未验证</strong>。主机角色覆盖："
        + str(coverage["mapped_host_roles"]) + "/" + str(coverage["total_host_roles"])
        + " 已识别，" + str(coverage["fallback_host_roles"]) + " 个使用 GeneralWorker fallback。</p>"
        "<section class='cards'>" + "".join(cards) + "</section>"
        "<h2>真实观测记录（最近 50 条）</h2><table><thead><tr>"
        "<th>时间 UTC</th><th>角色</th><th>模型</th><th>轮次</th>"
        "<th>输入 tokens</th><th>输出 tokens</th><th>摘要</th></tr></thead><tbody>"
        + ("".join(rows) or "<tr><td colspan='7'>暂无记录</td></tr>")
        + "</tbody></table></body></html>"
    )
    dashboard_path = state_dir / "dashboard.html"
    try:
        _atomic_write(dashboard_path, document.encode("utf-8"))
    except OSError as exc:
        raise AdapterError(f"cannot write dashboard at {dashboard_path}: {exc}") from exc
    emit({
        "status": "ok",
        "dashboard": str(dashboard_path),
        "runtime_status": "runtime_unverified",
        "coverage": {
            "total_host_roles": coverage["total_host_roles"],
            "mapped_host_roles": coverage["mapped_host_roles"],
            "fallback_host_roles": coverage["fallback_host_roles"],
        },
    })
    return 0


def _parse_loaded_agent_types(value: str | None) -> set[str] | None:
    if value is None:
        return None
    return {item.strip().casefold() for item in value.split(",") if item.strip()}


def build_parser() -> argparse.ArgumentParser:
    default_codex_home = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    parser = JsonArgumentParser(description="Compile and inspect a Team Agent role profile")
    parser.add_argument("--state-dir", default=str(Path.home() / ".team-agent"))
    parser.add_argument("--codex-home", default=default_codex_home)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def command_parser(name: str) -> argparse.ArgumentParser:
        child = subparsers.add_parser(name)
        child.add_argument("--state-dir", default=argparse.SUPPRESS)
        child.add_argument("--codex-home", default=argparse.SUPPRESS)
        return child

    for name in ("models", "onboarding"):
        command_parser(name).add_argument("--dispatch-models", help="Comma-separated model enum exposed by the current dispatch tool")
    sync = command_parser("sync")
    sync.add_argument("--profile", required=True)
    resolve = command_parser("resolve")
    resolve.add_argument("--role", required=True)
    resolve.add_argument("--loaded-agent-types")
    failover = command_parser("failover")
    failover.add_argument("--role", required=True)
    failover.add_argument("--reason", required=True)
    failover.add_argument("--event-id", required=True)
    acknowledge = command_parser("catalog-ack")
    acknowledge.add_argument("--fingerprint", required=True)
    acknowledge.add_argument("--decision", required=True, choices=["selected", "defer"])
    acknowledge.add_argument("--roles", default="")
    preference = command_parser("preference")
    preference.add_argument("--role", required=True)
    preference.add_argument(
        "--skip-fallback-prompts", required=True, choices=["true", "false"]
    )
    verify = command_parser("verify")
    verify.add_argument("--loaded-agent-types")
    context = command_parser("context")
    context.add_argument("--model")
    context.add_argument("--thread-id")
    context_set = command_parser("context-set")
    context_set.add_argument("--model", required=True)
    context_set.add_argument("--window", type=int, required=True)
    context_set.add_argument("--scope", choices=["subagent", "host-model"], default="subagent")
    context_reset = command_parser("context-reset")
    context_reset.add_argument("--model", required=True)
    log = command_parser("log")
    log.add_argument("--role", required=True)
    log.add_argument("--model", required=True)
    log.add_argument("--turns", type=int, required=True)
    log.add_argument("--input-tokens", type=int)
    log.add_argument("--output-tokens", type=int)
    log.add_argument("--requests", type=int)
    log.add_argument("--duration-ms", type=int)
    log.add_argument("--files", default="")
    log.add_argument("--summary", default="")
    command_parser("dashboard")
    return parser


def _run_command(args: argparse.Namespace, state_dir: Path, codex_home: Path) -> int:
    global CATALOG_NOTICE
    try:
        from model_context import refresh as refresh_context
        if args.command not in {"context-set", "context-reset", "preference"}:
            refresh_context(state_dir, codex_home)
        CATALOG_NOTICE = (
            None if args.command == "preference"
            else check_catalog(state_dir, codex_home)
        )
        if args.command in {"models", "onboarding"}:
            dispatch_models = (
                {item.strip() for item in args.dispatch_models.split(",") if item.strip()}
                if args.dispatch_models is not None else None
            )
            return cmd_models(codex_home, state_dir, dispatch_models)
        if args.command in {"context-set", "context-reset"}:
            if args.command == "context-set" and args.scope != "host-model":
                emit({"status": "unsupported", "scope": "subagent",
                      "model": args.model, "requested_context_window": args.window,
                      "reason": "current_codex_dispatch_has_no_subagent_window_parameter",
                      "host_settings_changed": False})
                return 1
            from model_context import configure
            emit(configure(state_dir, codex_home, args.model,
                           args.window if args.command == "context-set" else None))
            return 0
        if args.command == "context":
            from model_context import report, observe
            result = report(state_dir, codex_home, args.model)
            if args.thread_id:
                result["observation"] = observe(codex_home, args.thread_id, args.model)
            emit(result)
            return 0 if not args.thread_id or result["observation"]["status"] == "observed" else 1
        if args.command == "sync":
            return cmd_sync(Path(args.profile).expanduser(), state_dir, codex_home)
        if args.command == "resolve":
            return cmd_resolve(
                args.role, state_dir, codex_home,
                _parse_loaded_agent_types(args.loaded_agent_types),
            )
        if args.command == "failover":
            return cmd_failover(args.role, args.reason, args.event_id, state_dir, codex_home)
        if args.command == "catalog-ack":
            return cmd_catalog_ack(args.fingerprint, args.decision, args.roles, state_dir)
        if args.command == "preference":
            return cmd_preference(
                args.role, args.skip_fallback_prompts == "true", state_dir
            )
        if args.command == "verify":
            return cmd_verify(
                state_dir, codex_home,
                _parse_loaded_agent_types(args.loaded_agent_types),
            )
        if args.command == "log":
            return cmd_log(
                args.role, args.model, args.turns, args.input_tokens, args.output_tokens,
                args.requests, args.duration_ms, args.files, args.summary, state_dir,
            )
        if args.command == "dashboard":
            return cmd_dashboard(state_dir, codex_home)
        raise AdapterError(f"unknown command: {args.command}")
    except AdapterError as exc:
        emit({"status": "error", "error": str(exc)})
        return 1
    except ValueError as exc:
        emit({"status": "error", "error": str(exc)})
        return 1
    except Exception as exc:
        emit({
            "status": "error",
            "error": f"unexpected {type(exc).__name__}: {exc}",
        })
        return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    state_dir = Path(args.state_dir).expanduser()
    codex_home = Path(args.codex_home).expanduser()
    try:
        with state_lock(state_dir):
            return _run_command(args, state_dir, codex_home)
    except TimeoutError as exc:
        emit({"status": "error", "error": str(exc)})
        return 1
    except Exception as exc:
        emit({
            "status": "error",
            "error": f"unexpected {type(exc).__name__}: {exc}",
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


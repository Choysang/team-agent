"""Discover provider metadata without treating it as callable Codex capability."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


def discover(codex_home: Path, ccswitch_home: Path | None = None) -> dict[str, Any]:
    codex_home = Path(codex_home).expanduser().resolve()
    ccswitch_home = Path(ccswitch_home) if ccswitch_home else codex_home.parent / ".cc-switch"
    entries: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    seen_sources = 0

    def add(model: Any, provider: str, host: str, levels: Any, source: Path) -> None:
        if not isinstance(model, str) or not model.strip() or model == "custom":
            return
        efforts = set()
        if isinstance(levels, list):
            for level in levels:
                value = level.get("effort") if isinstance(level, dict) else level
                if isinstance(value, str) and value:
                    efforts.add(value)
        identity = json.dumps([host, provider, model], ensure_ascii=False)
        key = hashlib.sha256(identity.encode()).hexdigest()
        existing = entries.get(key)
        if existing:
            efforts.update(existing["efforts"])
        entries[key] = {
            "id": key, "model": model, "provider": provider, "host": host,
            "efforts": sorted(efforts),
            "capability_status": "declared_metadata_unverified" if efforts else "unknown",
            "dispatch_status": "discovered_not_verified_callable",
            "billing_group": "unknown", "source": str(source),
        }

    catalog_file = codex_home / "cc-switch-model-catalog.json"
    if catalog_file.is_file():
        seen_sources += 1
        try:
            data = json.loads(catalog_file.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or not isinstance(data.get("models"), list):
                raise ValueError("invalid catalog schema")
            for entry in data["models"]:
                if isinstance(entry, dict):
                    add(entry.get("slug") or entry.get("model"),
                        str(entry.get("provider") or "unspecified"),
                        str(entry.get("host") or "codex"),
                        entry.get("supported_reasoning_levels"), catalog_file)
        except (OSError, ValueError, TypeError) as error:
            errors.append({"source": str(catalog_file), "error_type": type(error).__name__})

    database = (ccswitch_home / "cc-switch.db").resolve()
    if database.is_file():
        seen_sources += 1
        try:
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
                connection.row_factory = sqlite3.Row
                # Existing credential fields are never returned, logged, or sent to a service.
                rows = connection.execute("SELECT id, app_type, meta, settings_config FROM providers").fetchall()
            for row in rows:
                provider, host = str(row["id"]), str(row["app_type"])
                for field in ("meta", "settings_config"):
                    try:
                        data = json.loads(row[field] or "{}")
                        if not isinstance(data, dict):
                            raise ValueError("invalid provider schema")
                        if field == "meta":
                            routes = data.get("claudeDesktopModelRoutes", {})
                            if isinstance(routes, dict):
                                for route in routes.values():
                                    if isinstance(route, dict):
                                        add(route.get("model"), provider, host,
                                            route.get("supported_reasoning_levels"), database)
                        catalog = data.get("modelCatalog", {})
                        models = catalog.get("models", []) if isinstance(catalog, dict) else []
                        for item in models if isinstance(models, list) else []:
                            if isinstance(item, dict):
                                add(item.get("model") or item.get("slug"), provider, host,
                                    item.get("reasoningLevels", item.get("supported_reasoning_levels")), database)
                        # Parse explicit Codex TOML only; do not regex arbitrary serialized secrets.
                        if host == "codex" and isinstance(data.get("config"), str):
                            import tomllib
                            config = tomllib.loads(data["config"])
                            add(config.get("model"), provider, host, None, database)
                    except (ValueError, TypeError) as error:
                        errors.append({"source": str(database), "provider": provider,
                                       "field": field, "error_type": type(error).__name__})
        except (OSError, sqlite3.Error) as error:
            errors.append({"source": str(database), "error_type": type(error).__name__})

    status = "partial" if errors and entries else "unavailable" if errors else "ok" if seen_sources else "absent"
    return {"status": status, "models": sorted(entries.values(), key=lambda item: item["id"]),
            "errors": errors, "scope": "discovery_only_parent_provider_inherited"}

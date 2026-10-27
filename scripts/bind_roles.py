"""Bind existing Codex specialist instructions to configured team models."""
from __future__ import annotations

import argparse
import json
import os
import tomllib
from pathlib import Path

START = "# team-agent model binding:start\n"
END = "# team-agent model binding:end\n"
NATIVE = {"default": "GeneralWorker", "worker": "Executor", "explorer": "Explorer"}


def plan_bindings(codex_home: Path, profile: dict, host_roles: list[dict]) -> dict[Path, bytes]:
    bindings = {item["host_role"]: (Path(item["file"]), item["mapped_role"]) for item in host_roles}
    for name, role in NATIVE.items():
        path = bindings[name][0] if name in bindings else codex_home / "agents" / (name + ".toml")
        bindings[name] = (path, role)
    outputs = {}
    for name, (path, role) in bindings.items():
        if path.exists():
            text = path.read_text(encoding="utf-8-sig")
        else:
            text = (f'name = {json.dumps(name)}\ndescription = "Configured {role} subagent"\n'
                    f'developer_instructions = "Perform the assigned {role} task within its stated scope."\n')
        if text.startswith(START):
            position = text.find(END)
            if position < 0:
                raise ValueError(f"incomplete managed model binding: {path}")
            text = text[position + len(END):]
        data = tomllib.loads(text)
        if "model" in data or "model_reasoning_effort" in data:
            raise ValueError(f"existing explicit model settings need reconciliation: {path}")
        config = profile["roles"][role]
        choice = [config, *config["fallbacks"]][config["active_index"]]
        updated = (START + f'model = {json.dumps(choice["model"])}\n'
                   f'model_reasoning_effort = {json.dumps(choice["effort"])}\n' + END + text)
        tomllib.loads(updated)
        outputs[path] = updated.encode("utf8")
    return outputs


def main() -> int:
    from team_adapter import commit_files, scan_host_agents
    from state_lock import state_lock
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex"))
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".team-agent")
    args = parser.parse_args()
    try:
        with state_lock(args.state_dir):
            profile = json.loads((args.state_dir / "profile.json").read_text(encoding="utf8"))
            outputs = plan_bindings(args.codex_home, profile, scan_host_agents(args.codex_home))
            marker = args.state_dir / "host-bindings.json"
            outputs[marker] = (json.dumps({"enabled": True, "files": [str(path) for path in outputs]}, indent=2) + "\n").encode()
            backups = commit_files(outputs, args.state_dir)
        print(json.dumps({"status": "bound", "bound_roles": len(outputs) - 1, "backup_files": backups}))
        return 0
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps({"status": "error", "error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Freshness of the per-host adapter cards, and stamping one after a self-check.

Each card in references/adapters/ carries one metadata line:
  <!-- adapter: host=<id>; verified=<YYYY-MM-DD>; host_version=<text|unknown>; evidence=<tested|local|docs> -->

  status [--host ID] [--host-version V] [--max-age-days 90]
      one host -> fresh | stale (with reasons) | missing; no host -> all cards
  stamp --host ID --verified YYYY-MM-DD --evidence tested|local|docs [--host-version V]
      rewrites only that card's metadata line (atomic); the card must exist

Python 3.10+ standard library. Output is one JSON object.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ADAPTERS = Path(__file__).resolve().parents[1] / "references" / "adapters"
LINE = re.compile(r"^<!-- adapter: (?P<body>[^>]*) -->\s*$", re.M)
EVIDENCE = ("tested", "local", "docs")
NOT_CARDS = {"generic", "provider-discovery"}


def parse(path: Path) -> dict | None:
    match = LINE.search(path.read_text(encoding="utf-8"))
    if not match:
        return None
    fields = dict(part.strip().split("=", 1) for part in match.group("body").split(";") if "=" in part)
    return {key.strip(): value.strip() for key, value in fields.items()}


def cards(root: Path) -> dict[str, dict]:
    result = {}
    for path in sorted(root.glob("*.md")):
        if path.stem in NOT_CARDS:
            continue
        meta = parse(path) or {}
        result[meta.get("host", path.stem)] = {"file": str(path), **meta, "has_metadata": bool(meta)}
    return result


def judge(card: dict | None, host_version: str | None, max_age: int, today: dt.date) -> dict:
    if card is None:
        return {"status": "missing", "next": "create the card from references/self-adapt.md"}
    reasons = []
    if not card["has_metadata"]:
        reasons.append("no_metadata_line")
    else:
        try:
            age = (today - dt.date.fromisoformat(card.get("verified", ""))).days
            if age > max_age:
                reasons.append(f"verified_{age}_days_ago")
        except ValueError:
            reasons.append("verified_date_invalid")
        known = card.get("host_version", "unknown")
        if host_version and known not in ("unknown", host_version):
            reasons.append(f"host_version_changed:{known}->{host_version}")
    status = "stale" if reasons else "fresh"
    return {"status": status, "reasons": reasons,
            "next": "run the self-check in references/self-adapt.md" if reasons else "use the card as is"}


def stamp(path: Path, verified: str, evidence: str, host_version: str | None, host: str) -> dict:
    dt.date.fromisoformat(verified)
    text = path.read_text(encoding="utf-8")
    old = parse(path) or {}
    version = host_version or old.get("host_version", "unknown")
    line = f"<!-- adapter: host={host}; verified={verified}; host_version={version}; evidence={evidence} -->"
    if LINE.search(text):
        text = LINE.sub(lambda _: line, text, count=1)
    else:
        head, _, rest = text.partition("\n")
        text = f"{head}\n{line}\n{rest}"
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".stamp-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(tmp, path)
    return {"status": "stamped", "file": str(path), "previous": old or None,
            "current": {"host": host, "verified": verified, "host_version": version, "evidence": evidence}}


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < (3, 10):
        print(json.dumps({"status": "error", "error": "Python 3.10+ required"}))
        return 1
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--adapters", type=Path, default=ADAPTERS)
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    status.add_argument("--host")
    status.add_argument("--host-version")
    status.add_argument("--max-age-days", type=int, default=90)
    status.add_argument("--today", help=argparse.SUPPRESS)
    mark = sub.add_parser("stamp")
    mark.add_argument("--host", required=True)
    mark.add_argument("--verified", required=True)
    mark.add_argument("--evidence", required=True, choices=EVIDENCE)
    mark.add_argument("--host-version")
    args = parser.parse_args(argv)
    try:
        all_cards = cards(args.adapters)
        if args.command == "status":
            today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
            if args.host:
                out = {"host": args.host, **judge(all_cards.get(args.host), args.host_version,
                                                   args.max_age_days, today)}
                if args.host in all_cards:
                    out["card"] = all_cards[args.host]
            else:
                out = {"status": "ok", "cards": {h: {**c, **judge(c, None, args.max_age_days, today)}
                                                 for h, c in all_cards.items()}}
        else:
            card = all_cards.get(args.host)
            if card is None:
                raise ValueError(f"no card for host {args.host}; create it first")
            out = stamp(Path(card["file"]), args.verified, args.evidence, args.host_version, args.host)
    except (OSError, ValueError) as exc:
        out = {"status": "error", "error": str(exc)}
    line = json.dumps(out, ensure_ascii=False) + "\n"
    binary_stdout = getattr(sys.stdout, "buffer", None)
    if binary_stdout is not None:  # UTF-8 regardless of the console code page
        binary_stdout.write(line.encode("utf-8"))
        binary_stdout.flush()
    else:
        sys.stdout.write(line)
    return 1 if out.get("status") in ("error", "missing", "stale") else 0


if __name__ == "__main__":
    raise SystemExit(main())

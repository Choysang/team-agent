from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "adapter_status.py"


class AdapterStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "alpha.md").write_text(
            "# Alpha\n<!-- adapter: host=alpha; verified=2026-10-01; host_version=1.0; evidence=tested -->\nbody\n",
            encoding="utf-8")
        (self.root / "beta.md").write_text("# Beta\nno metadata\n", encoding="utf-8")
        (self.root / "generic.md").write_text("# Generic\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *args: str) -> tuple[int, dict]:
        done = subprocess.run([sys.executable, str(SCRIPT), "--adapters", str(self.root), *args],
                              capture_output=True, text=True, encoding="utf-8")
        return done.returncode, json.loads(done.stdout)

    def test_fresh_stale_missing(self) -> None:
        code, out = self.run_cli("status", "--host", "alpha", "--today", "2026-10-27")
        self.assertEqual((code, out["status"]), (0, "fresh"))
        code, out = self.run_cli("status", "--host", "alpha", "--today", "2027-03-01")
        self.assertEqual((code, out["status"]), (1, "stale"))
        self.assertTrue(out["reasons"][0].startswith("verified_"))
        _, out = self.run_cli("status", "--host", "alpha", "--host-version", "2.0", "--today", "2026-10-27")
        self.assertEqual(out["reasons"], ["host_version_changed:1.0->2.0"])
        _, out = self.run_cli("status", "--host", "alpha", "--host-version", "1.0", "--today", "2026-10-27")
        self.assertEqual(out["status"], "fresh")
        code, out = self.run_cli("status", "--host", "gamma")
        self.assertEqual((code, out["status"]), (1, "missing"))
        _, out = self.run_cli("status", "--host", "beta")
        self.assertEqual(out["reasons"], ["no_metadata_line"])

    def test_listing_skips_non_cards(self) -> None:
        _, out = self.run_cli("status", "--today", "2026-10-27")
        self.assertEqual(sorted(out["cards"]), ["alpha", "beta"])

    def test_stamp_updates_only_the_metadata_line(self) -> None:
        code, out = self.run_cli("stamp", "--host", "alpha", "--verified", "2026-11-02", "--evidence", "local")
        self.assertEqual(code, 0)
        self.assertEqual(out["current"]["host_version"], "1.0")  # kept when not given
        text = (self.root / "alpha.md").read_text(encoding="utf-8")
        self.assertEqual(text, "# Alpha\n<!-- adapter: host=alpha; verified=2026-11-02; host_version=1.0; "
                               "evidence=local -->\nbody\n")
        self.run_cli("stamp", "--host", "beta", "--verified", "2026-11-02", "--evidence", "docs")
        self.assertIn("host=beta", (self.root / "beta.md").read_text(encoding="utf-8").splitlines()[1])

    def test_stamp_rejects_bad_input(self) -> None:
        code, out = self.run_cli("stamp", "--host", "gamma", "--verified", "2026-11-02", "--evidence", "docs")
        self.assertEqual((code, out["status"]), (1, "error"))
        code, out = self.run_cli("stamp", "--host", "alpha", "--verified", "yesterday", "--evidence", "docs")
        self.assertEqual(out["status"], "error")
        self.assertIn("verified=2026-10-01", (self.root / "alpha.md").read_text(encoding="utf-8"))

    def test_real_cards_all_have_metadata(self) -> None:
        done = subprocess.run([sys.executable, str(SCRIPT), "status", "--max-age-days", "100000"],
                              capture_output=True, text=True, encoding="utf-8")
        cards = json.loads(done.stdout)["cards"]
        self.assertTrue(cards)
        self.assertEqual([h for h, c in cards.items() if not c["has_metadata"]], [])


if __name__ == "__main__":
    unittest.main()

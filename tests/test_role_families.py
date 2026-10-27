import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import team_adapter as adapter


class ReviewedRoleFamilyTests(unittest.TestCase):
    def test_type_design_is_code_review_not_visual_design(self):
        self.assertEqual(adapter.infer_host_role("type-design-analyzer", "type-design-analyzer"), "Reviewer")

    def test_known_specialties_use_their_actual_function(self):
        expected = {"e2e-runner": "Executor", "tdd-guide": "Executor",
                    "opensource-sanitizer": "Reviewer", "gan-evaluator": "Reviewer",
                    "doc-updater": "Executor", "docs-lookup": "Researcher",
                    "chief-of-staff": "GeneralWorker", "gan-planner": "ExpertAdvisor"}
        for name, profile in expected.items():
            with self.subTest(name=name):
                self.assertEqual(adapter.infer_host_role(name, name), profile)

    def test_unknown_name_remains_unclassified(self):
        self.assertIsNone(adapter.infer_host_role("new-domain-specialist", "new-domain-specialist"))

    def test_coverage_separates_reviewed_templates_from_builtins(self):
        mappings = adapter.reviewed_role_families()
        self.assertEqual(len(mappings), 47)
        self.assertEqual(len({entry["family"] for entry in mappings.values()}), 8)
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            agents = home / "agents"
            agents.mkdir()
            for name in (*mappings, "worker", "default", "explorer"):
                (agents / (name + ".toml")).write_text(
                    f'name="{name}"\ndescription="test"\ndeveloper_instructions="test"\n', encoding="utf8")
            coverage = adapter.role_coverage(home)
            self.assertEqual(coverage["total_host_roles"], 50)
            self.assertEqual(coverage["reviewed_specialist_roles"], 47)
            self.assertEqual(coverage["fallback_host_roles"], 0)
            self.assertEqual(sum(coverage["family_counts"].values()), 47)


if __name__ == "__main__":
    unittest.main()

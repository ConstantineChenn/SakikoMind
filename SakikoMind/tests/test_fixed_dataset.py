import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FixedDatasetTests(unittest.TestCase):
    def test_fixed_dataset_has_twenty_complete_unique_cases(self):
        dataset = json.loads(
            (PROJECT_ROOT / "data" / "eval" / "fixed_cases_20.json").read_text(encoding="utf-8")
        )
        policies = json.loads(
            (PROJECT_ROOT / "data" / "demo_docs" / "saas_policies.json").read_text(encoding="utf-8")
        )
        known_sources = {document["source_id"] for document in policies}
        cases = dataset["cases"]

        self.assertEqual(len(cases), 20)
        self.assertEqual(len({case["id"] for case in cases}), 20)

        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["message"].strip())
                self.assertTrue(case["expected_intents"])
                self.assertTrue(case["expected_agents"])
                self.assertIsInstance(case["should_escalate"], bool)
                self.assertTrue(case["expected_skills"])
                self.assertTrue(set(case.get("expected_sources", [])).issubset(known_sources))
                if case["should_escalate"]:
                    self.assertTrue(case.get("expected_reason"))
                    self.assertIn(case.get("expected_priority"), {"P0", "P1", "P2"})

import tempfile
import unittest
from pathlib import Path

from evaluation.evaluator import EndToEndEvaluator, EvalReport


class EvaluatorSnapshotTests(unittest.TestCase):
    def test_evaluator_saves_baseline_and_timestamped_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            baseline_path = Path(temp_dir) / "baseline.json"
            evaluator = EndToEndEvaluator(
                orchestrator=None,
                recognizer=None,
                api_key="test-key",
                baseline_path=str(baseline_path),
            )
            report = EvalReport(
                timestamp="2026-08-07T00:00:00",
                total=1,
                passed=1,
                pass_rate=1.0,
                avg_scores={"relevance": 1.0},
                regressions=[],
                recommendations=[],
                results=[],
            )

            evaluator._save_baseline(report)

            self.assertTrue(baseline_path.exists())
            self.assertIsNotNone(evaluator.last_snapshot_path)
            self.assertTrue((baseline_path.parent / "snapshots").exists())

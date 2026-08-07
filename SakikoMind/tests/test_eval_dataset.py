import unittest

from evaluation.evaluator import load_fixed_eval_cases


class FixedEvalDatasetTests(unittest.TestCase):
    def test_fixed_eval_dataset_covers_twenty_labeled_intents(self):
        intent_cases, dialog_cases = load_fixed_eval_cases()

        self.assertEqual(len(intent_cases), 20)
        self.assertGreaterEqual(len(dialog_cases), 5)
        self.assertEqual(
            {case.expected_intent for case in intent_cases},
            {
                "query", "complaint", "request", "greeting", "escalation",
                "technical", "billing", "account", "feedback", "other",
            },
        )
        self.assertIn(
            "请帮我取消本次订单。",
            [case.message for case in intent_cases],
        )

import unittest

from evaluation.evaluator import LLMJudge


class LLMJudgeParserTests(unittest.TestCase):
    def test_parse_scores_accepts_json_wrapped_by_extra_text(self):
        scores = LLMJudge._parse_scores(
            "评分结果：{\"relevance\": 0.9, \"accuracy\": 0.8, \"completeness\": 0.7, \"helpfulness\": 0.6}"
        )

        self.assertEqual(scores.relevance, 0.9)
        self.assertEqual(scores.helpfulness, 0.6)

    def test_parse_scores_rejects_empty_or_incomplete_output(self):
        with self.assertRaises(ValueError):
            LLMJudge._parse_scores("")
        with self.assertRaises(ValueError):
            LLMJudge._parse_scores("{\"relevance\": 0.9}")

import unittest

from core.intent_recognizer import IntentCategory, IntentRecognizer


class IntentRecognizerRulesTest(unittest.TestCase):
    def test_production_outage_uses_technical_intent(self):
        message = "生产系统全站不可用，而且疑似发生数据丢失。"

        result = IntentRecognizer._high_risk_intent(message)

        self.assertEqual(result, IntentCategory.TECHNICAL)

    def test_privacy_leak_uses_account_intent(self):
        message = "我怀疑个人信息发生数据泄露，请协助处理。"

        result = IntentRecognizer._high_risk_intent(message)

        self.assertEqual(result, IntentCategory.ACCOUNT)

    def test_regular_question_has_no_high_risk_override(self):
        result = IntentRecognizer._high_risk_intent("专业版多少钱？")

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()

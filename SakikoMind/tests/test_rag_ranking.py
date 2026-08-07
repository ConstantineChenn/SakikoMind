import unittest

from mcp.knowledge_base import KnowledgeBase
from mcp.tool_manager import MCPToolManager


class RagRankingTests(unittest.TestCase):
    def test_business_keywords_raise_relevant_policy_score(self):
        bonus = KnowledgeBase._keyword_bonus(
            "专业版升级后什么时候生效？",
            {"title": "月付与年付账单规则", "effective_date": "2026-08-01"},
            "套餐升级即时生效，系统按剩余周期折算差额。",
        )

        self.assertGreaterEqual(bonus, 0.2)

    def test_deterministic_score_prefers_higher_hybrid_score(self):
        items = [{"score": 0.4}, {"score": 0.9}, {"score": 0.6}]

        ranked = sorted(items, key=MCPToolManager._deterministic_score, reverse=True)

        self.assertEqual([item["score"] for item in ranked], [0.9, 0.6, 0.4])

    def test_source_hint_prioritizes_data_deletion_policy(self):
        bonus = KnowledgeBase._keyword_bonus(
            "我想删除账号和所有个人数据",
            {
                "source_id": "SaaS-DATA-002",
                "title": "数据保留与删除请求",
                "effective_date": "2026-08-01",
            },
            "个人数据删除请求必须完成身份核验并升级人工处理。",
        )

        self.assertGreaterEqual(bonus, 0.9)

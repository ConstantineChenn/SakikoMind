import asyncio
import unittest
from types import SimpleNamespace

from mcp.tool_manager import MCPToolManager


async def _slow_create(**kwargs):
    await asyncio.sleep(0.05)
    return SimpleNamespace(content=[])


class RagTimeoutTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager = MCPToolManager("test-key", model="test-model", llm_timeout_s=0.001)
        self.manager._client = SimpleNamespace(
            messages=SimpleNamespace(create=_slow_create),
        )

    async def test_query_rewrite_returns_original_query_after_timeout(self):
        result = await self.manager.rewrite_query("退款流程")

        self.assertEqual(result, ["退款流程"])

    async def test_rerank_uses_deterministic_order_after_timeout(self):
        items = [
            {"source_id": "low", "score": 0.2},
            {"source_id": "high", "score": 0.9},
        ]

        result = await self.manager._rerank("退款流程", items, top_k=1)

        self.assertEqual(result, [{"source_id": "high", "score": 0.9}])


if __name__ == "__main__":
    unittest.main()

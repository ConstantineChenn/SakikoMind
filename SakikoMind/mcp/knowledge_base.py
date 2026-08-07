"""
RAG 知识库 —— 基于 ChromaDB 的真实检索实现。

功能：
  1. 文档导入：将文本切片后存入 ChromaDB（自动生成 Embedding）
  2. 语义检索：根据 query 从知识库中检索最相关的文档片段
  3. 与 MCP 工具框架集成：作为 knowledge_search 工具的真实 handler

ChromaDB 在这里的角色：
  - memory/ 中用于存储对话记忆（情景记忆 + 用户画像）
  - 这里用于存储知识库文档（RAG 检索）
  两者是不同的 collection，互不干扰。
"""
import hashlib
import json
import logging
import pathlib
from typing import Any, Dict, List, Optional

import chromadb

logger = logging.getLogger(__name__)


class KnowledgeBase:
    """
    基于 ChromaDB 的 RAG 知识库。

    ChromaDB 内置了 Embedding 模型（all-MiniLM-L6-v2），
    调用 add() 时自动生成向量，query() 时自动做语义匹配。
    不需要额外调用 Anthropic Embeddings API。
    """

    COLLECTION_NAME = "knowledge_base"

    def __init__(
        self,
        chroma_host: str = "localhost",
        chroma_port: int = 8000,
        chroma_path: str = "./data/chroma",
    ):
        # 优先连接独立 ChromaDB 服务（服务端内置 embedding 模型，客户端无需下载）
        self._use_server = False
        try:
            # HttpClient 默认也会初始化 ChromaDB telemetry；显式关闭避免 posthog 兼容性错误日志。
            self._client = chromadb.HttpClient(
                host=chroma_host,
                port=chroma_port,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )
            self._client.heartbeat()
            self._use_server = True
            logger.info(f"知识库 ChromaDB 已连接: {chroma_host}:{chroma_port}")
        except Exception:
            logger.info(f"知识库 ChromaDB 服务不可用，使用本地模式: {chroma_path}")
            self._client = chromadb.PersistentClient(
                path=chroma_path,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )

        # 使用服务端时不传 embedding_function，让服务端处理
        # 本地模式时也不传，使用 ChromaDB 默认的（会触发模型下载）
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"description": "SakikoMind RAG 知识库"},
        )

        # 如果知识库为空，导入默认文档
        if self._collection.count() == 0:
            self._load_default_docs()

    # ── 文档管理 ──────────────────────────────────────────────────────────────

    def add_documents(self, documents: List[Dict[str, Any]]) -> int:
        """
        批量导入文档到知识库。

        documents 格式: [{"source_id": "...", "title": "...", "content": "..."}, ...]
        长文档会自动切片（每片 500 字）。
        """
        ids, docs, metas = [], [], []

        for doc in documents:
            title   = doc.get("title", "")
            content = doc.get("content", "")
            chunks  = self._chunk_text(content, chunk_size=500)
            source_id = str(doc.get("source_id") or f"kb-{hashlib.sha1(title.encode()).hexdigest()[:12]}")

            for i, chunk in enumerate(chunks):
                doc_id = hashlib.md5(f"{title}_{i}_{chunk[:50]}".encode()).hexdigest()
                ids.append(doc_id)
                docs.append(chunk)
                metadata = {
                    "source_id": source_id,
                    "title": title,
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                }
                for key in ("version", "effective_date", "scope"):
                    value = doc.get(key)
                    if value is not None and str(value).strip():
                        metadata[key] = str(value).strip()
                metas.append(metadata)

        if ids:
            # ChromaDB 会自动生成 Embedding
            self._collection.add(ids=ids, documents=docs, metadatas=metas)
            logger.info(f"知识库导入 {len(ids)} 个文档片段")

        return len(ids)

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        混合检索：根据 query 返回语义和业务关键词都相关的文档片段。

        ChromaDB 内部自动将 query 转为向量，与存储的文档向量做余弦相似度匹配。
        """
        total_documents = self._collection.count()
        if total_documents == 0:
            return []

        candidate_count = min(max(top_k * 5, 20), total_documents)
        results = self._collection.query(
            query_texts=[query],
            n_results=candidate_count,
        )

        items = []
        if results["documents"] and results["documents"][0]:
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                semantic_score = round(1.0 - dist, 4)
                keyword_bonus = self._keyword_bonus(query, meta, doc)
                items.append({
                    "source_id": meta.get("source_id", ""),
                    "title":    meta.get("title", ""),
                    "content":  doc,
                    "score":    round(semantic_score + keyword_bonus, 4),
                    "semantic_score": semantic_score,
                    "keyword_bonus": keyword_bonus,
                    "chunk":    meta.get("chunk_index", 0),
                    "version": meta.get("version", ""),
                    "effective_date": meta.get("effective_date", ""),
                    "scope": meta.get("scope", ""),
                })

        return sorted(items, key=lambda item: item["score"], reverse=True)[:top_k]

    @property
    def doc_count(self) -> int:
        return self._collection.count()

    # ── MCP 工具 handler ─────────────────────────────────────────────────────

    async def search_handler(self, params: Dict[str, Any], context: Any) -> List[Dict]:
        """
        作为 MCP 工具的 handler 注册。

        MCPToolManager.register(Tool(
            name="knowledge_search",
            handler=kb.search_handler,
            ...
        ))
        """
        query = params.get("query", "")
        top_k = params.get("top_k", 5)
        return self.search(query, top_k=top_k)

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    def _chunk_text(self, text: str, chunk_size: int = 500) -> List[str]:
        """将长文本按 chunk_size 切片，保留语义完整性（按句号/换行切分）。"""
        if len(text) <= chunk_size:
            return [text] if text.strip() else []

        chunks = []
        current = ""
        # 按句子切分
        sentences = text.replace("\n", "。").split("。")
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            if len(current) + len(sent) + 1 > chunk_size:
                if current:
                    chunks.append(current)
                current = sent
            else:
                current = f"{current}。{sent}" if current else sent

        if current:
            chunks.append(current)

        return chunks

    @staticmethod
    def _keyword_bonus(query: str, metadata: Dict[str, Any], content: str) -> float:
        """为高风险和高业务特异性词汇加入确定性排序信号。"""
        query_text = (query or "").lower()
        title = str(metadata.get("title", "")).lower()
        source_id = str(metadata.get("source_id", ""))
        document = f"{title}\n{content}".lower()
        keywords = (
            "重复扣款", "退款", "退费", "发票", "续费", "订阅", "升级", "降级", "试用", "支付失败",
            "用量", "席位", "权限", "邀请", "异常登录", "密码", "401", "403",
            "超时", "限流", "429", "500", "api key", "webhook", "导入", "导出",
            "数据删除", "删除账号", "数据泄露", "服务等级", "状态页", "全站不可用",
            "数据丢失", "转人工", "投诉",
        )
        bonus = 0.0
        for keyword in keywords:
            if keyword not in query_text or keyword not in document:
                continue
            bonus += 0.45 if keyword in title else 0.2
        source_hints = {
            "SaaS-BILL-001": ("升级", "降级", "月付", "年付", "生效"),
            "SaaS-REFUND-001": ("退款", "退费", "退款到账"),
            "SaaS-INVOICE-001": ("发票", "付款凭证", "收据"),
            "SaaS-PAY-002": ("重复扣款", "扣了两次", "多扣"),
            "SaaS-SEC-001": ("异常登录", "陌生设备", "账号被盗"),
            "SaaS-DEV-001": ("api key", "密钥泄露", "key 泄露"),
            "SaaS-DATA-001": ("导出数据", "数据导出"),
            "SaaS-DATA-002": ("删除账号", "删除数据", "个人数据", "数据泄露"),
            "SaaS-INCIDENT-001": ("全站不可用", "大面积故障", "数据丢失", "很多用户", "服务异常"),
            "SaaS-HANDOFF-001": ("转人工", "人工客服", "投诉", "举报"),
        }
        if any(hint in query_text for hint in source_hints.get(source_id, ())):
            bonus += 0.9
        if metadata.get("effective_date"):
            bonus += 0.02
        return round(min(bonus, 1.5), 4)

    def _load_default_docs(self) -> None:
        """导入默认知识库文档（客服场景常见问题）。"""
        policy_path = pathlib.Path(__file__).resolve().parents[1] / "data" / "demo_docs" / "saas_policies.json"
        try:
            docs = json.loads(policy_path.read_text(encoding="utf-8"))
            if not isinstance(docs, list) or not docs:
                raise ValueError("默认知识库文档必须是非空数组")
            self.add_documents(docs)
            logger.info(f"已导入订阅制 SaaS 默认知识库: {len(docs)} 篇文档")
            return
        except Exception as ex:
            logger.warning(f"加载订阅制 SaaS 默认知识库失败，使用兼容默认文档: {ex}")

        default_docs = [
            {
                "title": "退款政策",
                "content": (
                    "退款政策说明。"
                    "用户在购买后 7 天内可以申请无理由退款。"
                    "退款申请提交后，系统会在 1-3 个工作日内审核。"
                    "审核通过后，款项将在 5-7 个工作日内退回原支付账户。"
                    "如果商品已发货，需要先完成退货流程才能退款。"
                    "退货运费由用户承担，除非是商品质量问题。"
                    "超过 7 天但未超过 30 天的订单，需要提供商品质量问题的证据才能退款。"
                ),
            },
            {
                "title": "订单查询",
                "content": (
                    "订单查询指南。"
                    "用户可以通过订单号查询订单状态。"
                    "订单状态包括：待支付、已支付、已发货、运输中、已签收、已完成。"
                    "如果订单显示已发货但超过 7 天未收到，可以联系客服申请查件。"
                    "物流信息通常在发货后 24 小时内更新。"
                    "如果订单显示异常，请提供订单号联系客服处理。"
                ),
            },
            {
                "title": "账户安全",
                "content": (
                    "账户安全说明。"
                    "建议用户定期修改密码，密码长度至少 8 位，包含字母和数字。"
                    "如果忘记密码，可以通过绑定的手机号或邮箱重置。"
                    "发现账户异常登录时，系统会自动锁定账户并发送通知。"
                    "用户可以在安全设置中开启两步验证，提高账户安全性。"
                    "不要将密码分享给他人，客服人员不会索要用户密码。"
                ),
            },
            {
                "title": "技术故障排查",
                "content": (
                    "常见技术问题排查。"
                    "应用崩溃：请尝试清除缓存后重启应用，如果问题持续请更新到最新版本。"
                    "登录失败 401 错误：表示认证失败，请检查用户名密码是否正确，或尝试重置密码。"
                    "页面加载慢：检查网络连接，尝试切换 WiFi 或移动数据。"
                    "支付失败：确认银行卡余额充足，检查是否开启了网上支付功能。"
                    "500 服务器错误：这是服务端问题，请稍后重试，如果持续出现请联系技术支持。"
                ),
            },
            {
                "title": "会员与积分",
                "content": (
                    "会员积分规则。"
                    "每消费 1 元累积 1 积分。"
                    "积分可以在下次购物时抵扣，100 积分 = 1 元。"
                    "会员等级分为：普通会员、银卡会员（累计消费 1000 元）、金卡会员（累计消费 5000 元）。"
                    "银卡会员享受 95 折优惠，金卡会员享受 9 折优惠。"
                    "积分有效期为 1 年，过期自动清零。"
                    "生日当月消费可获得双倍积分。"
                ),
            },
            {
                "title": "配送说明",
                "content": (
                    "配送服务说明。"
                    "标准配送：3-5 个工作日送达，免运费（订单满 99 元）。"
                    "加急配送：1-2 个工作日送达，运费 15 元。"
                    "同城配送：当日达或次日达，运费 10 元。"
                    "偏远地区可能需要额外 2-3 天。"
                    "配送时间为每天 9:00-18:00，节假日可能延迟。"
                    "如果需要修改收货地址，请在发货前联系客服。"
                ),
            },
        ]
        self.add_documents(default_docs)
        logger.info(f"已导入默认知识库: {len(default_docs)} 篇文档")

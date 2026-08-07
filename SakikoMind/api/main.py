"""
SakikoMind 智能客服系统 — FastAPI 入口

启动时打印小熊饼干图案。
所有核心组件在 lifespan 中初始化，通过环境变量配置。
"""
import asyncio
import hashlib
import logging
import os
import pathlib
import re
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Awaitable, Dict, List, Optional, TypeVar


_ROOT = str(pathlib.Path(__file__).parent.parent.resolve())
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request as FastAPIRequest, Response, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel

load_dotenv()

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BANNER = r"""
    ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ
   ╔══════════════════════╗
   ║   SakikoMind  v2.0     ║
   ║   智能客服 AI 系统    ║
   ╚══════════════════════╝
    ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ
"""

# ── 全局组件（lifespan 中初始化）─────────────────────────────────────────────
_orchestrator = None
_memory       = None
_tool_manager = None
_monitor      = None
_evaluator    = None
_skill_manager = None
_handoff_store = None
_StageResult = TypeVar("_StageResult")

HTTP_REQUESTS_TOTAL = Counter(
    "sakikomind_http_requests_total",
    "SakikoMind HTTP 请求总数",
    ["method", "path", "status"],
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "sakikomind_http_request_duration_seconds",
    "SakikoMind HTTP 请求耗时（秒）",
    ["method", "path", "status"],
)
CHAT_STAGE_DURATION_SECONDS = Histogram(
    "sakikomind_chat_stage_duration_seconds",
    "SakikoMind 对话各阶段耗时（秒）",
    ["stage", "outcome"],
)
CHAT_STAGE_FAILURES_TOTAL = Counter(
    "sakikomind_chat_stage_failures_total",
    "SakikoMind 对话各阶段失败次数",
    ["stage"],
)
PROFILE_UPDATE_ATTEMPTS_TOTAL = Counter(
    "sakikomind_profile_update_attempts_total",
    "SakikoMind 异步用户画像更新尝试次数",
    ["outcome"],
)
PROFILE_UPDATE_FAILURES_TOTAL = Counter(
    "sakikomind_profile_update_failures_total",
    "SakikoMind 异步用户画像更新失败次数",
)

def _anthropic_cfg() -> Dict[str, Any]:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("未设置 ANTHROPIC_API_KEY")
    cfg: Dict[str, Any] = {
        "api_key":  key,
        "model":    os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022").strip(),
    }
    base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()
    if base_url:
        cfg["base_url"] = base_url
    return cfg


def _cors_allowed_origins() -> List[str]:
    """从环境变量读取允许访问 API 的前端来源。"""
    raw_origins = os.getenv("CORS_ALLOWED_ORIGINS", "*")
    origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
    return origins or ["*"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _orchestrator, _memory, _tool_manager, _monitor, _evaluator, _skill_manager, _handoff_store

    print(BANNER, flush=True)

    from agents.agent_orchestrator import AgentOrchestrator, Request
    from core.handoff_store import HandoffStore
    from core.intent_recognizer import IntentRecognizer
    from evaluation.evaluator import EndToEndEvaluator
    from mcp.knowledge_base import KnowledgeBase
    from mcp.tool_manager import MCPToolManager, Tool
    from memory.conversation_memory import MemoryManager
    from monitor.performance_monitor import PerformanceMonitor
    from core.skill_loader import SkillManager

    cfg = _anthropic_cfg()
    logger.info(f"模型: {cfg['model']}  base_url: {cfg.get('base_url', '(官方)')}")

    # 意图识别器（Orchestrator 内部也会创建，这里单独暴露给 Evaluator）
    recognizer = IntentRecognizer(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )

    # Skills：启动时从目录加载业务能力说明，并在 Agent 调用 LLM 时动态注入。
    skills_dir = os.getenv("SAKIKOMIND_SKILLS_DIR", str(pathlib.Path(_ROOT) / "skills"))
    _skill_manager = SkillManager(
        root_dir=skills_dir,
        max_prompt_chars=int(os.getenv("SAKIKOMIND_SKILLS_MAX_PROMPT_CHARS", "5000")),
    )
    _skill_manager.load()
    handoff_db_path = os.getenv(
        "HANDOFF_DB_PATH",
        str(pathlib.Path(_ROOT) / "data" / "handoffs" / "tickets.db"),
    )
    _handoff_store = HandoffStore(handoff_db_path)

    # Agent 编排器
    _orchestrator = AgentOrchestrator(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        skill_manager=_skill_manager,
    )

    # 记忆管理器（Redis 工作记忆 + ChromaDB 情景记忆/用户画像）
    _memory = MemoryManager(
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )

    # MCP 工具管理器 + RAG 知识库（基于 ChromaDB 的真实检索）
    _tool_manager = MCPToolManager(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        llm_timeout_s=float(os.getenv("RAG_LLM_TIMEOUT_S", "8")),
    )
    kb = KnowledgeBase(
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
    )
    logger.info(f"知识库已加载: {kb.doc_count} 个文档片段")

    def knowledge_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        query = params.get("query", "")
        return [{
            "title": "知识库降级结果",
            "content": f"知识库暂时不可用，未能完成对“{query}”的语义检索。请稍后重试，或转人工客服确认。",
            "score": 0.0,
            "fallback": True,
            "error": error,
        }]

    _tool_manager.register(Tool(
        name="knowledge_search",
        description="搜索知识库（基于 ChromaDB 向量检索）",
        handler=kb.search_handler,
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer"},
            },
            "required": ["query"],
        },
        cache_ttl=300.0,
        supports_rerank=True,
        fallback=knowledge_fallback,
    ))

    # 性能监控（可选启动 Prometheus）
    prom_port = int(os.getenv("PROMETHEUS_PORT", "0")) or None
    _monitor = PerformanceMonitor(
        orchestrator=_orchestrator,
        tool_manager=_tool_manager,
        interval_s=float(os.getenv("MONITOR_INTERVAL", "10")),
        webhook_url=os.getenv("ALERT_WEBHOOK_URL") or None,
        prometheus_port=prom_port,
    )
    await _monitor.start()

    # 评测器
    _evaluator = EndToEndEvaluator(
        orchestrator=_orchestrator,
        recognizer=recognizer,
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        baseline_path=os.getenv("EVAL_BASELINE_PATH", "/app/data/eval/baseline.json"),
    )

    logger.info("SakikoMind 已就绪")
    yield

    await _monitor.stop()
    logger.info("SakikoMind 已关闭")


# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(
    title="SakikoMind 智能客服",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allowed_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 请求/响应模型 ─────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message:     str
    user_id:     str = "anonymous"
    conv_id:     Optional[str] = None


class KnowledgeCitation(BaseModel):
    """本次回复实际使用的知识库来源摘要。"""
    source_id: str
    title: str
    chunk: int
    score: float
    content_preview: str
    version: Optional[str] = None
    effective_date: Optional[str] = None
    scope: Optional[str] = None


class SkillUsage(BaseModel):
    """本次回复实际注入的业务 Skill 摘要。"""
    name: str
    version: str
    owner: str = ""
    matched_keywords: List[str] = []


class HandoffTicket(BaseModel):
    """SakikoMind 内置人工工单，可持久化查询和更新处理状态。"""
    ticket_id: str
    trace_id: str = ""
    conv_id: str
    user_id: str
    agent_type: str
    status: str = "open"
    delivery: str = "internal_ticket_center"
    reason: str
    reason_label: str
    priority: str
    summary: str
    created_at: str
    updated_at: str
    citation_source_ids: List[str] = []


class HandoffTicketUpdate(BaseModel):
    status: str


class HandoffTicketList(BaseModel):
    items: List[HandoffTicket] = []


class ChatResponse(BaseModel):
    trace_id:    str
    conv_id:     str
    response:    str
    intent:      str
    agent_type:  str
    escalated:   bool
    latency_ms:  float
    knowledge_used: bool = False
    citations: List[KnowledgeCitation] = []
    skills_used: List[SkillUsage] = []
    handoff_ticket: Optional[HandoffTicket] = None


# ── 路由 ──────────────────────────────────────────────────────────────────────
_TRACE_ID_PATTERN = re.compile(r"[^A-Za-z0-9._:-]+")


def _normalize_trace_id(value: Optional[str]) -> str:
    """规范客户端追踪标识；无有效值时生成新的请求级标识。"""
    normalized = _TRACE_ID_PATTERN.sub("", (value or "").strip())[:64]
    return normalized or f"trc-{uuid.uuid4().hex[:16]}"


@app.middleware("http")
async def trace_requests(request: FastAPIRequest, call_next):
    """为每个 HTTP 请求建立可回传、可检索的 trace ID。"""
    trace_id = _normalize_trace_id(request.headers.get("X-Trace-ID"))
    request.state.trace_id = trace_id
    started_at = time.monotonic()
    logger.info(f"[trace_id={trace_id}] HTTP 开始 method={request.method} path={request.url.path}")
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = (time.monotonic() - started_at) * 1000
        metric_path = _metric_path(request)
        HTTP_REQUESTS_TOTAL.labels(request.method, metric_path, "500").inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(request.method, metric_path, "500").observe(elapsed_ms / 1000)
        logger.exception(f"[trace_id={trace_id}] HTTP 异常 duration_ms={elapsed_ms:.1f}")
        raise
    response.headers["X-Trace-ID"] = trace_id
    elapsed_ms = (time.monotonic() - started_at) * 1000
    metric_path = _metric_path(request)
    status = str(response.status_code)
    HTTP_REQUESTS_TOTAL.labels(request.method, metric_path, status).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(request.method, metric_path, status).observe(elapsed_ms / 1000)
    logger.info(
        f"[trace_id={trace_id}] HTTP 完成 status={response.status_code} duration_ms={elapsed_ms:.1f}"
    )
    return response


def _metric_path(request: FastAPIRequest) -> str:
    """返回低基数指标路径，避免把动态 ID 写入 Prometheus 标签。"""
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if route_path:
        return str(route_path)
    return request.url.path


async def _run_chat_stage(
    stage: str,
    operation: Awaitable[_StageResult],
    trace_id: str,
) -> _StageResult:
    """记录对话阶段的耗时与失败指标，并保持原始异常语义。"""
    started_at = time.monotonic()
    try:
        result = await operation
    except asyncio.CancelledError:
        raise
    except Exception:
        elapsed_ms = (time.monotonic() - started_at) * 1000
        CHAT_STAGE_FAILURES_TOTAL.labels(stage).inc()
        CHAT_STAGE_DURATION_SECONDS.labels(stage, "failure").observe(elapsed_ms / 1000)
        logger.exception(
            f"[trace_id={trace_id}] 阶段失败 stage={stage} duration_ms={elapsed_ms:.1f}"
        )
        raise

    elapsed_ms = (time.monotonic() - started_at) * 1000
    CHAT_STAGE_DURATION_SECONDS.labels(stage, "success").observe(elapsed_ms / 1000)
    logger.info(
        f"[trace_id={trace_id}] 阶段完成 stage={stage} duration_ms={elapsed_ms:.1f}"
    )
    return result


async def _update_profile_safely(
    memory_manager: Any,
    user_id: str,
    conv_id: str,
    trace_id: str,
    *,
    attempts: int = 2,
    retry_delay_s: float = 0.2,
) -> bool:
    """在响应返回后更新画像；有限重试并把失败暴露为指标。"""
    for attempt in range(1, attempts + 1):
        try:
            await memory_manager.update_profile(user_id, conv_id)
            PROFILE_UPDATE_ATTEMPTS_TOTAL.labels("success").inc()
            logger.info(
                f"[trace_id={trace_id}] 用户画像更新完成 attempt={attempt}"
            )
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            PROFILE_UPDATE_ATTEMPTS_TOTAL.labels("failure").inc()
            PROFILE_UPDATE_FAILURES_TOTAL.inc()
            logger.exception(
                f"[trace_id={trace_id}] 用户画像更新失败 attempt={attempt}/{attempts}"
            )
            if attempt < attempts:
                await asyncio.sleep(retry_delay_s)
    return False


@app.get("/health")
async def health():
    if _orchestrator is None:
        raise HTTPException(503, "服务未就绪")
    return {"status": "ok", "agents": _orchestrator.get_stats()}


@app.get("/skills", tags=["Skills"])
async def skills_summary():
    """查看当前已加载的 Skills，便于确认热加载结果和排查解析错误。"""
    if _skill_manager is None:
        raise HTTPException(503, "Skills 未初始化")
    return _skill_manager.summary()


@app.post("/skills/reload", tags=["Skills"])
async def reload_skills():
    """运行时重新扫描 Skill 目录，不需要重启服务。"""
    if _skill_manager is None:
        raise HTTPException(503, "Skills 未初始化")
    _skill_manager.reload()
    if _orchestrator is not None:
        _orchestrator.set_skill_manager(_skill_manager)
    return _skill_manager.summary()


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: FastAPIRequest):
    """
    主对话接口。完整流程：
      记忆读取 → 意图识别 → Agent 路由 → 执行 → 记忆写入
    """
    if _orchestrator is None or _memory is None:
        raise HTTPException(503, "服务未就绪")

    from agents.agent_orchestrator import Request as OrcReq
    from memory.conversation_memory import MsgRole

    trace_id = request.state.trace_id
    conv_id = req.conv_id or str(uuid.uuid4())
    total_started_at = time.monotonic()

    # 1. 读取记忆上下文
    mem_ctx = await _run_chat_stage(
        "memory_read",
        _memory.get_context(req.user_id, conv_id, query=req.message),
        trace_id,
    )

    # 2. 构建编排请求（含对话历史，用于意图识别上下文）
    history = [
        {"role": m.role.value, "content": m.content}
        for m in mem_ctx.recent_messages[-5:]
    ] if mem_ctx.recent_messages else None

    knowledge_text, citations = await _run_chat_stage(
        "rag",
        _build_knowledge_context(req.message, trace_id=trace_id),
        trace_id,
    )
    logger.info(f"[trace_id={trace_id}] RAG 引用数量 citations={len(citations)}")
    context_parts = [mem_ctx.to_prompt_text()]
    if knowledge_text:
        context_parts.append(knowledge_text)
    full_context = "\n\n".join(part for part in context_parts if part)

    orch_req = OrcReq(
        message=req.message,
        user_id=req.user_id,
        conv_id=conv_id,
        context=full_context,
        history=history,
        request_id=trace_id,
    )

    # 3. 执行
    result = await _run_chat_stage("agent", _orchestrator.run(orch_req), trace_id)
    logger.info(
        f"[trace_id={trace_id}] Agent 结果 "
        f"agent={result.agent_type.value} intent={result.intent.value if result.intent else 'other'}"
    )

    # 4. 写入记忆
    async def write_conversation_memory() -> None:
        await _memory.add_message(req.user_id, conv_id, MsgRole.USER, req.message)
        await _memory.add_message(req.user_id, conv_id, MsgRole.ASSISTANT, result.response)

    await _run_chat_stage("memory_write", write_conversation_memory(), trace_id)

    # 5. 异步更新用户画像（不阻塞响应）
    asyncio.create_task(
        _update_profile_safely(_memory, req.user_id, conv_id, trace_id),
        name=f"profile-update:{trace_id}",
    )

    skills_used = (
        [SkillUsage(**usage) for usage in _skill_manager.usage_for(req.message, result.agent_type.value)]
        if _skill_manager is not None else []
    )
    skill_requires_escalation = (
        _skill_manager.requires_escalation(req.message)
        if _skill_manager is not None else False
    )
    escalated = result.escalated or skill_requires_escalation
    intent = result.intent.value if result.intent else "other"
    handoff_ticket = _build_handoff_ticket(
        message=req.message,
        trace_id=trace_id,
        conv_id=conv_id,
        user_id=req.user_id,
        agent_type=result.agent_type.value,
        intent=intent,
        escalated=escalated,
        citations=citations,
    )
    if handoff_ticket is not None and _handoff_store is not None:
        handoff_ticket = HandoffTicket(**_handoff_store.create(handoff_ticket.dict()))
        logger.info(
            f"[trace_id={trace_id}] 工单创建 ticket_id={handoff_ticket.ticket_id} "
            f"reason={handoff_ticket.reason} priority={handoff_ticket.priority}"
        )

    logger.info(
        f"[trace_id={trace_id}] 对话完成 escalated={escalated} "
        f"duration_ms={(time.monotonic() - total_started_at) * 1000:.1f}"
    )

    return ChatResponse(
        trace_id=trace_id,
        conv_id=conv_id,
        response=result.response,
        intent=intent,
        agent_type=result.agent_type.value,
        escalated=escalated,
        latency_ms=round(result.latency_ms, 1),
        knowledge_used=bool(citations),
        citations=citations,
        skills_used=skills_used,
        handoff_ticket=handoff_ticket,
    )


@app.get("/handoffs", response_model=HandoffTicketList, tags=["人工工单"])
async def list_handoffs(status: Optional[str] = None, limit: int = 50):
    """查询 SakikoMind 内置人工工单，默认返回最近更新的 50 条。"""
    if _handoff_store is None:
        raise HTTPException(503, "人工工单服务未初始化")
    return HandoffTicketList(items=[HandoffTicket(**item) for item in _handoff_store.list(status, limit)])


@app.patch("/handoffs/{ticket_id}", response_model=HandoffTicket, tags=["人工工单"])
async def update_handoff(ticket_id: str, req: HandoffTicketUpdate):
    """更新内置人工工单状态。公开部署前应配合管理员鉴权使用。"""
    if _handoff_store is None:
        raise HTTPException(503, "人工工单服务未初始化")
    try:
        ticket = _handoff_store.update_status(ticket_id, req.status)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if ticket is None:
        raise HTTPException(404, "工单不存在")
    return HandoffTicket(**ticket)


_HANDOFF_REASON_RULES = (
    ("account_security", "账号安全风险", "P1", ("账号被盗", "陌生设备", "密码重置", "密钥泄露", "api key泄露", "api key 泄露", "未授权交易")),
    ("payment_dispute", "账务争议", "P1", ("重复扣款", "扣款错误", "支付争议", "账单争议", "未授权扣款")),
    ("tool_failure", "系统或工具故障", "P1", ("全站不可用", "大面积故障", "503", "502", "504", "数据丢失", "系统宕机")),
    ("privacy_request", "隐私数据请求", "P2", ("删除数据", "删除账号", "导出数据", "数据泄露", "gdpr")),
    ("abusive_or_complaint", "投诉升级", "P2", ("投诉", "举报", "欺诈", "威胁")),
    ("user_requested_human", "用户主动要求人工", "P2", ("转人工", "人工客服", "人工处理", "找人工")),
)


def _build_handoff_ticket(
    *,
    message: str,
    trace_id: str,
    conv_id: str,
    user_id: str,
    agent_type: str,
    intent: str,
    escalated: bool,
    citations: List[KnowledgeCitation],
) -> Optional[HandoffTicket]:
    """为已升级请求创建 SakikoMind 内置工单。"""
    if not escalated:
        return None

    lowered_message = (message or "").lower()
    reason = None
    for code, label, priority, keywords in _HANDOFF_REASON_RULES:
        if any(keyword in lowered_message for keyword in keywords):
            reason = (code, label, priority)
            break

    if reason is None and intent == "escalation":
        reason = ("user_requested_human", "用户主动要求人工", "P2")
    if reason is None:
        reason = ("low_grounding", "需要人工核验", "P2")

    code, label, priority = reason
    if code == "tool_failure" and any(keyword in lowered_message for keyword in ("全站不可用", "数据丢失")):
        priority = "P0"

    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return HandoffTicket(
        ticket_id=f"EM-{uuid.uuid4().hex[:10].upper()}",
        trace_id=trace_id,
        conv_id=conv_id,
        user_id=user_id,
        agent_type=agent_type,
        reason=code,
        reason_label=label,
        priority=priority,
        summary=_safe_handoff_summary(message, agent_type),
        created_at=timestamp,
        updated_at=timestamp,
        citation_source_ids=[citation.source_id for citation in citations[:3]],
    )


def _safe_handoff_summary(message: str, agent_type: str) -> str:
    """保留必要问题摘要，同时隐藏常见的密钥与长数字。"""
    compact_message = " ".join((message or "").split())
    compact_message = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[已隐藏密钥]", compact_message, flags=re.IGNORECASE)
    compact_message = re.sub(r"\b\d{16,19}\b", "[已隐藏长数字]", compact_message)
    return f"{agent_type} Agent：{compact_message[:120]}"


async def _build_knowledge_context(
    message: str,
    top_k: int = 3,
    trace_id: Optional[str] = None,
) -> tuple[str, List[KnowledgeCitation]]:
    """
    为 /chat 主链路构建 RAG 知识上下文。

    这里复用 MCPToolManager 的查询改写、并行召回、重排、fallback 能力。
    """
    if _tool_manager is None:
        return "", []
    if not _should_use_knowledge(message):
        return "", []
    try:
        result = await _tool_manager.search_with_rewrite(
            "knowledge_search",
            message,
            top_k=top_k,
            context={"trace_id": trace_id} if trace_id else None,
        )
        if not result.success or not isinstance(result.data, list) or not result.data:
            return "", []

        parts = ["[知识库检索结果]"]
        citations: List[KnowledgeCitation] = []
        for i, item in enumerate(result.data[:top_k], start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "未命名文档"))
            content = str(item.get("content", "")).strip()
            score = item.get("score", "")
            if not content:
                continue
            source_id = str(item.get("source_id") or f"legacy-{hashlib.sha1(title.encode()).hexdigest()[:12]}")
            citations.append(KnowledgeCitation(
                source_id=source_id,
                title=title,
                chunk=int(item.get("chunk", 0)),
                score=float(score) if isinstance(score, (int, float)) else 0.0,
                content_preview=content[:180],
                version=str(item.get("version") or "") or None,
                effective_date=str(item.get("effective_date") or "") or None,
                scope=str(item.get("scope") or "") or None,
            ))
            parts.append(f"{i}. 来源: {source_id}\n   标题: {title}\n   相关度: {score}\n   内容: {content[:600]}")

        if not citations:
            return "", []
        parts.append("请优先依据以上知识库内容回答；如果知识库内容不足，再结合通用客服能力说明。")
        return "\n".join(parts), citations
    except Exception as ex:
        logger.warning(f"[trace_id={trace_id or '-'}] 构建知识库上下文失败: {ex}")
        return "", []


def _should_use_knowledge(message: str) -> bool:
    """跳过纯寒暄，业务类问题才检索知识库，避免无关 RAG 干扰回复。"""
    msg = (message or "").strip().lower()
    if not msg:
        return False
    greetings = {"你好", "您好", "嗨", "hi", "hello", "hey", "早上好", "晚上好"}
    if msg in greetings:
        return False
    business_keywords = [
        "退款", "订单", "物流", "配送", "发票", "扣款", "支付", "账单", "订阅",
        "登录", "报错", "错误", "崩溃", "会员", "积分", "账户", "密码", "地址",
        "refund", "order", "invoice", "payment", "error", "login",
    ]
    return len(msg) >= 4 or any(kw in msg for kw in business_keywords)


@app.get("/monitor")
async def monitor_summary():
    """实时监控摘要：Agent 成功率、工具统计、告警、优化建议。"""
    if _monitor is None:
        raise HTTPException(503, "服务未就绪")
    return _monitor.summary()


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus 指标入口。"""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/search")
async def search(query: str, top_k: int = 5):
    """
    演示检索优化链路：查询改写 → 并行召回 → 重排 → Top-K。
    展示 MCP 工具调用的核心亮点。
    """
    if _tool_manager is None:
        raise HTTPException(503, "服务未就绪")
    result = await _tool_manager.search_with_rewrite("knowledge_search", query, top_k=top_k)
    return {"query": query, "results": result.data, "reranked": result.reranked}


class DocInput(BaseModel):
    """单篇文档输入。"""
    title:   str
    content: str
    source_id: Optional[str] = None
    version: Optional[str] = None
    effective_date: Optional[str] = None
    scope: Optional[str] = None


class BatchDocInput(BaseModel):
    """批量文档导入请求体。"""
    documents: List[DocInput]


class EvalIntentInput(BaseModel):
    """意图识别评测用例。"""
    message: str
    expected_intent: str
    context: Optional[Dict[str, Any]] = None


class EvalDialogInput(BaseModel):
    """对话质量评测用例。question 单轮，turns 多轮。"""
    question: Optional[str] = None
    turns: Optional[List[str]] = None
    user_id: Optional[str] = None
    conv_id: Optional[str] = None


class EvalRunInput(BaseModel):
    """评测请求。为空时使用内置默认用例。"""
    intent_cases: Optional[List[EvalIntentInput]] = None
    dialog_cases: Optional[List[EvalDialogInput]] = None


@app.post("/knowledge/add", tags=["知识库"])
async def add_knowledge(body: BatchDocInput):
    """
    批量导入文档到知识库。

    文档会自动切片（每片 500 字）并存入 ChromaDB，ChromaDB 内置 Embedding 模型自动向量化。

    示例请求体：
    ```json
    {
      "documents": [
        {"title": "退款政策", "content": "用户在购买后 7 天内可以申请无理由退款..."},
        {"title": "配送说明", "content": "标准配送 3-5 个工作日..."}
      ]
    }
    ```
    """
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    count = kb.add_documents([
        {
            "source_id": d.source_id,
            "title": d.title,
            "content": d.content,
            "version": d.version,
            "effective_date": d.effective_date,
            "scope": d.scope,
        }
        for d in body.documents
    ])
    return {"message": f"成功导入 {count} 个文档片段", "added_chunks": count, "total_chunks": kb.doc_count}


@app.post("/knowledge/upload", tags=["知识库"])
async def upload_knowledge(file: UploadFile = File(...)):
    """
    上传文件导入知识库。

    支持格式：
    - `.txt` / `.md`：整个文件作为一篇文档，文件名作为标题
    - `.json`：JSON 数组格式 `[{"title": "...", "content": "..."}, ...]`

    文件大小限制：10MB
    """
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "文件大小超过 10MB 限制")

    text = content.decode("utf-8", errors="ignore")
    filename = file.filename or "unknown"

    if filename.endswith(".json"):
        import json as _json
        try:
            docs = _json.loads(text)
            if not isinstance(docs, list):
                raise HTTPException(400, "JSON 文件应为数组格式: [{title, content}, ...]")
        except _json.JSONDecodeError as e:
            raise HTTPException(400, f"JSON 解析失败: {e}")
    else:
        # txt / md：整个文件作为一篇文档
        title = filename.rsplit(".", 1)[0] if "." in filename else filename
        docs = [{"title": title, "content": text}]

    count = kb.add_documents(docs)
    return {
        "message": f"文件 {filename} 导入成功",
        "added_chunks": count,
        "total_chunks": kb.doc_count,
    }


@app.get("/knowledge/stats", tags=["知识库"])
async def knowledge_stats():
    """查看知识库统计信息（文档片段总数）。"""
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    return {"total_chunks": kb.doc_count}


@app.post("/eval/run")
async def run_eval(body: Optional[EvalRunInput] = None):
    """运行内置评测用例，返回评测报告。"""
    if _evaluator is None:
        raise HTTPException(503, "服务未就绪")
    from evaluation.evaluator import DEFAULT_DIALOG_CASES, DEFAULT_INTENT_CASES, IntentTestCase

    if body and body.intent_cases is not None:
        intent_cases = [
            IntentTestCase(
                message=c.message,
                expected_intent=c.expected_intent,
                context=c.context,
            )
            for c in body.intent_cases
        ]
    else:
        intent_cases = DEFAULT_INTENT_CASES

    if body and body.dialog_cases is not None:
        dialog_cases = [
            c.model_dump(exclude_none=True)
            for c in body.dialog_cases
        ]
    else:
        dialog_cases = DEFAULT_DIALOG_CASES

    report = await _evaluator.run(
        intent_cases=intent_cases,
        dialog_cases=dialog_cases,
    )
    intent_result = next(
        (item for item in report.results if item.test_id == "intent_recognition"),
        None,
    )
    dialog_failures = [
        {
            "test_id": item.test_id,
            "detail": item.detail,
            "question": item.metadata.get("question"),
            "overall": item.scores.get("overall"),
            "judge_failed": item.metadata.get("judge_failed", False),
            "judge_error": item.metadata.get("judge_error"),
        }
        for item in report.results
        if (
            item.test_id != "intent_recognition"
            and not item.passed
            and not item.metadata.get("judge_failed", False)
        )
    ]
    judge_failures = [
        {
            "test_id": item.test_id,
            "question": item.metadata.get("question"),
            "judge_error": item.metadata.get("judge_error"),
        }
        for item in report.results
        if item.metadata.get("judge_failed", False)
    ]
    return {
        "pass_rate":       report.pass_rate,
        "total":           report.total,
        "passed":          report.passed,
        "inconclusive":    report.inconclusive,
        "avg_scores":      report.avg_scores,
        "regressions":     report.regressions,
        "recommendations": report.recommendations,
        "snapshot_path":   _evaluator.last_snapshot_path,
        "failure_summary": {
            "intent_cases": intent_result.metadata.get("failed_cases", []) if intent_result else [],
            "dialog_cases": dialog_failures,
            "judge_failures": judge_failures,
        },
        "results": [
            {
                "test_id": r.test_id,
                "passed": r.passed,
                "scores": r.scores,
                "detail": r.detail,
                "metadata": r.metadata,
            }
            for r in report.results
        ],
    }


# ── 交互式 CLI ────────────────────────────────────────────────────────────────
async def _cli():
    print(BANNER)
    print("SakikoMind CLI — 输入 quit 退出\n")

    from agents.agent_orchestrator import AgentOrchestrator, Request
    from memory.conversation_memory import MemoryManager, MsgRole
    from core.skill_loader import SkillManager

    cfg = _anthropic_cfg()
    skill_manager = SkillManager(
        root_dir=os.getenv("SAKIKOMIND_SKILLS_DIR", str(pathlib.Path(_ROOT) / "skills")),
        max_prompt_chars=int(os.getenv("SAKIKOMIND_SKILLS_MAX_PROMPT_CHARS", "5000")),
    )
    skill_manager.load()
    orch = AgentOrchestrator(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        skill_manager=skill_manager,
    )
    mem  = MemoryManager(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        chroma_host=os.getenv("CHROMA_HOST", "localhost"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/tmp/chroma"),
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )

    user_id, conv_id = "cli_user", str(uuid.uuid4())

    while True:
        try:
            msg = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见 ʕ•ᴥ•ʔ")
            break
        if not msg or msg.lower() in ("quit", "exit", "退出"):
            print("再见 ʕ•ᴥ•ʔ")
            break

        ctx = await mem.get_context(user_id, conv_id, query=msg)
        history = [
            {"role": m.role.value, "content": m.content}
            for m in ctx.recent_messages[-5:]
        ] if ctx.recent_messages else None
        req = Request(message=msg, user_id=user_id, conv_id=conv_id, context=ctx.to_prompt_text(), history=history)
        result = await orch.run(req)

        await mem.add_message(user_id, conv_id, MsgRole.USER, msg)
        await mem.add_message(user_id, conv_id, MsgRole.ASSISTANT, result.response)

        print(f"\nSakikoMind [{result.agent_type.value}]: {result.response}\n")


if __name__ == "__main__":
    if "--cli" in sys.argv:
        asyncio.run(_cli())
    else:
        uvicorn.run(
            "api.main:app",
            host=os.getenv("API_HOST", "0.0.0.0"),
            port=int(os.getenv("API_PORT", "8000")),
            reload=os.getenv("APP_ENV") == "development",
        )

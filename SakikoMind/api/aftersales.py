"""Authenticated simulated after-sales API, also runnable without Redis/ChromaDB."""
import asyncio
import hmac
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool

from core.aftersales_planner import AfterSalesPlan, LLMPlanner
from core.aftersales_store import AfterSalesError, AfterSalesStore, Principal


router = APIRouter(prefix="/aftersales", tags=["模拟电商售后"])
bearer = HTTPBearer(auto_error=False)


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class QuoteRequest(StrictBody):
    order_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=500)


class ConfirmationRequest(StrictBody):
    quote_id: str = Field(min_length=1, max_length=128)
    confirmation_token: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("需要明确的布尔确认")
        return value


class ReviewRequest(StrictBody):
    approved: bool
    note: str = Field(min_length=1, max_length=1000)


class AfterSalesChatRequest(StrictBody):
    message: str = Field(min_length=1, max_length=2000)
    order_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    application_id: Optional[str] = Field(default=None, min_length=1, max_length=128)


def _tokens(value: Any) -> Dict[str, Principal]:
    """The server, rather than request fields, assigns the principal and role."""
    if not isinstance(value, dict):
        raise ValueError("AFTERSALES_TOKENS_JSON 必须是令牌到身份的映射")
    result = {}
    for token, identity in value.items():
        if not isinstance(token, str) or not token.strip() or len(token) > 512:
            raise ValueError("售后令牌配置无效")
        if not isinstance(identity, dict) or set(identity) != {"user_id", "role"}:
            raise ValueError("售后身份必须包含 user_id 和 role")
        user_id, role = identity["user_id"], identity["role"]
        if not isinstance(user_id, str) or not user_id.strip() or user_id != user_id.strip() or len(user_id) > 128:
            raise ValueError("售后用户标识配置无效")
        if not isinstance(role, str) or role not in {"customer", "reviewer"}:
            raise ValueError("售后角色配置无效")
        result[token] = Principal(user_id, role)
    return result


def configure_aftersales(
    app: FastAPI,
    *,
    store: Optional[AfterSalesStore] = None,
    tokens: Optional[dict] = None,
    planner: Any = None,
) -> None:
    from_environment = store is None
    if from_environment:
        load_dotenv()
        store = AfterSalesStore(os.getenv(
            "AFTERSALES_DB_PATH",
            str(Path(__file__).resolve().parent.parent / "data" / "aftersales" / "orders.db"),
        ))
        if os.getenv("AFTERSALES_DEMO_SEED", "0") == "1":
            store.seed_demo()
    if tokens is None:
        tokens = json.loads(os.getenv("AFTERSALES_TOKENS_JSON", "{}"))
    app.state.aftersales_tokens = _tokens(tokens)
    app.state.aftersales_store = store
    if planner is None and from_environment and os.getenv("ANTHROPIC_API_KEY"):
        planner = LLMPlanner(
            api_key=os.environ["ANTHROPIC_API_KEY"],
            model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
            base_url=os.getenv("ANTHROPIC_BASE_URL") or None,
        )
    app.state.aftersales_planner = planner


def get_store(request: Request) -> AfterSalesStore:
    store = getattr(request.app.state, "aftersales_store", None)
    if store is None:
        raise HTTPException(503, "售后服务未初始化")
    return store


def require_principal(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
) -> Principal:
    tokens = getattr(request.app.state, "aftersales_tokens", {})
    if not tokens:
        raise HTTPException(503, "售后身份认证尚未配置")
    if credentials and credentials.scheme.lower() == "bearer":
        supplied = credentials.credentials.encode("utf-8")
        for token, principal in tokens.items():
            if hmac.compare_digest(supplied, token.encode("utf-8")):
                return principal
    raise HTTPException(401, "需要有效的售后访问令牌", headers={"WWW-Authenticate": "Bearer"})


def trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", "") or f"trc-{uuid.uuid4().hex[:16]}"


async def domain_error_handler(request: Request, error: AfterSalesError):
    return JSONResponse(status_code=error.status_code, content={
        "detail": {"code": error.code, "message": error.message},
        "trace_id": trace_id(request),
    })


def install_aftersales(app: FastAPI) -> None:
    app.include_router(router)
    app.add_exception_handler(AfterSalesError, domain_error_handler)


@router.get("/orders/{order_id}")
def order(order_id: str, request: Request, principal: Principal = Depends(require_principal)):
    return get_store(request).get_order(principal, order_id)


@router.post("/quotes")
def quote(body: QuoteRequest, request: Request, principal: Principal = Depends(require_principal)):
    return get_store(request).prepare(principal, body.order_id, body.reason, trace_id(request))


@router.post("/applications")
def confirm(body: ConfirmationRequest, request: Request, principal: Principal = Depends(require_principal)):
    return get_store(request).confirm(
        principal, body.quote_id, body.confirmation_token, body.idempotency_key, trace_id(request),
    )


@router.get("/applications/{application_id}")
def application(application_id: str, request: Request, principal: Principal = Depends(require_principal)):
    return get_store(request).get_application(principal, application_id)


@router.post("/applications/{application_id}/review")
def review(
    application_id: str, body: ReviewRequest, request: Request,
    principal: Principal = Depends(require_principal),
):
    return get_store(request).review(principal, application_id, body.approved, body.note, trace_id(request))


@router.post("/applications/{application_id}/execute")
def execute(application_id: str, request: Request, principal: Principal = Depends(require_principal)):
    try:
        return get_store(request).execute(principal, application_id, trace_id(request))
    except TimeoutError:
        # A write may already be committed. Clients must read this operation before retrying.
        return JSONResponse(status_code=504, content={
            "detail": {
                "code": "outcome_unknown",
                "message": "执行结果暂未确认，请查询申请状态后再决定是否重试。",
                "application_id": application_id,
                "status_url": f"/aftersales/applications/{application_id}",
            },
            "trace_id": trace_id(request),
            "simulated": True,
        })


@router.get("/applications/{application_id}/audit")
def audit(application_id: str, request: Request, principal: Principal = Depends(require_principal)):
    return get_store(request).audit(principal, application_id)


async def run_aftersales_chat(body: AfterSalesChatRequest, request: Request, principal: Principal) -> dict:
    planner = getattr(request.app.state, "aftersales_planner", None)
    if planner is None:
        raise HTTPException(503, "售后语言理解服务未配置；订单查询和售后操作接口可独立使用。")
    try:
        suggestion = await asyncio.wait_for(planner.plan(body.message), timeout=16.0)
        plan = AfterSalesPlan.model_validate(suggestion)
    except (ValidationError, ValueError, TypeError):
        raise HTTPException(502, "售后语言理解结果无效，尚未提交申请。")
    except Exception:
        raise HTTPException(503, "售后语言理解暂时不可用，尚未提交申请。")

    store = get_store(request)
    data = None
    action = plan.action
    # Explicit UI-selected IDs override no other order silently: ambiguity requires clarification.
    if body.order_id and plan.order_id and body.order_id != plan.order_id:
        action, response = "clarify", "你选择的订单与消息中的订单不同，请先确认要处理的订单号。"
    elif body.application_id and plan.application_id and body.application_id != plan.application_id:
        action, response = "clarify", "你选择的申请与消息中的申请不同，请先确认申请号。"
    elif action == "status":
        application_id = body.application_id or plan.application_id
        if not application_id:
            action, response = "clarify", "请提供需要查询的售后申请号。"
        else:
            data = await run_in_threadpool(store.get_application, principal, application_id)
            labels = {"ready": "已确认，等待模拟执行", "pending_approval": "等待人工审批",
                      "succeeded": "模拟取消与退款已完成", "rejected": "审批未通过",
                      "stale": "订单状态已变化，需要重新核实"}
            response = f"申请 {application_id}：{labels.get(data['status'], data['status'])}。"
    elif action in {"lookup", "prepare"}:
        order_id = body.order_id or plan.order_id
        if not order_id:
            action, response = "clarify", "请提供需要查询或取消的订单号。"
        elif action == "prepare" and not (plan.reason or "").strip():
            action, response = "clarify", "请说明取消订单的原因，再核对模拟退款方案。"
        elif action == "lookup":
            data = await run_in_threadpool(store.get_order, principal, order_id)
            response = f"订单 {order_id} 当前状态：{data['status']}。"
        else:
            data = await run_in_threadpool(store.prepare, principal, order_id, plan.reason, trace_id(request))
            amount = f"{data['amount_cents'] // 100}.{data['amount_cents'] % 100:02d}"
            approval = "，提交后需要人工审批" if data["approval_required"] else ""
            response = f"订单 {order_id} 可申请取消，模拟退款金额 {amount} 元{approval}。请核对方案并明确确认。当前尚未创建售后申请。"
    else:
        response = "可以查询订单、准备取消方案或查询售后进展；提交方案需要你明确确认。"
    return {"response": response, "action": action, "data": data,
            "trace_id": trace_id(request), "simulated": True}


@router.post("/chat")
async def chat(body: AfterSalesChatRequest, request: Request, principal: Principal = Depends(require_principal)):
    return await run_aftersales_chat(body, request, principal)


def create_app(*, store=None, tokens=None, planner=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        if store is None:
            configure_aftersales(app, tokens=tokens, planner=planner)
        yield

    app = FastAPI(title="SakikoMind 模拟售后", lifespan=lifespan)
    install_aftersales(app)
    if store is not None:
        configure_aftersales(app, store=store, tokens=tokens, planner=planner)

    @app.middleware("http")
    async def request_trace(request, call_next):
        # Server-generated IDs avoid letting arbitrary caller text enter audit logs.
        request.state.trace_id = f"trc-{uuid.uuid4().hex[:16]}"
        response = await call_next(request)
        response.headers["X-Trace-ID"] = request.state.trace_id
        return response

    return app


app = create_app()

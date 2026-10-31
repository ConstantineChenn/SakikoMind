"""Transactional, local-only simulation of cancellation and refund applications.

The fixed policy below is a demonstration rule, not a merchant's real policy.
All amounts are integer cents. This service never calls a payment provider.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
from pathlib import Path
import secrets
import sqlite3
import threading
from typing import Any, Dict, Iterator, Optional
import uuid


DEMO_POLICY = {
    "policy_id": "demo-order-cancellation",
    "version": "v1",
    "effective_at": "2026-01-01T00:00:00+00:00",
    "scope": "simulated-ecommerce:paid-unshipped-orders",
    "content": (
        "演示规则：仅本人已支付且未发货订单可申请取消及全额模拟退款；"
        "必须由本人确认，金额达到10000分须由另一名审核员批准。"
        "执行时重新校验订单状态及版本。此规则不代表任何真实商家政策。"
    ),
    "approval_threshold_cents": 10000,
    "simulated": True,
}


class AfterSalesError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str = "customer"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _text(value: Any, field: str, limit: int = 128, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise AfterSalesError("invalid_input", f"{field}必须为不超过{limit}字符的字符串", 422)
    normalized = value.strip()
    if not normalized and not empty:
        raise AfterSalesError("invalid_input", f"{field}不能为空", 422)
    return normalized


def _principal(principal: Principal) -> Principal:
    if not isinstance(principal, Principal):
        raise AfterSalesError("invalid_principal", "需要有效的认证身份", 401)
    user_id = _text(principal.user_id, "user_id")
    if (user_id != principal.user_id or not isinstance(principal.role, str)
            or principal.role not in {"customer", "reviewer"}):
        raise AfterSalesError("invalid_principal", "认证身份或角色无效", 401)
    return principal


class AfterSalesStore:
    """One SQLite file, serialized writes, and durable idempotency records.

    Authentication belongs to the caller: Principal must be built from a trusted
    session, never from model output. Instances are safe to share between threads;
    each call opens and closes its own SQLite connection. Writes on this instance
    enter a bounded wait before opening a connection, reducing SQLite busy-loop
    contention. Other instances/processes still coordinate through SQLite's
    BEGIN IMMEDIATE; the instance lock is not a distributed lock or a FIFO queue.
    """

    def __init__(self, db_path: str, quote_ttl_s: int = 300, *,
                 serialize_writes: bool = True, write_queue_timeout_s: float = 15.0):
        if not isinstance(db_path, str) or not db_path.strip() or db_path == ":memory:":
            raise AfterSalesError("invalid_input", "db_path必须为持久化SQLite文件路径", 422)
        if type(quote_ttl_s) is not int or quote_ttl_s < 1 or quote_ttl_s > 86400:
            raise AfterSalesError("invalid_input", "quote_ttl_s必须为1到86400之间的整数", 422)
        if type(serialize_writes) is not bool:
            raise AfterSalesError("invalid_input", "serialize_writes必须为布尔值", 422)
        if (type(write_queue_timeout_s) not in (int, float)
                or not 0 < write_queue_timeout_s <= threading.TIMEOUT_MAX
                or not math.isfinite(write_queue_timeout_s)):
            raise AfterSalesError("invalid_input", "write_queue_timeout_s必须为有限正数且在平台超时范围内", 422)
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.quote_ttl_s = quote_ttl_s
        self.serialize_writes = serialize_writes
        self.write_queue_timeout_s = float(write_queue_timeout_s)
        self._write_lock = threading.Lock()
        self._initialize()

    @contextmanager
    def _connection(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        admitted = False
        connection = None
        try:
            if write and self.serialize_writes:
                admitted = self._write_lock.acquire(timeout=self.write_queue_timeout_s)
                if not admitted:
                    raise AfterSalesError("write_queue_timeout", "写入排队超时，请查询状态后重试", 503)
            # Queue waiting and SQLite's external-writer wait have separate
            # budgets. Keep the existing 15-second SQLite timeout unchanged.
            connection = sqlite3.connect(str(self.db_path), timeout=15, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            try:
                if connection is not None:
                    connection.close()
            finally:
                if admitted:
                    self._write_lock.release()

    def _initialize(self) -> None:
        with self._connection(write=True) as connection:
            statements = [
                """CREATE TABLE IF NOT EXISTS aftersales_orders (
                    order_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('paid','shipped','cancelled')),
                    amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
                    version INTEGER NOT NULL CHECK(version >= 1), updated_at TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS aftersales_policies (
                    policy_id TEXT NOT NULL, version TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL, PRIMARY KEY(policy_id, version)
                )""",
                """CREATE TRIGGER IF NOT EXISTS aftersales_policy_no_update
                    BEFORE UPDATE ON aftersales_policies BEGIN
                    SELECT RAISE(ABORT, 'policy versions are immutable'); END""",
                """CREATE TRIGGER IF NOT EXISTS aftersales_policy_no_delete
                    BEFORE DELETE ON aftersales_policies BEGIN
                    SELECT RAISE(ABORT, 'policy versions are immutable'); END""",
                """CREATE TABLE IF NOT EXISTS aftersales_quotes (
                    quote_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    order_id TEXT NOT NULL REFERENCES aftersales_orders(order_id),
                    order_version INTEGER NOT NULL, amount_cents INTEGER NOT NULL,
                    reason TEXT NOT NULL, policy_json TEXT NOT NULL,
                    approval_required INTEGER NOT NULL CHECK(approval_required IN (0,1)),
                    token_hash TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS aftersales_applications (
                    application_id TEXT PRIMARY KEY,
                    quote_id TEXT NOT NULL REFERENCES aftersales_quotes(quote_id),
                    order_id TEXT NOT NULL UNIQUE REFERENCES aftersales_orders(order_id),
                    user_id TEXT NOT NULL, order_version INTEGER NOT NULL,
                    amount_cents INTEGER NOT NULL CHECK(amount_cents > 0), reason TEXT NOT NULL,
                    policy_json TEXT NOT NULL,
                    approval_required INTEGER NOT NULL CHECK(approval_required IN (0,1)),
                    status TEXT NOT NULL CHECK(status IN
                        ('ready','pending_approval','succeeded','rejected','stale')),
                    confirmed_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS aftersales_idempotency (
                    user_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                    quote_id TEXT NOT NULL REFERENCES aftersales_quotes(quote_id),
                    token_hash TEXT NOT NULL,
                    application_id TEXT NOT NULL REFERENCES aftersales_applications(application_id),
                    created_at TEXT NOT NULL, PRIMARY KEY(user_id, idempotency_key)
                )""",
                """CREATE TABLE IF NOT EXISTS aftersales_approvals (
                    approval_id TEXT PRIMARY KEY,
                    application_id TEXT NOT NULL UNIQUE
                        REFERENCES aftersales_applications(application_id),
                    reviewer_user_id TEXT NOT NULL, approved INTEGER NOT NULL CHECK(approved IN (0,1)),
                    note TEXT NOT NULL, created_at TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS aftersales_operations (
                    operation_id TEXT PRIMARY KEY,
                    application_id TEXT NOT NULL UNIQUE
                        REFERENCES aftersales_applications(application_id),
                    order_id TEXT NOT NULL UNIQUE REFERENCES aftersales_orders(order_id),
                    user_id TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
                    status TEXT NOT NULL CHECK(status = 'succeeded'), created_at TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS aftersales_audit (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    application_id TEXT, quote_id TEXT NOT NULL,
                    actor TEXT NOT NULL, actor_role TEXT NOT NULL, action TEXT NOT NULL,
                    from_status TEXT, to_status TEXT NOT NULL, policy_version TEXT NOT NULL,
                    trace_id TEXT NOT NULL, time TEXT NOT NULL, metadata_json TEXT NOT NULL
                )""",
                """CREATE INDEX IF NOT EXISTS aftersales_audit_application
                    ON aftersales_audit(application_id, event_id)""",
            ]
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                "INSERT OR IGNORE INTO aftersales_policies VALUES (?, ?, ?)",
                (DEMO_POLICY["policy_id"], DEMO_POLICY["version"], json.dumps(DEMO_POLICY, ensure_ascii=False)),
            )

    def seed_demo(self) -> Dict[str, Any]:
        """Add missing demo records without resetting any existing business state."""
        with self._connection(write=True) as connection:
            for order_id, user_id, status, amount in [
                ("DEMO-1001", "alice", "paid", 4900),
                ("DEMO-1002", "alice", "paid", 29900),
                ("DEMO-2001", "bob", "paid", 9900),
                ("DEMO-1003", "alice", "shipped", 4900),
            ]:
                connection.execute(
                    "INSERT OR IGNORE INTO aftersales_orders VALUES (?, ?, ?, ?, 1, ?)",
                    (order_id, user_id, status, amount, _now()),
                )
            orders = connection.execute("SELECT * FROM aftersales_orders ORDER BY order_id").fetchall()
            return {"orders": [self._order_dict(row) for row in orders],
                    "policy": self._policy(connection), "simulated": True}

    @staticmethod
    def _policy(connection: sqlite3.Connection) -> Dict[str, Any]:
        row = connection.execute(
            "SELECT snapshot_json FROM aftersales_policies WHERE policy_id=? AND version=?",
            (DEMO_POLICY["policy_id"], DEMO_POLICY["version"]),
        ).fetchone()
        return json.loads(row["snapshot_json"])

    @staticmethod
    def _order_dict(row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        result["order_version"] = result["version"]
        result["simulated"] = True
        return result

    @staticmethod
    def _order(connection: sqlite3.Connection, principal: Principal, order_id: str,
               owner_only: bool = False) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM aftersales_orders WHERE order_id=?", (order_id,)).fetchone()
        if row is None or ((owner_only or principal.role == "customer") and row["user_id"] != principal.user_id):
            raise AfterSalesError("not_found", "订单不存在", 404)
        return row

    @staticmethod
    def _application(connection: sqlite3.Connection, principal: Principal,
                     application_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM aftersales_applications WHERE application_id=?", (application_id,)
        ).fetchone()
        if row is None or (principal.role == "customer" and row["user_id"] != principal.user_id):
            raise AfterSalesError("not_found", "售后申请不存在", 404)
        return row

    @staticmethod
    def _application_dict(connection: sqlite3.Connection, row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        result["policy"] = json.loads(result.pop("policy_json"))
        result["approval_required"] = bool(result["approval_required"])
        approval = connection.execute(
            "SELECT * FROM aftersales_approvals WHERE application_id=?", (row["application_id"],)
        ).fetchone()
        result["approval"] = dict(approval) if approval else None
        if result["approval"]:
            result["approval"]["approved"] = bool(result["approval"]["approved"])
        operation = connection.execute(
            "SELECT * FROM aftersales_operations WHERE application_id=?", (row["application_id"],)
        ).fetchone()
        result["operation"] = dict(operation) if operation else None
        if result["operation"]:
            result["operation"]["simulated"] = True
        result["simulated"] = True
        return result

    @staticmethod
    def _event(connection: sqlite3.Connection, principal: Principal, action: str,
               quote_id: str, policy_version: str, trace_id: str, from_status: Optional[str],
               to_status: str, application_id: Optional[str] = None,
               metadata: Optional[Dict[str, Any]] = None) -> None:
        connection.execute(
            """INSERT INTO aftersales_audit
               (application_id,quote_id,actor,actor_role,action,from_status,to_status,
                policy_version,trace_id,time,metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (application_id, quote_id, principal.user_id, principal.role, action, from_status,
             to_status, policy_version, trace_id, _now(), json.dumps(metadata or {}, ensure_ascii=False)),
        )

    def get_order(self, principal: Principal, order_id: str) -> Dict[str, Any]:
        principal = _principal(principal)
        order_id = _text(order_id, "order_id")
        with self._connection() as connection:
            return self._order_dict(self._order(connection, principal, order_id))

    def prepare(self, principal: Principal, order_id: str, reason: str,
                trace_id: str = "") -> Dict[str, Any]:
        principal = _principal(principal)
        order_id = _text(order_id, "order_id")
        reason = _text(reason, "reason", 500)
        trace_id = _text(trace_id, "trace_id", empty=True)
        with self._connection(write=True) as connection:
            order = self._order(connection, principal, order_id, owner_only=True)
            if order["status"] != "paid":
                raise AfterSalesError("order_not_cancellable", "仅已支付且未发货的订单可申请取消")
            policy = self._policy(connection)
            quote_id = "quote_" + uuid.uuid4().hex
            token = secrets.token_urlsafe(32)
            created_at = _now()
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=self.quote_ttl_s)).isoformat(timespec="microseconds")
            approval_required = order["amount_cents"] >= policy["approval_threshold_cents"]
            connection.execute(
                "INSERT INTO aftersales_quotes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (quote_id, principal.user_id, order_id, order["version"], order["amount_cents"], reason,
                 json.dumps(policy, ensure_ascii=False), int(approval_required),
                 hashlib.sha256(token.encode()).hexdigest(), created_at, expires_at),
            )
            self._event(connection, principal, "prepare", quote_id, policy["version"], trace_id,
                        None, "prepared", metadata={"order_id": order_id, "order_version": order["version"]})
            return {"quote_id": quote_id, "confirmation_token": token, "expires_at": expires_at,
                    "order_id": order_id, "order_version": order["version"], "amount_cents": order["amount_cents"],
                    "reason": reason, "policy": policy, "approval_required": approval_required,
                    "simulated": True}

    def confirm(self, principal: Principal, quote_id: str, confirmation_token: str,
                idempotency_key: str, trace_id: str = "") -> Dict[str, Any]:
        principal = _principal(principal)
        quote_id = _text(quote_id, "quote_id")
        confirmation_token = _text(confirmation_token, "confirmation_token")
        idempotency_key = _text(idempotency_key, "idempotency_key")
        trace_id = _text(trace_id, "trace_id", empty=True)
        token_hash = hashlib.sha256(confirmation_token.encode()).hexdigest()
        with self._connection(write=True) as connection:
            previous = connection.execute(
                "SELECT * FROM aftersales_idempotency WHERE user_id=? AND idempotency_key=?",
                (principal.user_id, idempotency_key),
            ).fetchone()
            if previous:
                if previous["quote_id"] != quote_id or not hmac.compare_digest(previous["token_hash"], token_hash):
                    raise AfterSalesError("idempotency_conflict", "同一幂等键不能用于不同的确认参数")
                return self._application_dict(connection, self._application(connection, principal, previous["application_id"]))
            quote = connection.execute("SELECT * FROM aftersales_quotes WHERE quote_id=?", (quote_id,)).fetchone()
            if quote is None or quote["user_id"] != principal.user_id:
                raise AfterSalesError("not_found", "确认凭据不存在", 404)
            if not hmac.compare_digest(quote["token_hash"], token_hash):
                raise AfterSalesError("invalid_confirmation", "确认凭据无效", 403)
            if datetime.fromisoformat(quote["expires_at"]) <= datetime.now(timezone.utc):
                raise AfterSalesError("quote_expired", "确认凭据已过期，请重新获取方案")
            existing = connection.execute(
                "SELECT * FROM aftersales_applications WHERE order_id=?", (quote["order_id"],)
            ).fetchone()
            if existing:
                snapshot_fields = (
                    "order_version", "amount_cents", "reason", "policy_json", "approval_required",
                )
                if any(quote[field] != existing[field] for field in snapshot_fields):
                    raise AfterSalesError(
                        "application_conflict", "订单已有参数不同的售后申请，请查询原申请或联系人工处理",
                    )
                # An alias is durable too: reusing this key with another quote conflicts.
                connection.execute("INSERT INTO aftersales_idempotency VALUES (?,?,?,?,?,?)",
                                   (principal.user_id, idempotency_key, quote_id, token_hash,
                                    existing["application_id"], _now()))
                self._event(connection, principal, "confirm_duplicate", quote_id,
                            json.loads(quote["policy_json"])["version"], trace_id,
                            existing["status"], existing["status"], existing["application_id"])
                return self._application_dict(connection, existing)
            order = self._order(connection, principal, quote["order_id"], owner_only=True)
            if order["status"] != "paid" or order["version"] != quote["order_version"]:
                raise AfterSalesError("order_changed", "订单状态或版本已变化，请重新获取方案")
            if order["amount_cents"] != quote["amount_cents"]:
                raise AfterSalesError("order_changed", "订单金额已变化，请重新获取方案")
            application_id = "sale_" + uuid.uuid4().hex
            status = "pending_approval" if quote["approval_required"] else "ready"
            timestamp = _now()
            connection.execute(
                "INSERT INTO aftersales_applications VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (application_id, quote_id, quote["order_id"], principal.user_id, quote["order_version"],
                 quote["amount_cents"], quote["reason"], quote["policy_json"], quote["approval_required"],
                 status, timestamp, timestamp, timestamp),
            )
            connection.execute("INSERT INTO aftersales_idempotency VALUES (?,?,?,?,?,?)",
                               (principal.user_id, idempotency_key, quote_id, token_hash, application_id, timestamp))
            self._event(connection, principal, "confirm", quote_id,
                        json.loads(quote["policy_json"])["version"], trace_id, "prepared", status, application_id)
            return self._application_dict(connection, self._application(connection, principal, application_id))

    def get_application(self, principal: Principal, application_id: str) -> Dict[str, Any]:
        principal = _principal(principal)
        application_id = _text(application_id, "application_id")
        with self._connection() as connection:
            return self._application_dict(connection, self._application(connection, principal, application_id))

    def review(self, principal: Principal, application_id: str, approved: bool,
               note: str, trace_id: str = "") -> Dict[str, Any]:
        principal = _principal(principal)
        application_id = _text(application_id, "application_id")
        note = _text(note, "note", 1000, empty=True)
        trace_id = _text(trace_id, "trace_id", empty=True)
        if type(approved) is not bool:
            raise AfterSalesError("invalid_input", "approved必须为布尔值", 422)
        if principal.role != "reviewer":
            raise AfterSalesError("reviewer_required", "此操作需要审核员身份", 403)
        with self._connection(write=True) as connection:
            application = self._application(connection, principal, application_id)
            if application["user_id"] == principal.user_id:
                raise AfterSalesError("self_review_forbidden", "审核员不能审批自己的申请", 403)
            previous = connection.execute("SELECT * FROM aftersales_approvals WHERE application_id=?", (application_id,)).fetchone()
            if previous:
                if bool(previous["approved"]) != approved:
                    raise AfterSalesError("review_conflict", "该申请已有不同的审批决定")
                return self._application_dict(connection, application)
            if application["status"] != "pending_approval":
                raise AfterSalesError("invalid_application_state", "当前申请不等待审批")
            timestamp = _now()
            status = "ready" if approved else "rejected"
            connection.execute("INSERT INTO aftersales_approvals VALUES (?,?,?,?,?,?)",
                               ("review_" + uuid.uuid4().hex, application_id, principal.user_id, int(approved), note, timestamp))
            connection.execute("UPDATE aftersales_applications SET status=?,updated_at=? WHERE application_id=?",
                               (status, timestamp, application_id))
            self._event(connection, principal, "approve" if approved else "reject", application["quote_id"],
                        json.loads(application["policy_json"])["version"], trace_id,
                        "pending_approval", status, application_id)
            return self._application_dict(connection, self._application(connection, principal, application_id))

    def execute(self, principal: Principal, application_id: str, trace_id: str = "",
                fault: Optional[str] = None) -> Dict[str, Any]:
        principal = _principal(principal)
        application_id = _text(application_id, "application_id")
        trace_id = _text(trace_id, "trace_id", empty=True)
        if fault is not None and (not isinstance(fault, str) or fault not in {"before_commit", "after_commit"}):
            raise AfterSalesError("invalid_input", "未知故障注入模式", 422)
        failure = None
        result = None
        with self._connection(write=True) as connection:
            application = self._application(connection, principal, application_id)
            if application["status"] == "succeeded":
                return self._application_dict(connection, application)
            if application["status"] == "pending_approval":
                raise AfterSalesError("approval_required", "申请须经独立审核员批准后才能执行")
            if application["status"] != "ready":
                raise AfterSalesError("invalid_application_state", "当前申请不可执行")
            policy = json.loads(application["policy_json"])
            if application["approval_required"] or application["amount_cents"] >= policy["approval_threshold_cents"]:
                approval = connection.execute("SELECT * FROM aftersales_approvals WHERE application_id=?", (application_id,)).fetchone()
                if not approval or not approval["approved"] or approval["reviewer_user_id"] == application["user_id"]:
                    raise AfterSalesError("approval_required", "缺少有效的独立审批记录")
            order = self._order(connection, principal, application["order_id"])
            timestamp = _now()
            if (order["status"] != "paid" or order["version"] != application["order_version"]
                    or order["amount_cents"] != application["amount_cents"]):
                connection.execute("UPDATE aftersales_applications SET status='stale',updated_at=? WHERE application_id=?",
                                   (timestamp, application_id))
                self._event(connection, principal, "execution_stale", application["quote_id"], policy["version"],
                            trace_id, "ready", "stale", application_id,
                            {"order_status": order["status"], "order_version": order["version"]})
                failure = AfterSalesError("order_changed", "执行前订单状态或版本已变化，申请已停止")
            else:
                operation_id = "simrefund_" + uuid.uuid4().hex
                connection.execute("INSERT INTO aftersales_operations VALUES (?,?,?,?,?,'succeeded',?)",
                                   (operation_id, application_id, application["order_id"], application["user_id"],
                                    application["amount_cents"], timestamp))
                connection.execute("UPDATE aftersales_orders SET status='cancelled',version=version+1,updated_at=? WHERE order_id=?",
                                   (timestamp, application["order_id"]))
                connection.execute("UPDATE aftersales_applications SET status='succeeded',updated_at=? WHERE application_id=?",
                                   (timestamp, application_id))
                self._event(connection, principal, "execute", application["quote_id"], policy["version"],
                            trace_id, "ready", "succeeded", application_id,
                            {"operation_id": operation_id, "order_version": order["version"] + 1, "simulated": True})
                if fault == "before_commit":
                    raise TimeoutError("模拟提交前超时：事务已回滚，可查询状态后重试")
                result = self._application_dict(connection, self._application(connection, principal, application_id))
        if failure:
            raise failure
        if fault == "after_commit":
            raise TimeoutError("模拟提交后响应丢失：执行结果未知，请查询申请状态")
        return result

    def audit(self, principal: Principal, application_id: str) -> Dict[str, Any]:
        principal = _principal(principal)
        application_id = _text(application_id, "application_id")
        with self._connection() as connection:
            application = self._application(connection, principal, application_id)
            rows = connection.execute(
                "SELECT * FROM aftersales_audit WHERE application_id=? OR quote_id=? ORDER BY event_id",
                (application_id, application["quote_id"]),
            ).fetchall()
            events = []
            for row in rows:
                event = dict(row)
                event["metadata"] = json.loads(event.pop("metadata_json"))
                events.append(event)
            return {"application_id": application_id, "events": events, "simulated": True}

    def set_order_status_for_test(self, order_id: str, status: str) -> Dict[str, Any]:
        """Python-only fault fixture. Never expose through an HTTP or agent tool."""
        order_id = _text(order_id, "order_id")
        status = _text(status, "status")
        if status not in {"paid", "shipped", "cancelled"}:
            raise AfterSalesError("invalid_input", "不支持的模拟订单状态", 422)
        with self._connection(write=True) as connection:
            cursor = connection.execute("UPDATE aftersales_orders SET status=?,version=version+1,updated_at=? WHERE order_id=?",
                                        (status, _now(), order_id))
            if cursor.rowcount == 0:
                raise AfterSalesError("not_found", "订单不存在", 404)
            row = connection.execute("SELECT * FROM aftersales_orders WHERE order_id=?", (order_id,)).fetchone()
            return self._order_dict(row)

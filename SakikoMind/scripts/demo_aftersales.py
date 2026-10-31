"""Demonstrate a persisted simulated refund with approval and timeout recovery.

Run from any working directory. This script uses no model, network or real funds.
"""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory


SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from core.aftersales_store import AfterSalesError, AfterSalesStore, Principal


def require(condition: bool, description: str) -> None:
    if not condition:
        raise RuntimeError(f"演示结果不符合预期：{description}")


def public_evidence(value):
    """Do not put usable confirmation credentials in the evidence report."""
    if isinstance(value, dict):
        return {
            key: public_evidence(item)
            for key, item in value.items()
            if key not in {"confirmation_token", "token_hash"}
        }
    if isinstance(value, list):
        return [public_evidence(item) for item in value]
    return value


def run_demo(db_path: Path, retained: bool) -> dict:
    store = AfterSalesStore(str(db_path))
    store.seed_demo()
    customer = Principal("alice")
    reviewer = Principal("reviewer", "reviewer")
    order_id = "DEMO-1002"
    evidence = []

    def record(step: str, data: dict) -> None:
        evidence.append({"step": step, "data": public_evidence(data)})

    initial_order = store.get_order(customer, order_id)
    record("initial_order", initial_order)
    require(initial_order["status"] == "paid", "初始订单必须未发货且已支付")

    quote = store.prepare(customer, order_id, "不再需要", trace_id="demo-prepare")
    record("prepare", quote)
    require(quote["approval_required"], "主链路应触发人工审批")

    application = store.confirm(
        customer,
        quote["quote_id"],
        quote["confirmation_token"],
        "demo-confirm-1002",
        trace_id="demo-confirm",
    )
    record("confirm", application)
    application_id = application["application_id"]
    repeated_confirmation = store.confirm(
        customer,
        quote["quote_id"],
        quote["confirmation_token"],
        "demo-confirm-1002",
        trace_id="demo-confirm-retry",
    )
    record("confirm_retry", repeated_confirmation)
    require(
        repeated_confirmation["application_id"] == application_id,
        "重复确认必须返回同一申请",
    )

    try:
        store.execute(customer, application_id, trace_id="demo-before-approval")
    except AfterSalesError as error:
        require(error.code == "approval_required", "未审批执行应因缺少审批被拒绝")
        record("execution_blocked_before_approval", {"code": error.code, "message": str(error)})
    else:
        raise RuntimeError("未审批申请被执行")
    before_approval = store.get_application(customer, application_id)
    require(before_approval["operation"] is None, "审批前不应产生模拟退款")
    require(store.get_order(customer, order_id)["status"] == "paid", "审批前订单不应改变")

    approved = store.review(
        reviewer, application_id, True, "模拟审批", trace_id="demo-approve"
    )
    record("review", approved)
    require(approved["status"] == "ready", "有效审批后申请应可执行")

    try:
        store.execute(
            customer, application_id, trace_id="demo-execute", fault="after_commit"
        )
    except TimeoutError as error:
        record(
            "response_timeout_after_commit",
            {"exception": type(error).__name__, "message": str(error), "caller_outcome": "unknown"},
        )
    else:
        raise RuntimeError("未观察到预期的提交后响应超时")

    # Reopen the database: recovery must come from persisted state, not memory.
    recovered_store = AfterSalesStore(str(db_path))
    recovered = recovered_store.get_application(customer, application_id)
    record("query_recovered_application", recovered)
    operation = recovered["operation"]
    require(recovered["status"] == "succeeded", "提交后的执行状态必须持久化")
    require(operation is not None and operation["status"] == "succeeded", "应能查询到已完成操作")
    require(
        operation["user_id"] == "alice"
        and operation["order_id"] == order_id
        and operation["amount_cents"] == initial_order["amount_cents"]
        and operation["simulated"] is True,
        "操作必须属于正确用户、订单、金额且为模拟结果",
    )

    retried = recovered_store.execute(customer, application_id, trace_id="demo-execute-retry")
    record("execute_retry", retried)
    require(retried["operation"]["operation_id"] == operation["operation_id"], "重试必须返回原操作")

    final_order = recovered_store.get_order(customer, order_id)
    record("final_order", final_order)
    require(final_order["status"] == "cancelled", "最终订单应取消")
    require(final_order["version"] == initial_order["version"] + 1, "执行重试不能重复更新订单版本")
    audit = recovered_store.audit(customer, application_id)
    record("audit", audit)
    execution_events = [event for event in audit["events"] if event["action"] == "execute"]
    require(len(execution_events) == 1, "审计中应仅有一次执行事实")
    require(
        execution_events[0]["metadata"]["operation_id"] == operation["operation_id"],
        "审计应能关联持久化操作",
    )

    with closing(sqlite3.connect(str(db_path))) as connection:
        application_count = connection.execute(
            "SELECT COUNT(*) FROM aftersales_applications WHERE order_id=?", (order_id,)
        ).fetchone()[0]
        operation_count = connection.execute(
            "SELECT COUNT(*) FROM aftersales_operations WHERE order_id=?", (order_id,)
        ).fetchone()[0]
    require(application_count == 1 and operation_count == 1, "数据库必须只有一条申请和一笔模拟操作")
    record("database_counts", {"applications_for_order": application_count, "operations_for_order": operation_count})

    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "simulated": True,
        "model_calls": 0,
        "scenario": "approved_order_cancellation_with_post_commit_timeout",
        "database": {"path": str(db_path), "retained_after_exit": retained},
        "checks": {
            "confirmation_retry_reuses_application": True,
            "execution_without_approval_blocked": True,
            "post_commit_timeout_observed": True,
            "result_recovered_from_reopened_database": True,
            "execution_retry_reuses_operation": True,
            "single_application_and_simulated_operation": True,
            "correct_order_owner_amount_and_final_state": True,
        },
        "evidence": evidence,
    }


def main() -> int:
    # JSON reports use UTF-8 on Windows too, including when stdout is redirected.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="演示售后审批、幂等和提交后超时恢复；不调用模型或真实资金")
    parser.add_argument("--db", type=Path, help="保留结果的新 SQLite 路径；已有非空文件会被拒绝")
    parser.add_argument("--output", type=Path, help="将 JSON 证据另存到此文件；也会输出到标准输出")
    args = parser.parse_args()
    db_path = args.db.resolve() if args.db else None
    output = args.output.resolve() if args.output else None
    if db_path and db_path.exists() and (not db_path.is_file() or db_path.stat().st_size > 0):
        parser.error("--db 必须指向新文件或空文件，请使用新的数据库路径")
    if db_path and output == db_path:
        parser.error("--output 不能与 --db 使用同一路径")
    if db_path:
        report = run_demo(db_path, retained=True)
    else:
        with TemporaryDirectory(prefix="sakikomind-aftersales-") as directory:
            report = run_demo(Path(directory) / "orders.db", retained=False)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

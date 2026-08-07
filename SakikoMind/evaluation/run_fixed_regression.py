"""通过真实 /chat 主链路运行固定标注集并保存机器可读报告。"""
import argparse
import json
import pathlib
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List


def run_case(base_url: str, case: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    payload = json.dumps(
        {
            "message": case["message"],
            "user_id": "fixed-eval-user",
            "conv_id": f"fixed-{case['id'].lower()}-{time.time_ns()}",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    started_at = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            actual = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        return {
            "id": case["id"],
            "passed": False,
            "latency_ms": round((time.perf_counter() - started_at) * 1000, 1),
            "failures": [f"请求失败：{error}"],
        }

    failures: List[str] = []
    _expect_in(failures, "intent", actual.get("intent"), case["expected_intents"])
    _expect_in(failures, "agent_type", actual.get("agent_type"), case["expected_agents"])
    _expect_equal(failures, "escalated", bool(actual.get("escalated")), case["should_escalate"])

    ticket = actual.get("handoff_ticket") or {}
    if case.get("expected_reason"):
        _expect_equal(failures, "handoff_reason", ticket.get("reason"), case["expected_reason"])
    if case.get("expected_priority"):
        _expect_equal(failures, "handoff_priority", ticket.get("priority"), case["expected_priority"])

    actual_skills = {item.get("name") for item in actual.get("skills_used", [])}
    missing_skills = sorted(set(case.get("expected_skills", [])) - actual_skills)
    if missing_skills:
        failures.append(f"缺少 Skills：{missing_skills}")

    actual_sources = {item.get("source_id") for item in actual.get("citations", [])}
    expected_sources = set(case.get("expected_sources", []))
    if expected_sources and not actual_sources.intersection(expected_sources):
        failures.append(
            f"引用未命中预期来源：期望任一 {sorted(expected_sources)}，实际 {sorted(actual_sources)}"
        )

    return {
        "id": case["id"],
        "passed": not failures,
        "latency_ms": round((time.perf_counter() - started_at) * 1000, 1),
        "ticket_id": ticket.get("ticket_id"),
        "failures": failures,
        "actual": {
            "intent": actual.get("intent"),
            "agent_type": actual.get("agent_type"),
            "escalated": bool(actual.get("escalated")),
            "handoff_reason": ticket.get("reason"),
            "handoff_priority": ticket.get("priority"),
            "skills": sorted(name for name in actual_skills if name),
            "sources": sorted(source for source in actual_sources if source),
        },
    }


def close_test_ticket(base_url: str, ticket_id: str, timeout: int) -> None:
    """关闭固定评测产生的工单，保留审计记录但不污染人工待办。"""
    payload = json.dumps({"status": "closed"}).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/handoffs/{ticket_id}",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout):
            return
    except urllib.error.URLError:
        return


def _expect_in(failures: List[str], field: str, actual: Any, expected: List[Any]) -> None:
    if actual not in expected:
        failures.append(f"{field} 不符合预期：实际 {actual!r}，允许值 {expected!r}")


def _expect_equal(failures: List[str], field: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        failures.append(f"{field} 不符合预期：实际 {actual!r}，期望 {expected!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 SakikoMind 固定回归评测集")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--dataset", default="/app/data/eval/fixed_cases_20.json")
    parser.add_argument("--output-dir", default="/app/data/eval/fixed-reports")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    dataset = json.loads(pathlib.Path(args.dataset).read_text(encoding="utf-8"))
    cases = dataset["cases"][: args.limit] if args.limit else dataset["cases"]
    started_at = time.perf_counter()
    worker_count = max(1, min(args.workers, 4))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = []
        for result in executor.map(
            lambda case: run_case(args.base_url, case, args.timeout),
            cases,
        ):
            ticket_id = result.pop("ticket_id", None)
            if ticket_id:
                close_test_ticket(args.base_url, ticket_id, args.timeout)
            results.append(result)
            print(
                json.dumps(
                    {
                        "case": result["id"],
                        "passed": result["passed"],
                        "completed": len(results),
                        "total": len(cases),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    passed = sum(1 for result in results if result["passed"])
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "dataset": dataset["dataset"],
        "dataset_version": dataset["version"],
        "timestamp": timestamp,
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round(passed / len(results), 4) if results else 0.0,
        "duration_ms": round((time.perf_counter() - started_at) * 1000, 1),
        "results": results,
    }
    output_dir = pathlib.Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"fixed-{timestamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "total": report["total"],
                "passed": report["passed"],
                "failed": report["failed"],
                "pass_rate": report["pass_rate"],
                "duration_ms": report["duration_ms"],
                "report_path": str(report_path),
            },
            ensure_ascii=False,
        )
    )
    return 1 if args.strict and report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

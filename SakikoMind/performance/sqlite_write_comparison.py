"""Paired, loopback HTTP comparison of SQLite write admission, without models.

Both arms run the same frozen production source, fixtures and workload. Only
AfterSalesStore.serialize_writes changes. This is not the historical 14.83% run.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager
import contextvars
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import shutil
import socket
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
import urllib.request

USER = "comparison-customer"
TOKEN = "synthetic-loopback-comparison-only"
REQUEST = contextvars.ContextVar("comparison_request", default="startup")
SOURCES = ("api/aftersales.py", "core/aftersales_store.py", "core/aftersales_planner.py")


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def serve(args):
    sys.path.insert(0, str(args.source_root.resolve()))
    from core.aftersales_store import AfterSalesStore
    from api.aftersales import create_app
    from fastapi import Request
    import uvicorn
    import anyio.to_thread

    log_lock = threading.Lock()

    class ObservedStore(AfterSalesStore):
        @contextmanager
        def _connection(self, write=False):
            start = time.perf_counter()
            entered = None
            error = None
            try:
                with super()._connection(write=write) as connection:
                    entered = time.perf_counter()
                    yield connection
            except BaseException as exc:
                error = getattr(exc, "code", type(exc).__name__)
                raise
            finally:
                end = time.perf_counter()
                row = {"request_id": REQUEST.get(), "write": write, "error": error,
                       "entry_ms": ((entered or end) - start) * 1000,
                       "total_ms": (end - start) * 1000}
                with log_lock, args.connection_log.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row) + "\n")

    store = ObservedStore(str(args.database), quote_ttl_s=86400,
                          serialize_writes=args.arm == "queued", write_queue_timeout_s=15)
    app = create_app(store=store, tokens={TOKEN: {"user_id": USER, "role": "customer"}}, planner=None)

    @app.middleware("http")
    async def observation(request: Request, call_next):
        marker = REQUEST.set(request.headers.get("X-Comparison-Request", "startup"))
        try:
            return await call_next(request)
        finally:
            REQUEST.reset(marker)

    @app.get("/_comparison/ready")
    async def ready():
        # Set the same explicitly declared thread capacity in both arms.
        anyio.to_thread.current_default_thread_limiter().total_tokens = args.server_threads
        return {"pid": os.getpid(), "arm": args.arm, "server_threads": args.server_threads,
                "cpu_seconds": time.process_time()}

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, workers=1,
                                          access_log=False, log_level="warning"))

    @app.post("/_comparison/stop")
    async def stop():
        server.should_exit = True
        return {"stopping": True, "pid": os.getpid()}

    server.run()


def fixture(source, output, count):
    sys.path.insert(0, str(source))
    from core.aftersales_store import AfterSalesStore, Principal
    db = output / "fixture.db"
    store = AfterSalesStore(str(db), quote_ttl_s=86400)
    with sqlite3.connect(db) as connection:
        connection.executemany("INSERT INTO aftersales_orders VALUES (?,?,'paid',4900,1,?)",
                               [(f"CMP-{i:06d}", USER, "2026-01-01T00:00:00+00:00") for i in range(count)])
    requests = []
    for i in range(count):
        order_id = f"CMP-{i:06d}"
        quote = store.prepare(Principal(USER), order_id, "synthetic comparison", "fixture")
        requests.append({"quote_id": quote["quote_id"], "confirmation_token": quote["confirmation_token"],
                         "idempotency_key": f"comparison-{i:06d}", "confirmed": True})
    return db, requests


def state_oracle(db, rows):
    with sqlite3.connect(db) as connection:
        connection.row_factory = sqlite3.Row
        orders = {r["order_id"]: dict(r) for r in connection.execute("SELECT * FROM aftersales_orders")}
        applications = {}
        for row in connection.execute("SELECT * FROM aftersales_applications"):
            applications.setdefault(row["order_id"], []).append(dict(row))
        idempotency = {}
        for row in connection.execute("SELECT * FROM aftersales_idempotency"):
            idempotency.setdefault(row["application_id"], []).append(dict(row))
        audits = {}
        for row in connection.execute("SELECT * FROM aftersales_audit WHERE action='confirm'"):
            audits.setdefault(row["application_id"], []).append(dict(row))
        operation_count = connection.execute("SELECT COUNT(*) FROM aftersales_operations").fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    findings = []
    for row in rows:
        order = orders[row["order_id"]]
        apps = applications.get(row["order_id"], [])
        valid = (order["status"] == "paid" and order["amount_cents"] == 4900 and order["user_id"] == USER
                 and len(apps) <= 1 and all(a["status"] == "ready" and a["amount_cents"] == 4900
                                           and a["user_id"] == USER for a in apps))
        if row["success"]:
            valid = valid and len(apps) == 1 and apps[0]["application_id"] == row["application_id"]
        for application in apps:
            app_id = application["application_id"]
            keys, events = idempotency.get(app_id, []), audits.get(app_id, [])
            valid = valid and (len(keys) == len(events) == 1
                and keys[0]["user_id"] == USER and keys[0]["quote_id"] == application["quote_id"]
                and keys[0]["idempotency_key"] == "comparison-" + row["order_id"].split("-")[1]
                and events[0]["actor"] == USER and events[0]["to_status"] == "ready")
        if row["http_status"] == 503 and row["error"] == "write_queue_timeout":
            valid = valid and not apps
        findings.append({"request_id": row["request_id"], "valid": bool(valid),
                         "application_count": len(apps),
                         "failed_response_but_committed": not row["success"] and bool(apps)})
    return {"valid": integrity == "ok" and not foreign_keys and not operation_count and all(r["valid"] for r in findings),
            "integrity_check": integrity, "foreign_key_violations": len(foreign_keys),
            "refund_operations": operation_count, "requests": findings}


async def run_arm(args, source, template, payloads, output, arm, concurrency):
    import httpx
    output.mkdir()
    db = output / "orders.db"
    shutil.copyfile(template, db)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    command = [sys.executable, "-B", str(source.parent / Path(__file__).name), "--serve", "--source-root", str(source),
               "--database", str(db), "--connection-log", str(output / "connections.jsonl"),
               "--port", str(port), "--arm", arm, "--server-threads", str(args.server_threads)]
    # Explicit fixtures bypass dotenv; do not forward provider credentials.
    environment = {k: v for k, v in os.environ.items()
                   if not any(s in k.upper() for s in ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "ANTHROPIC", "DEEPSEEK"))}
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    stdout = (output / "stdout.log").open("w", encoding="utf-8")
    stderr = (output / "stderr.log").open("w", encoding="utf-8")
    process = subprocess.Popen(command, cwd=output, env=environment, stdout=stdout, stderr=stderr,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    rows = []
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False,
                limits=httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency),
                headers={"Authorization": "Bearer " + TOKEN}, timeout=args.timeout) as client:
            deadline = time.monotonic() + 30
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Comparison server exited; inspect stderr.log")
                try:
                    ready = await client.get("/_comparison/ready", timeout=.5)
                    ready.raise_for_status()
                    break
                except httpx.HTTPError:
                    if time.monotonic() > deadline:
                        raise TimeoutError("Comparison server startup timed out")
                    await asyncio.sleep(.1)
            save(output / "server.json", {"command": command, "ready": ready.json(), "pid": process.pid})

            async def batch(offset, count, workers, phase):
                gate = asyncio.Semaphore(workers)
                started = time.perf_counter()

                async def one(local_index):
                    index = offset + local_index
                    row = {"request_id": f"{phase}-{index:06d}", "phase": phase,
                           "order_id": f"CMP-{index:06d}", "http_status": None,
                           "application_id": None, "success": False, "error": None}
                    async with gate:
                        sent = time.perf_counter()
                        row["client_queue_ms"] = (sent - started) * 1000
                        try:
                            response = await client.post("/aftersales/applications", json=payloads[index],
                                headers={"X-Comparison-Request": row["request_id"]})
                            row["http_status"] = response.status_code
                            try:
                                body = response.json()
                            except ValueError:
                                body = {}
                            if isinstance(body, dict):
                                row["application_id"] = body.get("application_id")
                                row["success"] = (response.status_code == 200 and body.get("order_id") == row["order_id"]
                                    and body.get("status") == "ready" and body.get("amount_cents") == 4900
                                    and body.get("user_id") == USER and bool(body.get("application_id")))
                            if not row["success"]:
                                detail = body.get("detail") if isinstance(body, dict) else None
                                row["error"] = detail.get("code") if isinstance(detail, dict) else f"http_{response.status_code}"
                        except httpx.HTTPError as exc:
                            row["error"] = type(exc).__name__
                        row["latency_ms"] = (time.perf_counter() - sent) * 1000
                        row["end_to_end_ms"] = (time.perf_counter() - started) * 1000
                        rows.append(row)
                        with (output / "requests.jsonl").open("a", encoding="utf-8") as stream:
                            stream.write(json.dumps(row) + "\n")
                await asyncio.gather(*(one(i) for i in range(count)))
                return time.perf_counter() - started

            await batch(0, args.warmup, 1, "warmup")
            before = (await client.get("/_comparison/ready")).json()
            elapsed = await batch(args.warmup, args.requests, concurrency, "measured")
            after = (await client.get("/_comparison/ready")).json()
            await batch(args.warmup + args.requests, 10, 1, "recovery")
            # All responses normally finish the transaction. A timed-out client
            # may leave server work running; allow the declared queue+DB bounds.
            if any(r["error"] and "Timeout" in r["error"] for r in rows):
                await asyncio.sleep(31)
    finally:
        if process.poll() is None:
            # Windows venv executables can be launchers with a separate server
            # child. Ask that server to exit, then reap the launcher as well.
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                request = urllib.request.Request(f"http://127.0.0.1:{port}/_comparison/stop", data=b"", method="POST")
                with opener.open(request, timeout=2):
                    pass
            except OSError:
                pass
            try:
                await asyncio.to_thread(process.wait, 35)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, check=False)
                else:
                    process.kill()
                await asyncio.to_thread(process.wait, 5)
        stdout.close()
        stderr.close()
        save(output / "shutdown.json", {"returncode": process.returncode, "running": process.poll() is None})
    oracle = state_oracle(db, rows)
    save(output / "oracle.json", oracle)
    measured = [r for r in rows if r["phase"] == "measured"]
    connections = [json.loads(line) for line in (output / "connections.jsonl").read_text().splitlines()]
    entries = [r for r in connections if r["request_id"].startswith("measured-") and r["write"]]
    summary = {"arm": arm, "concurrency": concurrency, "requests": len(measured),
               "errors": sum(not r["success"] for r in measured), "elapsed_s": elapsed,
               "throughput_rps": len(measured) / elapsed,
               "successful_rps": sum(r["success"] for r in measured) / elapsed,
               "p95_ms": percentile([r["latency_ms"] for r in measured], .95),
               "p99_ms": percentile([r["latency_ms"] for r in measured], .99),
               "client_queue_p95_ms": percentile([r["client_queue_ms"] for r in measured], .95),
               "end_to_end_p95_ms": percentile([r["end_to_end_ms"] for r in measured], .95),
               "connection_entry_p95_ms": percentile([r["entry_ms"] for r in entries], .95),
               "server_cpu_s": after["cpu_seconds"] - before["cpu_seconds"],
               "error_categories": dict(Counter(r["error"] for r in measured if r["error"])),
               "oracle_passed": oracle["valid"],
               "warmup_errors": sum(not r["success"] for r in rows if r["phase"] == "warmup"),
               "recovery_errors": sum(not r["success"] for r in rows if r["phase"] == "recovery"),
               "fixture_sha256": digest(template)}
    save(output / "summary.json", summary)
    return summary


async def compare(args):
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = output / "source"
    for name in SOURCES:
        destination = source / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.source_root / name, destination)
    shutil.copyfile(Path(__file__), output / Path(__file__).name)
    plan = {"kind": "new_paired_sqlite_write_admission_http_comparison", "historical_14_83_reproduced": False,
            "created_at": datetime.now(timezone.utc).isoformat(), "python": sys.version,
            "platform": platform.platform(), "sqlite_version": sqlite3.sqlite_version,
            "dependencies": {name: importlib.metadata.version(name) for name in
                             ("fastapi", "starlette", "anyio", "httpx", "uvicorn", "pydantic")},
            "concurrency": args.concurrency, "requests_per_arm": args.requests, "repeats": args.repeats,
            "warmup_requests": args.warmup, "recovery_requests": 10, "client_timeout_s": args.timeout,
            "sqlite_timeout_s": 15, "write_queue_timeout_s": 15, "server_threads": args.server_threads,
            "source_sha256": {name: digest(source / name) for name in SOURCES},
            "runner_sha256": digest(Path(__file__)), "environment_note": args.environment_note,
            "workload": "closed-loop bounded HTTP concurrency; no pacing/retries; one distinct order per confirmation",
            "order": "raw then queued in odd repetitions; queued then raw in even repetitions",
            "only_arm_difference": "serialize_writes=False versus True on identical frozen source",
            "measurement": "HTTP includes server queue; client submission queue recorded separately; entry includes connect, admission, PRAGMA and BEGIN",
            "limits": ["local client/server share a host", "one process and shared store instance",
                       "no model, Redis, Chroma or external payment", "no production SLA or maximum capacity claim",
                       "no historical error-rate comparison", "new baseline is this source with admission disabled; not a historical revision"]}
    save(output / "plan.json", plan)
    template, payloads = fixture(source, output, args.warmup + args.requests + 10)
    save(output / "fixture.json", {"sha256": digest(template), "orders": len(payloads),
                                   "quote_ttl_s": 86400, "amount_cents": 4900})
    runs = []
    for repeat in range(1, args.repeats + 1):
        for concurrency in args.concurrency:
            for arm in (("raw", "queued") if repeat % 2 else ("queued", "raw")):
                run = await run_arm(args, source, template, payloads,
                                    output / f"r{repeat}-c{concurrency}-{arm}", arm, concurrency)
                run["repeat"] = repeat
                runs.append(run)
                print(json.dumps(run), flush=True)
                save(output / "runs.json", runs)
    aggregated = []
    for concurrency in args.concurrency:
        for arm in ("raw", "queued"):
            group = [r for r in runs if r["concurrency"] == concurrency and r["arm"] == arm]
            aggregate = {"concurrency": concurrency, "arm": arm, "runs": len(group),
                         "requests": sum(r["requests"] for r in group), "errors": sum(r["errors"] for r in group)}
            for field in ("p95_ms", "p99_ms", "throughput_rps", "successful_rps", "connection_entry_p95_ms"):
                aggregate["median_run_" + field] = statistics.median(r[field] for r in group)
            aggregated.append(aggregate)
    summary = {"plan": plan, "aggregated": aggregated,
               "all_oracles_passed": all(r["oracle_passed"] for r in runs),
               "all_recoveries_passed": all(not r["recovery_errors"] for r in runs),
               "all_warmups_passed": all(not r["warmup_errors"] for r in runs)}
    save(output / "comparison.json", summary)
    lines = ["# SQLite 写入排队同条件 HTTP 对照", "", "本轮是新建对照，不复现或替代历史 14.83% 错误率。",
             "同一冻结源码、相同数据库副本、请求、超时与服务器线程数，只改变进程内写入排队开关。",
             "每轮新启动服务；奇数轮 raw→queued，偶数轮 queued→raw。下表延迟和吞吐是各轮指标的中位数，不是合并分位数。", "",
             "| 并发 | 策略 | 轮数 | 请求 | 错误 | P95 ms | 成功 req/s | 连接进入 P95 ms |",
             "|---:|---|---:|---:|---:|---:|---:|---:|"]
    for r in aggregated:
        lines.append(f"| {r['concurrency']} | {r['arm']} | {r['runs']} | {r['requests']} | {r['errors']} | "
                     f"{r['median_run_p95_ms']:.2f} | {r['median_run_successful_rps']:.2f} | {r['median_run_connection_entry_p95_ms']:.2f} |")
    lines += ["", "HTTP延迟包含服务器排队；客户端提交等待另见 requests.jsonl。连接进入包含排队、连接、PRAGMA和BEGIN，并非纯锁等待。",
              "原始请求、连接计时、SQLite副本、业务状态校验、服务日志、代码快照与计划均保留。",
              "仅单进程共享Store实例受进程内锁协调，多进程仍依赖SQLite事务锁。没有模型和真实支付调用。",
              f"业务状态校验全部通过：{summary['all_oracles_passed']}；恢复请求全部通过：{summary['all_recoveries_passed']}。", ""]
    (output / "README.md").write_text("\n".join(lines), encoding="utf-8")
    return 0 if all(summary[k] for k in ("all_oracles_passed", "all_recoveries_passed", "all_warmups_passed")) and not any(r["errors"] for r in runs if r["arm"] == "queued") else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[50, 100, 200])
    parser.add_argument("--requests", type=int, default=400)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=35)
    parser.add_argument("--server-threads", type=int, default=200)
    parser.add_argument("--environment-note", default="Shared developer workstation; not a dedicated load host")
    parser.add_argument("--database", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--connection-log", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--arm", choices=["raw", "queued"], help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        return serve(args)
    if args.output_dir is None or args.output_dir.exists():
        parser.error("--output-dir must name a new directory; old evidence is never overwritten")
    if min(args.requests, args.repeats, args.warmup, args.server_threads, *args.concurrency) < 1:
        parser.error("Counts and concurrency must be positive")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("Timeout must be finite and positive")
    return asyncio.run(compare(args))


if __name__ == "__main__":
    raise SystemExit(main())

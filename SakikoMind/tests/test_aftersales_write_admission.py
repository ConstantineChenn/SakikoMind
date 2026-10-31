"""Bounded write admission preserves real SQLite transactions and business state.

All data lives in per-test files. No model, network, credentials or live database
is used; thread waits have deadlines so a leaked admission lock fails promptly.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3
import threading
from unittest.mock import patch

import pytest

from core.aftersales_store import AfterSalesError, AfterSalesStore, Principal


@pytest.fixture
def store(tmp_path):
    instance = AfterSalesStore(str(tmp_path / "orders.db"), write_queue_timeout_s=0.1)
    instance.seed_demo()
    return instance


@pytest.mark.parametrize("argument,value", [
    ("serialize_writes", 1), ("serialize_writes", "false"), ("serialize_writes", None),
    ("write_queue_timeout_s", True), ("write_queue_timeout_s", "1"),
    ("write_queue_timeout_s", 0), ("write_queue_timeout_s", -1),
    ("write_queue_timeout_s", float("inf")), ("write_queue_timeout_s", float("nan")),
    ("write_queue_timeout_s", threading.TIMEOUT_MAX * 2),
])
def test_invalid_admission_options_create_no_database(tmp_path, argument, value):
    path = tmp_path / "invalid.db"
    with pytest.raises(AfterSalesError) as raised:
        AfterSalesStore(str(path), **{argument: value})
    assert (raised.value.code, raised.value.status_code) == ("invalid_input", 422)
    assert not path.exists()


def test_default_and_control_modes_keep_same_sqlite_transaction(tmp_path):
    for serialized in (True, False):
        instance = AfterSalesStore(str(tmp_path / f"{serialized}.db"), serialize_writes=serialized)
        assert instance.serialize_writes is serialized
        assert instance.write_queue_timeout_s == 15.0
        with instance._connection(write=True) as connection:
            assert connection.in_transaction
            assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 15000
            # A second connection cannot start a writer: BEGIN IMMEDIATE is
            # still the cross-instance/process safety mechanism in both modes.
            with closing(sqlite3.connect(instance.db_path, timeout=0)) as other:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    other.execute("BEGIN IMMEDIATE")


def test_simultaneous_same_key_confirmation_keeps_one_durable_application(store):
    actor = Principal("alice")
    quote = store.prepare(actor, "DEMO-1001", "Changed my mind")
    barrier = threading.Barrier(8)

    def confirm():
        barrier.wait(timeout=3)
        return store.confirm(actor, quote["quote_id"], quote["confirmation_token"], "shared-key")

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(confirm) for _ in range(8)]
        applications = [future.result(timeout=5) for future in futures]
    assert len({application["application_id"] for application in applications}) == 1
    assert all(application["status"] == "ready" for application in applications)
    with closing(sqlite3.connect(store.db_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM aftersales_applications").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM aftersales_idempotency").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM aftersales_operations").fetchone()[0] == 0
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_queue_timeout_does_not_open_connection_or_release_active_writer(store):
    entered = threading.Event()
    release = threading.Event()

    def hold_writer():
        with store._connection(write=True):
            entered.set()
            if not release.wait(timeout=3):
                raise TimeoutError("test writer was not released")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(hold_writer)
        try:
            assert entered.wait(timeout=2)
            with patch("core.aftersales_store.sqlite3.connect") as open_connection:
                with pytest.raises(AfterSalesError) as raised:
                    store.prepare(Principal("alice"), "DEMO-1001", "Timed out")
            assert (raised.value.code, raised.value.status_code) == ("write_queue_timeout", 503)
            open_connection.assert_not_called()
            assert store._write_lock.locked()
        finally:
            release.set()
            future.result(timeout=2)
    assert not store._write_lock.locked()
    # Timeout created no quote; the next request can still persist one.
    with closing(sqlite3.connect(store.db_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM aftersales_quotes").fetchone()[0] == 0
    assert store.prepare(Principal("alice"), "DEMO-1001", "Retry")["order_id"] == "DEMO-1001"


def test_reader_does_not_wait_for_same_instance_writer(store):
    with store._connection(write=True) as writer:
        writer.execute("UPDATE aftersales_orders SET status='cancelled' WHERE order_id='DEMO-1001'")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(store.get_order, Principal("alice"), "DEMO-1001")
            result = future.result(timeout=1)
        # Reader observes committed state while the RESERVED writer lock is held.
        assert result["status"] == "paid"
    assert store.get_order(Principal("alice"), "DEMO-1001")["status"] == "cancelled"


def test_control_mode_bypasses_only_the_instance_admission_lock(tmp_path):
    instance = AfterSalesStore(str(tmp_path / "control.db"), serialize_writes=False,
                               write_queue_timeout_s=0.01)
    instance.seed_demo()
    with instance._write_lock:
        quote = instance.prepare(Principal("alice"), "DEMO-1001", "Control")
    assert quote["order_id"] == "DEMO-1001"


def test_connection_open_failure_releases_write_admission(store):
    with patch("core.aftersales_store.sqlite3.connect", side_effect=sqlite3.OperationalError("open failed")):
        with pytest.raises(sqlite3.OperationalError, match="open failed"):
            store.prepare(Principal("alice"), "DEMO-1001", "Failure")
    assert not store._write_lock.locked()
    assert store.prepare(Principal("alice"), "DEMO-1001", "Recovery")["order_id"] == "DEMO-1001"


def test_http_timeout_returns_explicit_503_and_recovers_without_partial_write(store):
    from fastapi.testclient import TestClient
    from api.aftersales import create_app

    token = "synthetic-write-admission-test"
    app = create_app(store=store, tokens={token: {"user_id": "alice", "role": "customer"}})
    headers = {"Authorization": "Bearer " + token}
    with TestClient(app) as client:
        with store._connection(write=True):
            failed = client.post("/aftersales/quotes", headers=headers,
                                 json={"order_id": "DEMO-1001", "reason": "Queue test"})
        assert failed.status_code == 503
        assert failed.json()["detail"]["code"] == "write_queue_timeout"
        recovered = client.post("/aftersales/quotes", headers=headers,
                                json={"order_id": "DEMO-1001", "reason": "Queue test"})
        assert recovered.status_code == 200
    with closing(sqlite3.connect(store.db_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM aftersales_quotes").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM aftersales_applications").fetchone()[0] == 0


def test_body_failure_rolls_back_and_allows_next_writer(store):
    with pytest.raises(RuntimeError, match="abort transaction"):
        with store._connection(write=True) as connection:
            connection.execute("UPDATE aftersales_orders SET status='cancelled' WHERE order_id='DEMO-1001'")
            raise RuntimeError("abort transaction")
    assert not store._write_lock.locked()
    assert store.get_order(Principal("alice"), "DEMO-1001")["status"] == "paid"
    assert store.prepare(Principal("alice"), "DEMO-1001", "After rollback")["order_id"] == "DEMO-1001"


@pytest.mark.parametrize("failure", ["commit", "rollback", "close"])
def test_cleanup_failures_still_close_connection_and_release_admission(store, failure):
    real_connect = sqlite3.connect
    connections = []

    class FailingConnection(sqlite3.Connection):
        def commit(self):
            if failure == "commit":
                raise sqlite3.OperationalError("commit failed")
            return super().commit()

        def rollback(self):
            if failure == "rollback":
                raise sqlite3.OperationalError("rollback failed")
            return super().rollback()

        def close(self):
            super().close()
            if failure == "close":
                raise sqlite3.OperationalError("close failed")

    def connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs, factory=FailingConnection)
        connections.append(connection)
        return connection

    with patch("core.aftersales_store.sqlite3.connect", side_effect=connect):
        with pytest.raises(sqlite3.OperationalError, match=failure + " failed"):
            with store._connection(write=True) as connection:
                connection.execute("UPDATE aftersales_orders SET status='cancelled' WHERE order_id='DEMO-1001'")
                if failure == "rollback":
                    raise RuntimeError("trigger rollback")
    assert not store._write_lock.locked()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
    expected = "cancelled" if failure == "close" else "paid"
    assert store.get_order(Principal("alice"), "DEMO-1001")["status"] == expected
    # Writing a different paid order proves admission and SQLite handles recover.
    assert store.prepare(Principal("alice"), "DEMO-1002", "Next writer")["order_id"] == "DEMO-1002"

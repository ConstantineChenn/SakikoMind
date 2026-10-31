"""Ensure the performance oracle rejects partial confirmation persistence."""
from pathlib import Path
import sqlite3

import pytest

from core.aftersales_store import AfterSalesStore, Principal
from performance.sqlite_write_comparison import USER, fixture, state_oracle


@pytest.mark.parametrize("missing_table", [None, "aftersales_idempotency", "aftersales_audit"])
def test_confirmation_oracle_requires_atomic_evidence(tmp_path, missing_table):
    source = Path(__file__).resolve().parents[1]
    db, payloads = fixture(source, tmp_path, 1)
    store = AfterSalesStore(str(db))
    payload = payloads[0]
    result = store.confirm(Principal(USER), payload["quote_id"], payload["confirmation_token"],
                           payload["idempotency_key"], "test-trace")
    if missing_table:
        with sqlite3.connect(db) as connection:
            # The only possible names come from the explicit parametrization.
            connection.execute("DELETE FROM " + missing_table)
    rows = [{"request_id": "measured-000000", "order_id": "CMP-000000", "success": True,
             "application_id": result["application_id"], "http_status": 200, "error": None}]
    assert state_oracle(db, rows)["valid"] is (missing_table is None)

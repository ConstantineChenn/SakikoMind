import sqlite3
import tempfile
import unittest

from core.handoff_store import HandoffStore


class HandoffStoreTests(unittest.TestCase):
    def test_handoff_ticket_persists_and_updates_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = f"{temp_dir}/handoffs/tickets.db"
            store = HandoffStore(db_path)
            ticket = store.create(
                {
                    "ticket_id": "EM-TEST-001",
                    "trace_id": "trc-test-001",
                    "conv_id": "conv-001",
                    "user_id": "user-001",
                    "agent_type": "billing",
                    "reason": "payment_dispute",
                    "reason_label": "账务争议",
                    "priority": "P1",
                    "summary": "billing Agent：用户报告重复扣款。",
                    "created_at": "2026-08-07T00:00:00Z",
                    "updated_at": "2026-08-07T00:00:00Z",
                    "citation_source_ids": ["SaaS-REFUND-001"],
                }
            )

            self.assertEqual(ticket["status"], "open")
            self.assertEqual(ticket["trace_id"], "trc-test-001")
            self.assertEqual(ticket["delivery"], "internal_ticket_center")
            self.assertEqual(ticket["citation_source_ids"], ["SaaS-REFUND-001"])

            updated = store.update_status("EM-TEST-001", "in_progress")
            restarted_store = HandoffStore(db_path)

            self.assertEqual(updated["status"], "in_progress")
            self.assertEqual(restarted_store.get("EM-TEST-001")["status"], "in_progress")
            self.assertEqual(restarted_store.list(status="in_progress")[0]["ticket_id"], "EM-TEST-001")

    def test_existing_database_adds_trace_id_column(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = f"{temp_dir}/tickets.db"
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    """
                    CREATE TABLE handoff_tickets (
                        ticket_id TEXT PRIMARY KEY,
                        conv_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        agent_type TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        reason_label TEXT NOT NULL,
                        priority TEXT NOT NULL,
                        status TEXT NOT NULL,
                        delivery TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        citation_source_ids TEXT NOT NULL
                    )
                    """
                )

            store = HandoffStore(db_path)
            with sqlite3.connect(db_path) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(handoff_tickets)")}

            self.assertIn("trace_id", columns)
            self.assertEqual(store.list(), [])

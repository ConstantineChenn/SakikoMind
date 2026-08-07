"""SakikoMind 内置人工工单的 SQLite 持久化存储。"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


ALLOWED_STATUSES = {"open", "in_progress", "resolved", "closed"}


class HandoffStore:
    """使用本地 SQLite 管理人工工单，适合单实例部署与本地演示。"""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create(self, ticket: Dict[str, Any]) -> Dict[str, Any]:
        """持久化新工单，并返回标准化后的记录。"""
        now = _utc_now()
        payload = {
            "ticket_id": ticket["ticket_id"],
            "trace_id": ticket.get("trace_id", ""),
            "conv_id": ticket["conv_id"],
            "user_id": ticket["user_id"],
            "agent_type": ticket["agent_type"],
            "reason": ticket["reason"],
            "reason_label": ticket["reason_label"],
            "priority": ticket["priority"],
            "status": "open",
            "delivery": "internal_ticket_center",
            "summary": ticket["summary"],
            "created_at": ticket.get("created_at") or now,
            "updated_at": now,
            "citation_source_ids": json.dumps(ticket.get("citation_source_ids", []), ensure_ascii=False),
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO handoff_tickets (
                    ticket_id, trace_id, conv_id, user_id, agent_type, reason, reason_label,
                    priority, status, delivery, summary, created_at, updated_at,
                    citation_source_ids
                ) VALUES (
                    :ticket_id, :trace_id, :conv_id, :user_id, :agent_type, :reason, :reason_label,
                    :priority, :status, :delivery, :summary, :created_at, :updated_at,
                    :citation_source_ids
                )
                """,
                payload,
            )
        return self.get(payload["ticket_id"]) or {}

    def get(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        """按工单号读取单条记录。"""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM handoff_tickets WHERE ticket_id = ?",
                (ticket_id,),
            ).fetchone()
        return _row_to_ticket(row) if row else None

    def list(self, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """按更新时间倒序查询工单。"""
        bounded_limit = max(1, min(limit, 100))
        query = "SELECT * FROM handoff_tickets"
        params: List[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(bounded_limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_ticket(row) for row in rows]

    def update_status(self, ticket_id: str, status: str) -> Optional[Dict[str, Any]]:
        """更新工单状态，非法状态由调用方转换为接口错误。"""
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"不支持的工单状态：{status}")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE handoff_tickets SET status = ?, updated_at = ? WHERE ticket_id = ?",
                (status, _utc_now(), ticket_id),
            )
        if cursor.rowcount == 0:
            return None
        return self.get(ticket_id)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS handoff_tickets (
                    ticket_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL DEFAULT '',
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
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(handoff_tickets)").fetchall()
            }
            if "trace_id" not in columns:
                connection.execute(
                    "ALTER TABLE handoff_tickets ADD COLUMN trace_id TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_handoff_tickets_status_updated "
                "ON handoff_tickets (status, updated_at DESC)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection


def _row_to_ticket(row: sqlite3.Row) -> Dict[str, Any]:
    ticket = dict(row)
    ticket["citation_source_ids"] = json.loads(ticket["citation_source_ids"])
    return ticket


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

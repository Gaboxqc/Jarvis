"""Action history — REQ-25.

Every side effect Kai performs lands here before and after it runs, whether it
succeeded or not. This table is what makes "what did you just do?" and "undo
that" answerable.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .. import db

# Lifecycle: pending_confirmation -> (declined | expired | executed | failed)
#            executed -> undone
STATUS_PENDING = "pending_confirmation"
STATUS_EXECUTED = "executed"
STATUS_FAILED = "failed"
STATUS_DECLINED = "declined"
STATUS_EXPIRED = "expired"
STATUS_UNDONE = "undone"


@dataclass
class ActionRecord:
    id: str
    batch_id: str
    skill_name: str
    params: dict[str, Any]
    severity: str
    reversible: bool
    preview: str
    status: str
    undo_payload: dict[str, Any] | None
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime | None
    executed_at: datetime | None
    expires_at: datetime | None

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def can_undo(self) -> bool:
        return self.status == STATUS_EXECUTED and self.reversible and self.undo_payload is not None


def _row_to_record(row: Any) -> ActionRecord:
    return ActionRecord(
        id=row["id"],
        batch_id=row["batch_id"],
        skill_name=row["skill_name"],
        params=db.loads(row["params"], {}),
        severity=row["severity"],
        reversible=bool(row["reversible"]),
        preview=row["preview"],
        status=row["status"],
        undo_payload=db.loads(row["undo_payload"], None),
        result=db.loads(row["result"], None),
        error=row["error"],
        created_at=db.parse_ts(row["created_at"]),
        executed_at=db.parse_ts(row["executed_at"]),
        expires_at=db.parse_ts(row["expires_at"]),
    )


def create(
    *,
    skill_name: str,
    params: dict[str, Any],
    severity: str,
    reversible: bool,
    preview: str,
    status: str,
    batch_id: str | None = None,
    ttl_minutes: int | None = None,
) -> ActionRecord:
    action_id = str(uuid.uuid4())
    expires_at = None
    if ttl_minutes is not None:
        expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
        expires_at = expires.replace(microsecond=0).isoformat()

    db.execute(
        """
        INSERT INTO action_records
            (id, batch_id, skill_name, params, severity, reversible, preview,
             status, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            action_id,
            batch_id or action_id,
            skill_name,
            db.dumps(params),
            severity,
            int(reversible),
            preview,
            status,
            db.now(),
            expires_at,
        ),
    )
    record = get(action_id)
    assert record is not None
    return record


def get(action_id: str) -> ActionRecord | None:
    row = db.query_one("SELECT * FROM action_records WHERE id = ?", (action_id,))
    return _row_to_record(row) if row else None


def mark_executed(
    action_id: str,
    *,
    result: dict[str, Any] | None,
    undo_payload: dict[str, Any] | None,
) -> None:
    db.execute(
        """
        UPDATE action_records
           SET status = ?, result = ?, undo_payload = ?, executed_at = ?, expires_at = NULL
         WHERE id = ?
        """,
        (STATUS_EXECUTED, db.dumps(result or {}), db.dumps(undo_payload) if undo_payload else None,
         db.now(), action_id),
    )


def mark_failed(action_id: str, error: str) -> None:
    db.execute(
        "UPDATE action_records SET status = ?, error = ?, executed_at = ?, expires_at = NULL WHERE id = ?",
        (STATUS_FAILED, error, db.now(), action_id),
    )


def mark_status(action_id: str, status: str) -> None:
    db.execute("UPDATE action_records SET status = ? WHERE id = ?", (status, action_id))


def expire_stale() -> int:
    """Pending confirmations do not linger. An approval prompt from an hour ago
    is not consent now (REQ-24)."""
    cur = db.execute(
        """
        UPDATE action_records
           SET status = ?
         WHERE status = ? AND expires_at IS NOT NULL AND expires_at < ?
        """,
        (STATUS_EXPIRED, STATUS_PENDING, db.now()),
    )
    return cur.rowcount or 0


def pending() -> list[ActionRecord]:
    expire_stale()
    rows = db.query(
        "SELECT * FROM action_records WHERE status = ? ORDER BY created_at DESC",
        (STATUS_PENDING,),
    )
    return [_row_to_record(row) for row in rows]


def history(limit: int = 50) -> list[ActionRecord]:
    rows = db.query(
        """
        SELECT * FROM action_records
         WHERE status != ?
         ORDER BY COALESCE(executed_at, created_at) DESC, rowid DESC
         LIMIT ?
        """,
        (STATUS_PENDING, limit),
    )
    return [_row_to_record(row) for row in rows]


def batch(batch_id: str) -> list[ActionRecord]:
    rows = db.query(
        "SELECT * FROM action_records WHERE batch_id = ? ORDER BY created_at ASC",
        (batch_id,),
    )
    return [_row_to_record(row) for row in rows]


def last_undoable() -> ActionRecord | None:
    """Backs 'undo that' with no further qualification.

    `rowid` breaks ties deliberately. Timestamps are stored at second precision,
    so several actions in one turn share one `executed_at` — ordering on the
    timestamp alone would let "undo that" reverse the wrong one of them.
    """
    rows = db.query(
        """
        SELECT * FROM action_records
         WHERE status = ? AND reversible = 1 AND undo_payload IS NOT NULL
         ORDER BY executed_at DESC, rowid DESC
         LIMIT 1
        """,
        (STATUS_EXECUTED,),
    )
    return _row_to_record(rows[0]) if rows else None

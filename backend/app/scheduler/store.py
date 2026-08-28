"""Scheduled item storage — REQ-9, REQ-12.

Scheduled items live in SQLite, not in a process's memory. That is what makes
"persist across restarts and reboots" true rather than aspirational, and it is
what allows a missed reminder to be delivered on next launch with its original
due time intact.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .. import db

KIND_REMINDER = "reminder"
KIND_TIMER = "timer"
KIND_ROUTINE = "routine"


@dataclass
class ScheduledItem:
    id: str
    kind: str
    label: str
    payload: dict[str, Any]
    next_fire_at: datetime | None
    recurrence: dict[str, Any] | None
    created_at: datetime | None
    last_fired_at: datetime | None
    active: bool
    delivered: bool

    @property
    def is_due(self) -> bool:
        if not self.active or self.next_fire_at is None:
            return False
        return self.next_fire_at <= datetime.now(timezone.utc)

    def describe(self) -> str:
        when = self.next_fire_at.astimezone().strftime("%a %d %b %H:%M") if self.next_fire_at else "—"
        suffix = ""
        if self.recurrence:
            from ..skills.planning.timeparse import describe_recurrence

            suffix = f" (repeats {describe_recurrence(self.recurrence)})"
        return f"{self.label} — {when}{suffix}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "next_fire_at": self.next_fire_at.isoformat() if self.next_fire_at else None,
            "recurring": bool(self.recurrence),
            "active": self.active,
        }


def _row(row: Any) -> ScheduledItem:
    return ScheduledItem(
        id=row["id"],
        kind=row["kind"],
        label=row["label"],
        payload=db.loads(row["payload"], {}),
        next_fire_at=db.parse_ts(row["next_fire_at"]),
        recurrence=db.loads(row["recurrence"], None),
        created_at=db.parse_ts(row["created_at"]),
        last_fired_at=db.parse_ts(row["last_fired_at"]),
        active=bool(row["active"]),
        delivered=bool(row["delivered"]),
    )


def add(
    *,
    kind: str,
    label: str,
    fire_at: datetime,
    recurrence: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> ScheduledItem:
    item_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO scheduled_items
            (id, kind, label, payload, next_fire_at, recurrence, created_at, active, delivered)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0)
        """,
        (
            item_id,
            kind,
            label,
            db.dumps(payload or {}),
            _iso(fire_at),
            db.dumps(recurrence) if recurrence else None,
            db.now(),
        ),
    )
    item = get(item_id)
    assert item is not None
    return item


def get(item_id: str) -> ScheduledItem | None:
    row = db.query_one("SELECT * FROM scheduled_items WHERE id = ?", (item_id,))
    return _row(row) if row else None


def active_items() -> list[ScheduledItem]:
    rows = db.query(
        "SELECT * FROM scheduled_items WHERE active = 1 ORDER BY next_fire_at ASC"
    )
    return [_row(r) for r in rows]


def due_items() -> list[ScheduledItem]:
    """Everything whose time has passed and which has not been delivered yet.

    Because this is a query over stored times rather than a set of in-memory
    timers, an item that came due while the app was closed is still returned —
    that is the missed-reminder replay required by REQ-9.
    """
    rows = db.query(
        """
        SELECT * FROM scheduled_items
         WHERE active = 1 AND delivered = 0
           AND next_fire_at IS NOT NULL AND next_fire_at <= ?
         ORDER BY next_fire_at ASC
        """,
        (db.now(),),
    )
    return [_row(r) for r in rows]


def find(text: str) -> list[ScheduledItem]:
    exact = get(text.strip())
    if exact is not None:
        return [exact]
    rows = db.query(
        "SELECT * FROM scheduled_items WHERE active = 1 AND label LIKE ? ORDER BY next_fire_at ASC",
        (f"%{text.strip()}%",),
    )
    return [_row(r) for r in rows]


def mark_fired(item_id: str, next_fire_at: datetime | None) -> None:
    """Advance a recurring item, or retire a one-off."""
    if next_fire_at is None:
        db.execute(
            "UPDATE scheduled_items SET active = 0, delivered = 1, last_fired_at = ? WHERE id = ?",
            (db.now(), item_id),
        )
    else:
        db.execute(
            """
            UPDATE scheduled_items
               SET next_fire_at = ?, last_fired_at = ?, delivered = 0
             WHERE id = ?
            """,
            (_iso(next_fire_at), db.now(), item_id),
        )


def set_payload(item_id: str, payload: dict[str, Any]) -> None:
    """Replace an item's payload. Used by routines when their steps change."""
    db.execute(
        "UPDATE scheduled_items SET payload = ? WHERE id = ?", (db.dumps(payload), item_id)
    )


def cancel(item_id: str) -> ScheduledItem | None:
    item = get(item_id)
    if item is None:
        return None
    db.execute("UPDATE scheduled_items SET active = 0 WHERE id = ?", (item_id,))
    return item


def restore(item: ScheduledItem) -> None:
    db.execute("UPDATE scheduled_items SET active = 1 WHERE id = ?", (item.id,))


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()

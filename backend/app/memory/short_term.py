"""Short-term conversation memory — REQ-6.

A rolling window over the current session, reset once the session has been idle
past the configured timeout. The reset is computed from the gap between turns
rather than a background timer, so it behaves the same whether the app was
running or closed during the gap.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .. import db
from ..settings import load_config

DEFAULT_WINDOW_TURNS = 20


@dataclass
class Turn:
    id: str
    role: str
    text: str
    ts: datetime | None
    skill_calls: list[dict[str, Any]]

    def as_message(self) -> dict[str, str]:
        return {"role": self.role, "content": self.text}


def record(
    session_id: str,
    role: str,
    text: str,
    skill_calls: list[dict[str, Any]] | None = None,
) -> Turn:
    turn_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO conversation_turns(id, session_id, role, text, ts, skill_calls) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        (turn_id, session_id, role, text, db.now(), db.dumps(skill_calls or [])),
    )
    return Turn(turn_id, role, text, datetime.now(timezone.utc), skill_calls or [])


def window(session_id: str, limit: int = DEFAULT_WINDOW_TURNS) -> list[Turn]:
    """Recent turns, oldest first, truncated at the idle boundary."""
    rows = db.query(
        "SELECT * FROM conversation_turns WHERE session_id = ? ORDER BY ts DESC, rowid DESC LIMIT ?",
        (session_id, limit),
    )
    turns = [
        Turn(
            id=row["id"],
            role=row["role"],
            text=row["text"],
            ts=db.parse_ts(row["ts"]),
            skill_calls=db.loads(row["skill_calls"], []),
        )
        for row in rows
    ]
    turns.reverse()
    return _trim_at_idle_gap(turns)


def _trim_at_idle_gap(turns: list[Turn]) -> list[Turn]:
    timeout = timedelta(minutes=load_config().persona.idle_timeout_minutes)
    if timeout <= timedelta(0) or len(turns) < 2:
        return turns

    # Walk backwards; the first gap wider than the timeout is where this
    # conversation started as far as the user is concerned.
    cut = 0
    for i in range(len(turns) - 1, 0, -1):
        previous, current = turns[i - 1].ts, turns[i].ts
        if previous and current and (current - previous) > timeout:
            cut = i
            break
    return turns[cut:]


def is_session_stale(session_id: str) -> bool:
    row = db.query_one(
        "SELECT ts FROM conversation_turns WHERE session_id = ? ORDER BY ts DESC LIMIT 1",
        (session_id,),
    )
    last = db.parse_ts(row["ts"]) if row else None
    if last is None:
        return True
    timeout = timedelta(minutes=load_config().persona.idle_timeout_minutes)
    return datetime.now(timezone.utc) - last > timeout


def clear(session_id: str) -> int:
    cur = db.execute("DELETE FROM conversation_turns WHERE session_id = ?", (session_id,))
    return cur.rowcount or 0


def last_skill_result(session_id: str) -> dict[str, Any] | None:
    """Backs pronoun resolution against the most recent tool result (REQ-6)."""
    rows = db.query(
        "SELECT skill_calls FROM conversation_turns "
        "WHERE session_id = ? AND skill_calls != '[]' ORDER BY ts DESC LIMIT 1",
        (session_id,),
    )
    if not rows:
        return None
    calls = db.loads(rows[0]["skill_calls"], [])
    return calls[-1] if calls else None

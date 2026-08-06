"""Transcript storage — REQ-19, REQ-26.

Transcripts stay on this machine, are listable, and are individually deletable.
A recording of a conversation with other people in it is among the most
sensitive things this assistant will ever hold, so "where is it and how do I
delete it" has to have an obvious answer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .. import db


@dataclass
class Transcript:
    id: str
    label: str
    started_at: datetime | None
    ended_at: datetime | None
    sources: list[str]
    text: str
    summary: dict[str, Any] | None
    duration_seconds: float

    @property
    def is_running(self) -> bool:
        return self.ended_at is None

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    def describe(self) -> str:
        when = self.started_at.astimezone().strftime("%d %b %H:%M") if self.started_at else "?"
        minutes = int(self.duration_seconds // 60)
        state = " (recording)" if self.is_running else ""
        return f"{self.label or 'Untitled'} - {when}, {minutes} min, {self.word_count} words{state}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "sources": self.sources,
            "minutes": round(self.duration_seconds / 60, 1),
            "words": self.word_count,
            "summary": self.summary,
            "running": self.is_running,
        }


def _row(row: Any) -> Transcript:
    return Transcript(
        id=row["id"],
        label=row["label"],
        started_at=db.parse_ts(row["started_at"]),
        ended_at=db.parse_ts(row["ended_at"]),
        sources=db.loads(row["sources"], []) or [],
        text=row["text"] or "",
        summary=db.loads(row["summary"], None),
        duration_seconds=float(row["duration_seconds"] or 0),
    )


def create(label: str, sources: list[str]) -> Transcript:
    transcript_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO transcripts(id, label, started_at, sources, text, duration_seconds) "
        "VALUES(?, ?, ?, ?, '', 0)",
        (transcript_id, label, db.now(), db.dumps(sources)),
    )
    found = get(transcript_id)
    assert found is not None
    return found


def append_text(transcript_id: str, text: str) -> None:
    """Add a chunk's text as it is transcribed, so nothing is lost on a crash."""
    if not text.strip():
        return
    db.execute(
        "UPDATE transcripts SET text = TRIM(text || ' ' || ?) WHERE id = ?",
        (text.strip(), transcript_id),
    )


def finish(transcript_id: str, duration_seconds: float) -> Transcript | None:
    db.execute(
        "UPDATE transcripts SET ended_at = ?, duration_seconds = ? WHERE id = ?",
        (db.now(), duration_seconds, transcript_id),
    )
    return get(transcript_id)


def set_summary(transcript_id: str, summary: dict[str, Any]) -> None:
    db.execute("UPDATE transcripts SET summary = ? WHERE id = ?",
               (db.dumps(summary), transcript_id))


def set_label(transcript_id: str, label: str) -> None:
    db.execute("UPDATE transcripts SET label = ? WHERE id = ?", (label, transcript_id))


def get(transcript_id: str) -> Transcript | None:
    row = db.query_one("SELECT * FROM transcripts WHERE id = ?", (transcript_id,))
    return _row(row) if row else None


def latest() -> Transcript | None:
    rows = db.query("SELECT * FROM transcripts ORDER BY started_at DESC LIMIT 1")
    return _row(rows[0]) if rows else None


def recent(limit: int = 20) -> list[Transcript]:
    rows = db.query("SELECT * FROM transcripts ORDER BY started_at DESC LIMIT ?", (limit,))
    return [_row(r) for r in rows]


def find(query: str) -> list[Transcript]:
    like = f"%{query.strip()}%"
    rows = db.query(
        "SELECT * FROM transcripts WHERE label LIKE ? OR text LIKE ? "
        "ORDER BY started_at DESC LIMIT 20",
        (like, like),
    )
    return [_row(r) for r in rows]


def delete(transcript_id: str) -> Transcript | None:
    found = get(transcript_id)
    if found is None:
        return None
    db.execute("DELETE FROM transcripts WHERE id = ?", (transcript_id,))
    return found


def delete_all() -> int:
    count = len(recent(limit=10_000))
    db.execute("DELETE FROM transcripts")
    return count

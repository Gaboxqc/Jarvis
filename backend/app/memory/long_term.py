"""Long-term personal memory — REQ-7.

Durable facts the user has explicitly agreed to store. Two rules matter more
than the storage details:

* Nothing is written here silently. Writes go through the Action Gate as a
  consequential action, so the user sees each one as it happens.
* Everything here is reviewable and individually deletable.

Retrieval is lexical overlap rather than embeddings. At the scale of a personal
fact store (tens to low hundreds of entries) it is accurate enough, costs
nothing, and adds no model dependency. Swapping in embeddings later touches only
`relevant()`.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .. import db

CATEGORIES = ("preference", "fact", "shortcut", "person")

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "do", "for", "from",
    "has", "have", "how", "i", "if", "in", "is", "it", "its", "me", "my", "of",
    "on", "or", "that", "the", "then", "there", "they", "this", "to", "was",
    "what", "when", "where", "which", "who", "will", "with", "you", "your",
}


@dataclass
class MemoryFact:
    id: str
    text: str
    category: str
    created_at: datetime | None
    last_used_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "category": self.category,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def _row(row: Any) -> MemoryFact:
    return MemoryFact(
        id=row["id"],
        text=row["text"],
        category=row["category"],
        created_at=db.parse_ts(row["created_at"]),
        last_used_at=db.parse_ts(row["last_used_at"]),
    )


def add(text: str, category: str = "fact", source_turn_id: str | None = None) -> MemoryFact:
    text = text.strip()
    if not text:
        raise ValueError("empty memory")
    if category not in CATEGORIES:
        category = "fact"

    existing = _find_duplicate(text)
    if existing is not None:
        # Re-stating a known fact updates it rather than accumulating near
        # duplicates that would later contradict each other.
        db.execute("UPDATE memory_facts SET text = ?, category = ? WHERE id = ?",
                   (text, category, existing.id))
        return MemoryFact(existing.id, text, category, existing.created_at, existing.last_used_at)

    fact_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO memory_facts(id, text, category, source_turn_id, created_at) "
        "VALUES(?, ?, ?, ?, ?)",
        (fact_id, text, category, source_turn_id, db.now()),
    )
    got = get(fact_id)
    assert got is not None
    return got


def get(fact_id: str) -> MemoryFact | None:
    row = db.query_one("SELECT * FROM memory_facts WHERE id = ?", (fact_id,))
    return _row(row) if row else None


def all_facts() -> list[MemoryFact]:
    return [_row(r) for r in db.query("SELECT * FROM memory_facts ORDER BY created_at DESC")]


def delete(fact_id: str) -> MemoryFact | None:
    fact = get(fact_id)
    if fact is None:
        return None
    db.execute("DELETE FROM memory_facts WHERE id = ?", (fact_id,))
    return fact


def delete_all() -> int:
    count = len(all_facts())
    db.execute("DELETE FROM memory_facts")
    return count


def search(text: str) -> list[MemoryFact]:
    like = f"%{text.strip()}%"
    rows = db.query(
        "SELECT * FROM memory_facts WHERE text LIKE ? ORDER BY created_at DESC", (like,)
    )
    return [_row(r) for r in rows]


def relevant(query_text: str, limit: int = 6) -> list[MemoryFact]:
    """Facts worth putting in front of the model for this turn."""
    terms = _tokens(query_text)
    if not terms:
        return []

    scored: list[tuple[float, MemoryFact]] = []
    for fact in all_facts():
        fact_terms = _tokens(fact.text)
        if not fact_terms:
            continue
        overlap = terms & fact_terms
        if not overlap:
            continue
        # Normalise by the fact's own length so a long note does not outrank a
        # short precise one just by covering more words.
        score = len(overlap) / (len(fact_terms) ** 0.5)
        scored.append((score, fact))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    top = [fact for _, fact in scored[:limit]]
    for fact in top:
        db.execute("UPDATE memory_facts SET last_used_at = ? WHERE id = ?", (db.now(), fact.id))
    return top


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def _find_duplicate(text: str) -> MemoryFact | None:
    new_terms = _tokens(text)
    if not new_terms:
        return None
    for fact in all_facts():
        existing_terms = _tokens(fact.text)
        if not existing_terms:
            continue
        union = new_terms | existing_terms
        if len(new_terms & existing_terms) / len(union) >= 0.7:
            return fact
    return None

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
        updated = MemoryFact(
            existing.id, text, category, existing.created_at, existing.last_used_at
        )
        # The wording changed, so the old vector describes text that no longer
        # exists. Re-embedded rather than left: a stale vector is worse than
        # none, because it still ranks.
        embed_fact(updated)
        return updated

    fact_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO memory_facts(id, text, category, source_turn_id, created_at) "
        "VALUES(?, ?, ?, ?, ?)",
        (fact_id, text, category, source_turn_id, db.now()),
    )
    got = get(fact_id)
    assert got is not None
    # Best effort, and never in the way: with no embedding model this is a
    # no-op, and remembering something must not fail because an optional model
    # is missing.
    embed_fact(got)
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
    db.execute("DELETE FROM fact_vectors WHERE fact_id = ?", (fact_id,))
    return fact


def delete_all() -> int:
    count = len(all_facts())
    db.execute("DELETE FROM memory_facts")
    db.execute("DELETE FROM fact_vectors")
    return count


def search(text: str) -> list[MemoryFact]:
    like = f"%{text.strip()}%"
    rows = db.query(
        "SELECT * FROM memory_facts WHERE text LIKE ? ORDER BY created_at DESC", (like,)
    )
    return [_row(r) for r in rows]


def relevant(query_text: str, limit: int = 6) -> list[MemoryFact]:
    """Facts worth putting in front of the model for this turn.

    Word overlap plus, where the embedding model is pulled, meaning. Overlap
    alone could not connect "am I allergic to anything" with "peanuts make me
    ill" -- the two sentences share no word longer than two letters, which is
    exactly the shape of the thing a person expects an assistant to remember.

    Both rankings are merged, and the lexical one is not dropped: a fact
    containing the user's landlord's name should still surface when they type
    that name, and a nearest-neighbour search over eight facts is happy to put
    something vaguely thematic ahead of an exact match.
    """
    lexical = _by_overlap(query_text, limit)
    semantic = _by_meaning(query_text, limit)

    top = lexical[:limit] if not semantic else _fuse(lexical, semantic)[:limit]
    for fact in top:
        db.execute("UPDATE memory_facts SET last_used_at = ? WHERE id = ?", (db.now(), fact.id))
    return top


def _by_overlap(query_text: str, limit: int) -> list[MemoryFact]:
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
    return [fact for _, fact in scored[:limit]]


def _by_meaning(query_text: str, limit: int) -> list[MemoryFact]:
    from ..index import embeddings

    vector = embeddings.embed_one(query_text)
    if vector is None:
        return []

    rows = db.query(
        "SELECT fact_id, vector FROM fact_vectors WHERE model = ?", (embeddings.model_name(),)
    )
    ranked = embeddings.rank(vector, [(r["fact_id"], r["vector"]) for r in rows], limit)

    facts = []
    for fact_id, _score in ranked:
        fact = get(fact_id)
        if fact is not None:
            facts.append(fact)
    return facts


def _fuse(*rankings: list[MemoryFact]) -> list[MemoryFact]:
    """Reciprocal rank fusion, same as index/search.py and for the same reason:
    the two scores are on scales that cannot be compared, and only the ordering
    of each is trustworthy."""
    scores: dict[str, float] = {}
    seen: dict[str, MemoryFact] = {}
    for ranking in rankings:
        for rank, fact in enumerate(ranking):
            scores[fact.id] = scores.get(fact.id, 0.0) + 1.0 / (60 + rank + 1)
            seen.setdefault(fact.id, fact)
    return [seen[fid] for fid in sorted(scores, key=lambda f: scores[f], reverse=True)]


def embed_fact(fact: MemoryFact) -> bool:
    """Store a vector for one fact. False when there is no model to ask."""
    from ..index import embeddings

    vector = embeddings.embed_one(fact.text)
    if vector is None:
        return False
    db.execute(
        "INSERT OR REPLACE INTO fact_vectors(fact_id, model, vector) VALUES(?, ?, ?)",
        (fact.id, embeddings.model_name(), embeddings.pack(vector)),
    )
    return True


def embed_missing() -> int:
    """Embed every fact that has no vector for the current model.

    Facts are added one at a time by a person, so this is normally a no-op. It
    matters exactly twice: the first run after the model is pulled, and after
    the model is changed.
    """
    from ..index import embeddings

    if not embeddings.available():
        return 0

    rows = db.query(
        """
        SELECT f.* FROM memory_facts f
        LEFT JOIN fact_vectors v ON v.fact_id = f.id AND v.model = ?
        WHERE v.fact_id IS NULL
        """,
        (embeddings.model_name(),),
    )
    return sum(1 for row in rows if embed_fact(_row(row)))


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

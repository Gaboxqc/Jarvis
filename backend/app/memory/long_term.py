"""Long-term personal memory — REQ-7.

Durable facts the user has explicitly agreed to store. Two rules matter more
than the storage details:

* Nothing is written here silently. Writes go through the Action Gate as a
  consequential action, so the user sees each one as it happens.
* Everything here is reviewable and individually deletable.

Retrieval is word overlap *and* meaning. Overlap alone is accurate enough at
the scale of a personal fact store -- tens to low hundreds of entries -- and it
costs nothing, which is why it is still here; but it cannot connect "am I
allergic to anything" to "peanuts make me ill", and that is the shape of the
thing a person expects an assistant to remember. See `relevant()`.

What meaning costs, and when it is not paid
-------------------------------------------

`relevant()` runs on every turn, before the router is asked anything, so every
millisecond in here is latency the user sits through rather than work done
while they read an answer. The timings instrumentation (app/timings.py) found
it was 350ms of every single turn -- 391ms with an empty fact store -- and
essentially all of it was one Ollama round trip to embed the user's message:

    facts    relevant()    _by_overlap    _by_meaning
        0        391.5ms          0.3ms        375.3ms
        8        362.9ms          0.4ms        373.8ms
       50        362.1ms          0.3ms        373.7ms
      200        377.4ms          1.5ms        406.8ms

The flatness of that table is the finding. The embedding is the whole cost, it
does not care how many facts there are, and it was paid identically for a store
with two hundred facts and for one with none -- where the only possible answer
was an empty list. So it is not paid for that case any more: `_by_meaning()`
looks at what there is to rank against first, and asks Ollama nothing when the
answer cannot depend on it.

What was considered and rejected, measured rather than assumed:

* *Skip the embed while the store is small; overlap is good enough there.* No.
  A personal fact store never leaves that size, so a size threshold above zero
  turns semantic recall off permanently for almost everybody. One fact is
  enough to need it, and test_fact_recall.py pins that.
* *Cache the query vector, in case the same text is embedded twice in a turn.*
  Measured instead: on a real documents turn the recall embedded "what do my
  documents say about the security payment" and the skill embedded "security
  payment" -- the router paraphrases, so an exact-text cache would have hit
  nothing and cost memory to do it.
* *Overlap the embed with the routing call.* There is nothing to overlap it
  with. The facts go into the system prompt that routing is given, so routing
  cannot start until they are known. The turns where the embed is pure waste
  are better skipped than hidden.
"""

from __future__ import annotations

import re
import threading
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
    _invalidate()
    return fact


def delete_all() -> int:
    count = len(all_facts())
    db.execute("DELETE FROM memory_facts")
    db.execute("DELETE FROM fact_vectors")
    _invalidate()
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
    _touch(top)
    return top


def _touch(facts: list[MemoryFact]) -> None:
    """Stamp `last_used_at` on the facts this turn used.

    One statement rather than one per fact. This runs on every single turn, and
    `db.execute()` commits each call -- so recalling six facts was six commits
    before the model had even been asked anything. With synchronous=NORMAL that
    is cheap and it was never free, and the loop it replaces grew with the
    limit for no reason: the rows are known up front.
    """
    if not facts:
        return
    placeholders = ",".join("?" for _ in facts)
    db.execute(
        f"UPDATE memory_facts SET last_used_at = ? WHERE id IN ({placeholders})",
        (db.now(), *[fact.id for fact in facts]),
    )


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

    ids, matrix = _resident_vectors()
    if matrix is None:
        return []

    vector = embeddings.embed_one(query_text)
    if vector is None:
        return []
    if int(vector.shape[0]) != int(matrix.shape[1]):
        # The stored vectors and the query came from different models, under one
        # model name. Dropped rather than compared: a similarity between two
        # widths is not a weaker answer, it is a different arithmetic.
        return []

    ranked = embeddings.rank_matrix(vector, matrix, ids, limit)

    facts = []
    for fact_id, _score in ranked:
        fact = get(fact_id)
        if fact is not None:
            facts.append(fact)
    return facts


# -- the resident fact vectors --------------------------------------------
#
# Same mechanism as index/search.py's resident matrix, and a much smaller one.
# `fact_vectors` was read in full on every turn, before routing, and the matrix
# it rebuilt was identical every time until the user remembered something new.
# Measured on this machine, 768 float32 dimensions:
#
#      facts      sql    sql+rank      held
#          0   0.24ms      0.23ms    0.00MB
#          8   0.36ms      0.36ms    0.02MB
#         50   0.33ms      0.44ms    0.15MB
#        200   0.66ms      0.76ms    0.59MB
#      2,000  10.83ms     14.48ms    5.86MB
#     20,000 126.20ms    252.01ms   58.59MB
#
# Which is the honest reason this is not sold as a latency fix. At the scale
# this module's own docstring describes -- tens to low hundreds of facts -- the
# read costs 0.3 to 0.8ms, and one read of the generation counter costs 0.3ms,
# so holding the matrix saves almost exactly nothing. Three things make it
# worth having anyway:
#
#   * the size is the user's to grow and nothing here bounds it. At 2,000 facts
#     the per-turn read is already 14ms and at 20,000 it is 252ms, and the code
#     that pays it is in front of every message.
#   * `relevant()` runs before routing on every turn, so whatever it costs is
#     latency the user sits through rather than work done while they read.
#   * it is what makes "is there anything to rank against" free, which is what
#     lets the 318ms embed be skipped entirely. That is the real win, and it is
#     in `_by_meaning` above rather than here.
#
# None of index/search.py's paging, budget or streaming fallback is reused.
# Those exist for a corpus of twenty thousand chunks that may not fit in 64MB;
# a personal fact store at 20,000 entries -- three orders of magnitude past
# what anyone has -- is 59MB read in one statement in 126ms. Copying the
# machinery would have been more code defending against a case the numbers
# above say does not arise.

_lock = threading.Lock()
_cache: dict[str, Any] | None = None


def _generation() -> int:
    row = db.query_one("SELECT value FROM index_state WHERE key = 'fact_vectors_generation'")
    return int(row["value"]) if row else 0


def _invalidate() -> None:
    """Drop the resident matrix.

    Not what keeps the cache honest -- the trigger-backed counter is -- but a
    test that has just rewritten this table by hand has somewhere to say so,
    and the writes in this module can save the next turn the counter read.
    """
    global _cache
    with _lock:
        _cache = None


def _resident_vectors() -> tuple[list[str], Any]:
    """The fact vectors as one matrix, or `(…, None)` when there are none.

    Keyed on the database file as well as the model and the generation. The
    counter is a small integer that starts again at 0 in a fresh database, and
    the test suite points this process at a new one per test -- without the
    path in the key, a cache built in one test is indistinguishable from a
    valid one in the next, and would answer with fact ids that no longer exist.
    In the app the path never changes and this costs a dictionary comparison.
    """
    global _cache

    from ..index import embeddings

    key = (str(db.db_path()), embeddings.model_name(), _generation())

    with _lock:
        cached = _cache
        if cached is not None and cached["key"] == key:
            return cached["ids"], cached["matrix"]

    ids, matrix = _load_vectors(embeddings)
    with _lock:
        _cache = {"key": key, "ids": ids, "matrix": matrix}
    return ids, matrix


def _load_vectors(embeddings: Any) -> tuple[list[str], Any]:
    """Read every stored vector for the current model into one matrix.

    The width comes from the first row rather than from the query vector, which
    is how index/search.py decides it. It has to: the whole point of loading
    first is to answer "is there anything here at all" *before* paying for an
    embedding, so there is no query vector yet to take a width from. Rows that
    disagree with the first one are dropped by `to_matrix`, and `_by_meaning`
    rejects a query of a different width than the matrix it got back -- so a
    mixed table still cannot produce a cross-model comparison.
    """
    rows = db.query(
        "SELECT fact_id, vector FROM fact_vectors WHERE model = ?", (embeddings.model_name(),)
    )
    if not rows:
        return [], None

    width = len(rows[0]["vector"]) // 4  # float32
    if width <= 0:
        return [], None
    return embeddings.to_matrix([(r["fact_id"], r["vector"]) for r in rows], width)


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
    _invalidate()
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

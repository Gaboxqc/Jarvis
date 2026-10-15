"""Retrieval — REQ-16.

The module db.py's schema comment promised: "Semantic retrieval can be layered
on later behind index/search.py without touching anything above it." This is it,
and the promise held — `documents.search` and `system.find_files` call the same
function they always did.

Hybrid, not replaced
--------------------

BM25 stays. Keyword search is not a worse version of semantic search; it is
better at a different question, and the two fail in opposite directions:

    "how much was the deposit"     lexical finds nothing if the lease says
                                   "security payment"; semantic finds it
    "invoice 2024-118"             semantic drifts to every invoice ever;
                                   lexical lands on the one with that number

An assistant asked about someone's own documents gets both kinds of question,
often in the same sentence, so both rankings are produced and merged.

Reciprocal rank fusion does the merging: each result scores `1 / (K + rank)` in
each list it appears in, and the scores add. It is used because it needs no
calibration between the two scales -- BM25 is an unbounded negative and cosine
is a bounded positive, and any attempt to weight them against each other
directly is a constant somebody has to tune and nobody can defend. RRF only
reads the ordering, so there is nothing to tune, and a document both rankings
liked wins over one that either loved alone. K=60 is the value from the original
paper and there is no reason here to think we know better.

When the embedding model is not pulled -- which is every fresh install -- the
semantic list is empty, RRF over one list preserves that list's order exactly,
and this returns precisely what BM25 returned before any of it existed.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from .. import db
from . import embeddings, store

log = logging.getLogger(__name__)

# From the paper. Large enough that the top few ranks are not wildly separated,
# small enough that rank 1 still beats rank 20.
RRF_K = 60

# How deep each ranking goes before fusing. Wider than the answer, because a
# result that is fourth in both lists should be able to beat one that is first
# in a single list, and it cannot do that if it was never in the candidate set.
CANDIDATES = 24


def search(query: str, limit: int = 6) -> list[store.Hit]:
    """Chunks for a natural-language question, best first."""
    lexical = store.search(query, limit=CANDIDATES)
    semantic = _semantic(query, limit=CANDIDATES)

    if not semantic:
        return lexical[:limit]

    fused = _fuse([lexical, semantic])
    return fused[:limit]


def _semantic(query: str, limit: int) -> list[store.Hit]:
    vector = embeddings.embed_one(query)
    if vector is None:
        return []

    ranked = _rank_against_index(vector, limit)
    if not ranked:
        return []

    # One query for the text, not one per hit. The vector table holds the key
    # and the similarity; the chunk itself still lives beside FTS5. The loop
    # this replaced issued one round trip per hit while the comment above it
    # claimed the opposite.
    wanted = [key for key, _ in ranked]
    placeholders = ",".join("(?,?)" for _ in wanted)
    rows = db.query(
        "SELECT path, ordinal, text, section FROM document_chunks "
        f"WHERE (path, ordinal) IN (VALUES {placeholders})",
        tuple(value for key in wanted for value in key),
    )
    by_key = {(row["path"], row["ordinal"]): row for row in rows}

    hits: list[store.Hit] = []
    for key, score in ranked:
        chunk = by_key.get(key)
        if chunk is None:
            # The vector outlived its chunk. Possible between a reindex writing
            # chunks and writing vectors; skipped rather than surfaced as a hit
            # with no text.
            continue
        hits.append(
            store.Hit(
                path=chunk["path"],
                section=chunk["section"],
                text=chunk["text"],
                score=score,
            )
        )
    return hits


# -- the resident index ---------------------------------------------------
#
# Every query used to read every vector out of SQLite, rebuild each one into
# its own small array, and stack them into a matrix -- then throw all of it
# away and do it again for the next query. Measured on this machine:
#
#      chunks     sql     rank    total     read
#         500     8ms      4ms     12ms    1.5MB
#       2,000    35ms     16ms     51ms    6.1MB
#       5,000    61ms     32ms     94ms   15.4MB
#      20,000   422ms    244ms    666ms   61.4MB
#
# Linear in the size of the corpus, inside a turn someone is waiting on, and
# the matrix it rebuilt was identical every time until a document changed.
#
# So it is built once and kept. `generation` comes from a counter the database
# bumps by trigger on any write to chunk_vectors (see db.py), which is what
# makes "has this gone stale" a comparison of one integer rather than a promise
# that every writer remembered to call an invalidate function -- index/store.py
# deletes these rows too, and never goes through this module.

# How much of someone's memory this may hold. 64MB is 21,845 vectors at 768
# float32 dimensions: more than a large personal corpus, and an unremarkable
# line in Task Manager for a process meant to sit in the background all day.
RESIDENT_BUDGET_BYTES = 64 * 1024 * 1024

# Rows per batch when reading vectors. Bounds the peak: without it, building
# the matrix holds the blobs, the per-row arrays and the stacked copy at once,
# which is the spike the old code paid on every query.
BATCH = 2048

_lock = threading.Lock()
_cache: dict[str, Any] | None = None


def _generation() -> int:
    row = db.query_one("SELECT value FROM index_state WHERE key = 'vectors_generation'")
    return int(row["value"]) if row else 0


def _invalidate() -> None:
    """Drop the resident matrix.

    Not what keeps the cache honest -- the generation counter is -- but writes
    from this module can say so directly, so the next search does not pay to
    notice.
    """
    global _cache
    with _lock:
        _cache = None


def _rank_against_index(vector: Any, limit: int) -> list[tuple[tuple[str, int], float]]:
    global _cache

    model = embeddings.model_name()
    generation = _generation()

    with _lock:
        cached = _cache
        fresh = (
            cached is not None
            and cached["model"] == model
            and cached["generation"] == generation
        )
        if fresh and cached is not None:
            if cached["matrix"] is None:
                # Known to be over budget. Streamed, and still bounded.
                return _rank_streaming(vector, model, limit)
            return embeddings.rank_matrix(
                vector, cached["matrix"], cached["keys"], limit
            )

    keys, matrix = _load(model, int(vector.shape[0]))
    with _lock:
        _cache = {
            "model": model,
            "generation": generation,
            "keys": keys,
            "matrix": matrix,
        }

    if matrix is None:
        return _rank_streaming(vector, model, limit)
    return embeddings.rank_matrix(vector, matrix, keys, limit)


def _load(model: str, width: int) -> tuple[list[tuple[str, int]], Any]:
    """Read the vectors into one matrix, or decline if they will not fit.

    Returns `(…, None)` when the corpus is over budget, which is the signal to
    stream instead. That decision is cached like any other, so an over-budget
    index costs one counter read per search rather than a rebuild.

    `width` is the query vector's, not a constant. Sizing this from
    DEFAULT_DIMENSIONS looked equivalent and was not: anything storing a
    different width -- another model, or the eight-dimensional stand-in the
    tests use -- had every row rejected as the wrong size, and the whole thing
    fell through to the streaming path. Correct answers, fast path never once
    taken, tests green. Taking the width from the query means the comparison
    is against the only width that could have produced a score anyway.
    """
    keys: list[tuple[str, int]] = []
    blocks: list[Any] = []
    held = 0

    # The budget is checked as the rows arrive rather than from a COUNT taken
    # first. `model` is not indexed, so counting means a full scan of its own:
    # at twenty thousand chunks that scan cost more than reading every vector,
    # and it was paid on the way to deciding whether to read them. Accumulating
    # instead reads at most one batch past the limit before giving up.
    for batch in _batches(model):
        batch_keys, block = embeddings.to_matrix(batch, width)
        if block is None:
            continue
        held += int(block.nbytes)
        if held > RESIDENT_BUDGET_BYTES:
            log.info(
                "vectors exceed the %dMB resident budget; ranking by streaming",
                RESIDENT_BUDGET_BYTES // (1024 * 1024),
            )
            return [], None
        keys.extend(batch_keys)
        blocks.append(block)

    if not blocks:
        return [], None
    return keys, embeddings.concatenate(blocks)


def _batches(model: str) -> Any:
    """Vectors in groups of BATCH, ordered by the primary key.

    Ordered so the paging is stable: without ORDER BY, SQLite may return rows
    in a different order between two pages of the same scan, and a row can
    then be seen twice or missed entirely.

    Paged by seeking past the last key rather than by OFFSET. OFFSET makes
    SQLite walk and discard every row it has already returned, so reading a
    table in pages costs O(n²/BATCH) -- at twenty thousand chunks that turned
    a 300ms read into 1.65s, which would have made the first search after
    every single reindex slower than the full scan this replaces. The row
    value comparison seeks straight into the (path, ordinal) index instead.
    """
    last: tuple[str, int] | None = None
    while True:
        if last is None:
            rows = db.query(
                "SELECT path, ordinal, vector FROM chunk_vectors WHERE model = ? "
                "ORDER BY path, ordinal LIMIT ?",
                (model, BATCH),
            )
        else:
            rows = db.query(
                "SELECT path, ordinal, vector FROM chunk_vectors "
                "WHERE model = ? AND (path, ordinal) > (?, ?) "
                "ORDER BY path, ordinal LIMIT ?",
                (model, last[0], last[1], BATCH),
            )
        if not rows:
            return
        yield [((row["path"], row["ordinal"]), row["vector"]) for row in rows]
        if len(rows) < BATCH:
            return
        last = (rows[-1]["path"], int(rows[-1]["ordinal"]))


def _rank_streaming(
    vector: Any, model: str, limit: int
) -> list[tuple[tuple[str, int], float]]:
    """Rank an index too large to hold, a batch at a time.

    Peak memory is one batch rather than the whole corpus, so this is still an
    improvement on the code it replaces, which materialised everything.
    """
    best: list[tuple[tuple[str, int], float]] = []
    for batch in _batches(model):
        best.extend(embeddings.rank(vector, batch, limit))
        best.sort(key=lambda pair: pair[1], reverse=True)
        del best[limit:]
    return best


def _fuse(rankings: list[list[store.Hit]]) -> list[store.Hit]:
    scores: dict[tuple[str, str], float] = {}
    best: dict[tuple[str, str], store.Hit] = {}

    for ranking in rankings:
        for rank, hit in enumerate(ranking):
            key = (hit.path, hit.text)
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
            best.setdefault(key, hit)

    order = sorted(scores, key=lambda key: scores[key], reverse=True)
    # The fused score replaces the per-ranking one. Returning a BM25 number for
    # a result that a cosine ranking put on top would be a lie about how it got
    # there, and the callers only use the value for ordering.
    return [
        store.Hit(
            path=best[key].path,
            section=best[key].section,
            text=best[key].text,
            score=scores[key],
        )
        for key in order
    ]


# -- writing --------------------------------------------------------------


def index_chunks(path: str, chunks: list[Any]) -> int:
    """Store vectors for one document's chunks. Returns how many were written.

    Called after the chunks themselves are written. Zero is the normal answer on
    a machine with no embedding model, and it is not a failure -- the document
    is fully searchable by keyword either way.
    """
    if not chunks:
        return 0

    vectors = embeddings.embed([chunk.text for chunk in chunks])
    if vectors is None:
        return 0

    model = embeddings.model_name()
    db.execute("DELETE FROM chunk_vectors WHERE path = ?", (path,))
    connection = db.connect()
    connection.executemany(
        "INSERT OR REPLACE INTO chunk_vectors(path, ordinal, model, vector) VALUES(?, ?, ?, ?)",
        [
            (path, index, model, embeddings.pack(vector))
            for index, vector in enumerate(vectors)
        ],
    )
    connection.commit()
    _invalidate()
    return len(vectors)


def forget(path: str) -> None:
    db.execute("DELETE FROM chunk_vectors WHERE path = ?", (path,))
    _invalidate()


def clear() -> int:
    cursor = db.execute("DELETE FROM chunk_vectors")
    _invalidate()
    return cursor.rowcount or 0


def coverage() -> dict[str, int]:
    """How much of the index has vectors. Shown on the Documents screen so
    "semantic search is on" is a claim with a number behind it."""
    chunks = db.query_one("SELECT COUNT(*) AS c FROM document_chunks")
    vectors = db.query_one(
        "SELECT COUNT(*) AS c FROM chunk_vectors WHERE model = ?", (embeddings.model_name(),)
    )
    return {
        "chunks": int(chunks["c"]) if chunks else 0,
        "embedded": int(vectors["c"]) if vectors else 0,
    }

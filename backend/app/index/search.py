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

    rows = db.query(
        "SELECT path, ordinal, vector FROM chunk_vectors WHERE model = ?",
        (embeddings.model_name(),),
    )
    if not rows:
        return []

    ranked = embeddings.rank(
        vector, [((row["path"], row["ordinal"]), row["vector"]) for row in rows], limit
    )
    if not ranked:
        return []

    # One query for the text rather than one per hit. The vector table holds the
    # key and the similarity; the chunk itself still lives in FTS5.
    hits: list[store.Hit] = []
    for (path, ordinal), score in ranked:
        chunk = db.query_one(
            "SELECT text, path, section FROM document_chunks WHERE path = ? AND ordinal = ?",
            (path, ordinal),
        )
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
    return len(vectors)


def forget(path: str) -> None:
    db.execute("DELETE FROM chunk_vectors WHERE path = ?", (path,))


def clear() -> int:
    cursor = db.execute("DELETE FROM chunk_vectors")
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

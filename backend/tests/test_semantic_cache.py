"""The resident vector index — the cache behind index/search.py.

Semantic search used to read every stored vector out of SQLite on every query,
rebuild each one into its own array and stack them, then throw the lot away:
666ms and 61MB at twenty thousand chunks, inside a turn someone is waiting on.
The matrix is now built once and kept.

A cache is only worth having if it is never wrong, so most of this file is
about staleness rather than speed. The writers are spread across
index/store.py and index/search.py, and the one that matters most --
reindexing an edited document -- replaces a document's chunks with the same
number of chunks, so any scheme that watched row counts would miss it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app import db
from app.index import chunk as chunk_module
from app.index import embeddings, search, store
from .conftest import count_vector_reads, fake_embedder

WIDTH = 8


@pytest.fixture(autouse=True)
def _clean():
    embeddings.reset_cache()
    search._invalidate()
    yield
    embeddings.reset_cache()
    search._invalidate()


AXES = {"deposit": 0, "security": 0, "rent": 1, "monthly": 1, "bicycle": 2}


@pytest.fixture
def embedder(monkeypatch):
    return fake_embedder(monkeypatch, AXES)


def index(path: str, *sections: tuple[str, str]) -> None:
    chunks = [
        chunk_module.Chunk(text=text, section=section, ordinal=i)
        for i, (section, text) in enumerate(sections)
    ]
    store.replace_document(Path(path), title=path, size=1, mtime=1.0, chunks=chunks)


# -- the fast path is actually taken --------------------------------------


def test_the_matrix_is_built_once_and_reused(workspace, embedder, monkeypatch):
    index("C:/docs/lease.txt", ("Deposit", "the security deposit"))
    index("C:/docs/rent.txt", ("Rent", "the monthly rent"))

    reads = count_vector_reads(monkeypatch, "chunk_vectors")
    assert search.search("deposit", limit=3)
    after_first = reads[0]
    assert after_first > 0, "the first search must read the vectors from somewhere"

    for _ in range(5):
        assert search.search("deposit", limit=3)

    # The whole point: five more searches, no further reads.
    assert reads[0] == after_first


def test_the_resident_path_is_the_one_being_used(workspace, embedder):
    """Not the streaming fallback.

    Worth asserting directly: an earlier version sized its budget from a
    768-dimension constant while the tests store eight, so every row was
    rejected as the wrong width and everything quietly fell through to
    streaming. The answers were right and the fast path was dead.
    """
    index("C:/docs/lease.txt", ("Deposit", "the security deposit"))
    search.search("deposit", limit=3)

    assert search._cache is not None
    assert search._cache["matrix"] is not None
    assert search._cache["matrix"].shape == (1, WIDTH)


# -- and it is never stale ------------------------------------------------


def test_editing_a_document_is_noticed_even_with_the_same_chunk_count(
    workspace, embedder
):
    """The case a row count cannot see.

    Reindexing replaces one chunk with one chunk. Count before and count after
    are both 1, so a cache keyed on how many vectors exist would keep serving
    the old text.
    """
    index("C:/docs/lease.txt", ("Deposit", "the security deposit"))
    first = search.search("deposit", limit=3)
    assert "security deposit" in first[0].text

    index("C:/docs/lease.txt", ("Rent", "the monthly rent"))

    again = search.search("deposit", limit=3)
    assert all("security deposit" not in hit.text for hit in again)


def test_a_delete_that_bypasses_this_module_is_still_noticed(workspace, embedder):
    """index/store.py removes these rows without going through search.py.

    This is why the generation counter is a database trigger rather than an
    invalidate() call: a cache that trusted its own writers would hand back a
    document that had been deleted underneath it.
    """
    index("C:/docs/lease.txt", ("Deposit", "the security deposit"))
    index("C:/docs/rent.txt", ("Rent", "the monthly rent"))
    assert search.search("deposit", limit=3)

    store.forget_document(str(Path("C:/docs/lease.txt")))

    hits = search.search("deposit", limit=5)
    assert all("lease.txt" not in hit.path for hit in hits)


def test_a_vector_rewritten_behind_this_module_is_noticed(workspace, embedder):
    """The case nothing else catches.

    The other staleness tests pass even with the generation counter disabled,
    and it is worth being precise about why rather than counting them as
    cover. Writes through index/search.py drop the matrix themselves; deletes
    through index/store.py take the chunk text with them, and a vector whose
    chunk is gone is already skipped when the text is looked up. Both are
    masked.

    What is not masked is a vector changing while its chunk stays exactly
    where it is. That is a backfill, a re-embedding under a new model, a
    repair job -- anything that touches the table without going through this
    module. Here it is done in SQL directly, which is the one thing every
    such writer has in common.
    """
    # The lease is deliberately not a pure "deposit" vector -- it carries some
    # bicycle too -- so the rewritten vector below can beat it outright rather
    # than tie with it. A test whose outcome rests on how ties are broken is
    # testing the tie-break.
    index("C:/docs/lease.txt", ("Deposit", "the security deposit bicycle"))
    index("C:/docs/bike.txt", ("Bike", "the bicycle"))

    # Asserted on the semantic ranking rather than through search(), which
    # fuses it with BM25. Lexical search puts the lease first for the word
    # "deposit" whatever the vectors say, and that would mask the very thing
    # this is checking.
    generation = search._generation()
    before = search._semantic("deposit", 2)
    assert "security deposit" in before[0].text

    # Point the bicycle chunk's vector straight at the deposit axis, leaving
    # its text exactly where it is. Nothing in this module did it, and nothing
    # told the cache.
    moved = embeddings.normalise(np.eye(WIDTH, dtype=np.float32)[0])
    db.execute(
        "UPDATE chunk_vectors SET vector = ? WHERE path = ?",
        (embeddings.pack(moved), str(Path("C:/docs/bike.txt"))),
    )

    assert search._generation() > generation, "the trigger did not fire"

    after = search._semantic("deposit", 2)
    assert "bicycle" == after[0].text.replace("the ", ""), (
        "the rewritten vector did not change the ranking: "
        "the cache is serving a stale matrix"
    )


def test_the_generation_counter_moves_on_every_kind_of_write(workspace, embedder):
    start = search._generation()

    index("C:/docs/lease.txt", ("Deposit", "the security deposit"))
    after_insert = search._generation()
    assert after_insert > start

    search.forget(str(Path("C:/docs/lease.txt")))
    after_delete = search._generation()
    assert after_delete > after_insert


# -- the fallback for an index too large to hold --------------------------


def test_an_over_budget_index_streams_and_still_ranks(
    workspace, embedder, monkeypatch
):
    """Exercised on purpose, by shrinking the budget rather than growing the
    corpus. A fallback nothing ever runs is not a fallback."""
    monkeypatch.setattr(search, "RESIDENT_BUDGET_BYTES", 1)
    monkeypatch.setattr(search, "BATCH", 1)

    index("C:/docs/lease.txt", ("Deposit", "the security deposit"))
    index("C:/docs/rent.txt", ("Rent", "the monthly rent"))

    hits = search.search("deposit", limit=3)
    assert hits
    assert "security deposit" in hits[0].text
    # Declined to hold it, and said so rather than pretending.
    assert search._cache is not None
    assert search._cache["matrix"] is None


def test_streaming_and_resident_agree(workspace, embedder, monkeypatch):
    for i in range(12):
        index(f"C:/docs/d{i}.txt", ("S", "the security deposit" if i % 2 else "monthly rent"))

    resident = [(hit.path, round(hit.score, 6)) for hit in search.search("deposit", limit=5)]

    search._invalidate()
    monkeypatch.setattr(search, "RESIDENT_BUDGET_BYTES", 1)
    monkeypatch.setattr(search, "BATCH", 3)
    streamed = [(hit.path, round(hit.score, 6)) for hit in search.search("deposit", limit=5)]

    assert resident == streamed
"""Hybrid retrieval — REQ-16, REQ-27, REQ-30.

Document search was BM25 and memory recall was token overlap, so both failed on
the same thing: the word the user reaches for is often not the word the document
used. "How much was the deposit" finds nothing in a lease that says "security
payment", and "am I allergic to anything" cannot reach a fact that says "peanuts
make me ill" -- the two share no word longer than two letters.

Two properties are worth more than the ranking quality here, and they are what
most of this file is about:

  with no embedding model pulled -- which is every fresh install -- search must
      behave exactly as it did before any of this existed
  a model that goes away mid-session must not take search with it

The embedding calls are stubbed. A test that needs Ollama running and a 274MB
model pulled is a test that gets skipped, and a skipped test is not a guard.
`test_semantic_search_slow.py` holds the one that does use the real thing.
"""

from __future__ import annotations

import numpy as np
import pytest

from app import db
from app.index import chunk as chunk_module
from app.index import embeddings, search, store
from app.memory import long_term


@pytest.fixture(autouse=True)
def _clear_cache():
    embeddings.reset_cache()
    yield
    embeddings.reset_cache()


class FakeEmbedder:
    """Deterministic vectors from a tiny hand-built vocabulary.

    Words that mean the same thing are given the same axis, which is the whole
    property a real embedding model provides and the only one these tests read.
    """

    AXES = {
        # deposit / security payment: the lease example
        "deposit": 0, "security": 0, "bond": 0,
        # allergy: the memory example
        "allergic": 1, "allergy": 1, "peanuts": 1, "nuts": 1,
        "walnuts": 5,
        # rent
        "rent": 2, "monthly": 2,
        # noise
        "bicycle": 3, "weather": 4,
    }
    WIDTH = 8

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, texts: list[str]) -> list[np.ndarray] | None:
        # Same contract as the real one: None when there is no model. A fake
        # that ignores this lets tests "turn the model off" and keep getting
        # vectors, which is how the no-model tests would pass by accident.
        if not embeddings.available():
            return None
        self.calls += 1
        out = []
        for text in texts:
            vector = np.zeros(self.WIDTH, dtype=np.float32)
            for word in text.lower().replace(".", " ").replace(",", " ").split():
                axis = self.AXES.get(word.strip("?!'\""))
                if axis is not None:
                    vector[axis] += 1.0
            out.append(embeddings.normalise(vector))
        return out


@pytest.fixture
def embedder(monkeypatch):
    fake = FakeEmbedder()
    monkeypatch.setattr(embeddings, "available", lambda: True)
    monkeypatch.setattr(embeddings, "embed", fake)
    monkeypatch.setattr(embeddings, "model_name", lambda: "fake-embed")
    return fake


def index(path: str, *sections: tuple[str, str]) -> None:
    from pathlib import Path

    chunks = [
        chunk_module.Chunk(text=text, section=section, ordinal=i)
        for i, (section, text) in enumerate(sections)
    ]
    store.replace_document(Path(path), title=path, size=1, mtime=1.0, chunks=chunks)


# -- the point of the exercise --------------------------------------------


def test_a_document_is_found_by_meaning_when_it_shares_no_words(workspace, embedder):
    index("lease.md", ("Payments", "The security payment is 1200 and is refundable."))

    hits = search.search("how much was the deposit")

    assert hits, "a lexical-only index finds nothing here, which is the whole problem"
    assert "security payment" in hits[0].text


def test_an_exact_word_still_wins_when_it_is_there(workspace, embedder):
    """Semantic retrieval must not make keyword search worse. An invoice number
    is the case nearest-neighbour search is bad at, and it is a case people
    actually type."""
    index("a.md", ("Notes", "The security payment covers damage."))
    index("b.md", ("Notes", "Invoice 2024-118 was paid in March."))

    hits = search.search("invoice 2024-118")

    assert hits[0].path.endswith("b.md")


# -- what happens with no model -------------------------------------------


def test_with_no_model_search_is_exactly_what_it_was(workspace, monkeypatch):
    monkeypatch.setattr(embeddings, "available", lambda: False)
    index("lease.md", ("Payments", "The deposit is 1200."))
    index("bike.md", ("Notes", "The bicycle needs a new chain."))

    lexical = store.search("deposit", limit=6)
    hybrid = search.search("deposit", limit=6)

    assert [h.path for h in hybrid] == [h.path for h in lexical]
    assert hybrid[0].path.endswith("lease.md")


def test_a_question_that_needs_meaning_finds_nothing_without_a_model(workspace, monkeypatch):
    """Stated plainly rather than hidden: this is the capability the download
    buys, and pretending otherwise would make the setting look broken."""
    monkeypatch.setattr(embeddings, "available", lambda: False)
    index("lease.md", ("Payments", "The security payment is 1200."))

    assert search.search("how much was the deposit") == []


def test_indexing_without_a_model_still_indexes(workspace, monkeypatch):
    monkeypatch.setattr(embeddings, "available", lambda: False)

    index("lease.md", ("Payments", "The deposit is 1200."))

    assert store.search("deposit")
    assert db.query_one("SELECT COUNT(*) AS c FROM chunk_vectors")["c"] == 0


def test_a_model_that_disappears_mid_session_does_not_take_search_with_it(workspace, embedder,
                                                                          monkeypatch):
    index("lease.md", ("Payments", "The deposit is 1200."))
    # Ollama stopped. Vectors are still in the table; nothing can embed a query.
    monkeypatch.setattr(embeddings, "embed", lambda texts: None)

    hits = search.search("deposit")

    assert hits and hits[0].path.endswith("lease.md")


# -- keeping the two tables in step ---------------------------------------


def test_reindexing_replaces_the_vectors_rather_than_adding_to_them(workspace, embedder):
    index("lease.md", ("Payments", "The deposit is 1200."))
    index("lease.md", ("Payments", "The deposit is 1400."), ("Term", "Twelve months."))

    rows = db.query("SELECT ordinal FROM chunk_vectors WHERE path LIKE '%lease.md'")
    assert len(rows) == 2


def test_forgetting_a_document_forgets_its_vectors(workspace, embedder):
    index("lease.md", ("Payments", "The deposit is 1200."))
    path = db.query_one("SELECT path FROM chunk_vectors")["path"]

    store.forget_document(path)

    assert db.query_one("SELECT COUNT(*) AS c FROM chunk_vectors")["c"] == 0


def test_clearing_the_index_clears_the_vectors(workspace, embedder):
    index("lease.md", ("Payments", "The deposit is 1200."))

    store.clear()

    assert db.query_one("SELECT COUNT(*) AS c FROM chunk_vectors")["c"] == 0


def test_a_vector_from_another_model_is_ignored_rather_than_compared(workspace, embedder):
    """Comparing across models is not wrong in a way that raises. It is wrong in
    a way that ranks confidently and badly."""
    index("lease.md", ("Payments", "The security payment is 1200."))
    db.execute("UPDATE chunk_vectors SET model = 'something-else'")

    assert search.search("how much was the deposit") == []


def test_coverage_reports_what_is_actually_embedded(workspace, embedder):
    index("lease.md", ("Payments", "One."), ("Term", "Two."))

    assert search.coverage() == {"chunks": 2, "embedded": 2}


# -- memory ----------------------------------------------------------------


def test_a_fact_is_recalled_by_meaning(workspace, embedder):
    long_term.add("Peanuts make me ill")
    long_term.add("The bicycle needs a new chain")

    recalled = long_term.relevant("am I allergic to anything")

    assert recalled
    assert "Peanuts" in recalled[0].text


def test_recall_still_works_with_no_model(workspace, monkeypatch):
    monkeypatch.setattr(embeddings, "available", lambda: False)
    long_term.add("Peanuts make me ill")

    assert long_term.relevant("peanuts")[0].text == "Peanuts make me ill"


def test_deleting_a_fact_deletes_its_vector(workspace, embedder):
    fact = long_term.add("Peanuts make me ill")

    long_term.delete(fact.id)

    assert db.query_one("SELECT COUNT(*) AS c FROM fact_vectors")["c"] == 0


def test_restating_a_fact_re_embeds_it(workspace, embedder):
    """The wording changed, so the old vector describes text that no longer
    exists. A stale vector is worse than none, because it still ranks."""
    long_term.add("Peanuts make me ill")
    before = db.query_one("SELECT vector FROM fact_vectors")["vector"]

    long_term.add("Peanuts make me ill and so do walnuts")

    after = db.query_one("SELECT vector FROM fact_vectors")["vector"]
    assert db.query_one("SELECT COUNT(*) AS c FROM fact_vectors")["c"] == 1
    assert after != before


def test_backfill_embeds_only_what_is_missing(workspace, embedder, monkeypatch):
    """The path taken the first time the model is pulled on an install that
    already has memories."""
    monkeypatch.setattr(embeddings, "available", lambda: False)
    long_term.add("Peanuts make me ill")
    long_term.add("The bicycle needs a new chain")
    assert db.query_one("SELECT COUNT(*) AS c FROM fact_vectors")["c"] == 0

    monkeypatch.setattr(embeddings, "available", lambda: True)
    assert long_term.embed_missing() == 2
    assert long_term.embed_missing() == 0


# -- fusion ----------------------------------------------------------------


def test_something_both_rankings_liked_beats_something_only_one_loved():
    """The reason for RRF. A result fourth in both lists should be able to beat
    one that is first in a single list."""
    def hit(name: str) -> store.Hit:
        return store.Hit(path=f"{name}.md", section="", text=name, score=0.0)

    # Disjoint filler. Anything appearing in both lists scores twice, which is
    # the effect under test -- so only `both` may.
    fused = search._fuse([
        [hit("only"), hit("a1"), hit("a2"), hit("both")],
        [hit("b1"), hit("b2"), hit("b3"), hit("both")],
    ])

    assert fused[0].path == "both.md"


def test_fusion_over_one_ranking_preserves_it_exactly():
    """Which is what makes the no-model case free."""
    hits = [store.Hit(path=f"{i}.md", section="", text=str(i), score=0.0) for i in range(5)]

    assert [h.path for h in search._fuse([hits])] == [h.path for h in hits]

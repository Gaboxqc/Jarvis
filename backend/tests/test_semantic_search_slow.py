"""Hybrid retrieval against the real embedding model — REQ-16.

Everything in test_semantic_search.py runs against a hand-built fake, which is
right: those tests are about wiring, fallback and bookkeeping, and none of them
should need Ollama running or 274MB pulled.

This is the one that needs both, and it is here because a fake with a vocabulary
of eleven words cannot answer the question that actually matters — whether a
real model puts "the security payment is 1200" in front of "the bicycle needs a
chain" when someone asks about a deposit. Every fake I could write would agree
with me by construction.

Marked slow and skipped when the model is absent, in step with the speech tests.
Run it with:

    ollama pull nomic-embed-text
    python -m pytest tests/test_semantic_search_slow.py
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.index import chunk as chunk_module
from app.index import embeddings, search, store
from app.memory import long_term
from app.settings import reset_config_cache

pytestmark = pytest.mark.slow


@pytest.fixture
def real_model(workspace, config_file):
    """Turn semantic search back on, then use the real model or skip.

    conftest switches it off for the whole suite, so that no ordinary test
    depends on whether the developer happens to have pulled a 274MB model. This
    file is the exception, and it has to say so explicitly — without the
    re-enable it would skip on every machine including one where the model is
    present, which is a guard that can never fire.
    """
    raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    raw["documents"]["semantic_search"] = True
    config_file.write_text(yaml.safe_dump(raw), encoding="utf-8")
    reset_config_cache()
    embeddings.reset_cache()

    if not embeddings.available():
        pytest.skip(f"{embeddings.model_name()} is not pulled")
    yield
    embeddings.reset_cache()


def index(path: str, section: str, text: str) -> None:
    store.replace_document(
        Path(path),
        title=path,
        size=1,
        mtime=1.0,
        chunks=[chunk_module.Chunk(text=text, section=section, ordinal=0)],
    )


def test_the_model_answers_and_the_vectors_are_the_width_we_store(real_model):
    vector = embeddings.embed_one("a sentence")

    assert vector is not None
    assert vector.shape[0] == embeddings.DEFAULT_DIMENSIONS
    # Normalised on the way in, so retrieval is a dot product.
    assert abs(float((vector * vector).sum()) - 1.0) < 1e-4


def test_a_deposit_question_finds_a_lease_that_says_security_payment(real_model):
    index("lease.md", "Payments", "The security payment is 1200 and is returned at the end.")
    index("bike.md", "Notes", "The bicycle needs a new chain before winter.")
    index("recipe.md", "Method", "Fold the egg whites into the batter gently.")

    hits = search.search("how much was the deposit", limit=3)

    assert hits, "nothing came back at all"
    assert hits[0].path.endswith("lease.md"), f"ranked {[h.filename for h in hits]}"


def test_an_allergy_question_reaches_a_fact_about_peanuts(real_model):
    long_term.add("Peanuts make me ill")
    long_term.add("The bicycle needs a new chain")
    long_term.add("My landlord is called Marta")

    recalled = long_term.relevant("am I allergic to anything", limit=2)

    assert recalled
    assert "Peanuts" in recalled[0].text


def test_an_exact_identifier_still_ranks_first(real_model):
    """The case nearest-neighbour search is worst at, and the reason BM25 stays."""
    index("a.md", "Notes", "The security payment covers any damage to the property.")
    index("b.md", "Notes", "Invoice 2024-118 was paid in March.")

    hits = search.search("invoice 2024-118", limit=2)

    assert hits[0].path.endswith("b.md"), f"ranked {[h.filename for h in hits]}"

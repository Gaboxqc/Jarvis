"""Memory recall, and what it costs before routing — REQ-7, REQ-27.

`long_term.relevant()` runs on every turn, before the router is asked anything,
so everything it does is latency the user sits through. The timings
instrumentation measured it at 350ms of every single turn, essentially all of
it one Ollama round trip to embed the user's message -- paid identically
whether the fact store held two hundred facts or none at all.

Two things changed, and this file is about both:

  the vectors are held resident, keyed on a generation counter the database
      bumps by trigger, so the table is not re-read on every turn

  the embed is skipped when there is provably nothing to rank against, which
      is every turn on an install where nobody has asked Kai to remember
      anything yet

The second is the one with the half-second in it, and it is also the one that
could quietly turn semantic recall off for everyone. So most of what follows is
about the cases where it must still happen.
"""

from __future__ import annotations

import numpy as np
import pytest

from app import db
from app.index import embeddings
from app.memory import long_term

WIDTH = 8


@pytest.fixture(autouse=True)
def _clean():
    embeddings.reset_cache()
    long_term._invalidate()
    yield
    embeddings.reset_cache()
    long_term._invalidate()


class Embedder:
    """One axis per idea, so similarity is deliberate rather than random.

    Counts its calls, because "did this make an HTTP request to Ollama" is the
    question half this file asks.
    """

    AXES = {
        "allergic": 0, "allergy": 0, "peanuts": 0, "nuts": 0,
        "landlord": 1, "hendricks": 1, "rent": 1,
        "bicycle": 2, "chain": 2,
    }

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, texts: list[str]) -> list[np.ndarray] | None:
        if not embeddings.available():
            return None
        self.calls += 1
        out = []
        for text in texts:
            vector = np.zeros(WIDTH, dtype=np.float32)
            for word in text.lower().replace(".", " ").split():
                axis = self.AXES.get(word.strip("?!,'\""))
                if axis is not None:
                    vector[axis] += 1.0
            out.append(embeddings.normalise(vector))
        return out


@pytest.fixture
def embedder(monkeypatch):
    fake = Embedder()
    monkeypatch.setattr(embeddings, "available", lambda: True)
    monkeypatch.setattr(embeddings, "embed", fake)
    monkeypatch.setattr(embeddings, "model_name", lambda: "fake-embed")
    return fake


def count_vector_reads(monkeypatch) -> list[int]:
    """Count SELECTs that pull fact vectors, which is the per-turn read."""
    seen = [0]
    original = db.query

    def counting(sql, params=()):
        if "FROM fact_vectors" in sql and "vector" in sql.split("FROM")[0]:
            seen[0] += 1
        return original(sql, params)

    monkeypatch.setattr(db, "query", counting)
    return seen


# -- the embed that is not paid for ---------------------------------------


def test_an_empty_fact_store_does_not_embed_the_question(workspace, embedder):
    """The half-second this was all about.

    A fresh install has no facts and, if the user has pulled the embedding
    model for document search, a working embedder. Every turn embedded the
    user's message and ranked it against an empty table: 350ms median on a real
    machine, in front of every message, for a list that could only come back
    empty.
    """
    assert long_term.all_facts() == []

    assert long_term.relevant("am I allergic to anything") == []

    assert embedder.calls == 0, "the question was embedded with nothing to rank it against"


def test_facts_with_no_vectors_do_not_embed_either(workspace, embedder):
    """The other way to have nothing to rank against.

    Facts remembered before the model was pulled have no vectors until
    `embed_missing()` runs. Ranking against them is not merely useless, it is
    impossible -- and lexical overlap still answers, which is what this install
    had before embeddings existed at all.
    """
    db.execute(
        "INSERT INTO memory_facts(id, text, category, created_at) VALUES(?, ?, 'fact', ?)",
        ("no-vector", "Peanuts make me ill", db.now()),
    )
    assert db.query_one("SELECT COUNT(*) AS c FROM fact_vectors")["c"] == 0

    recalled = long_term.relevant("peanuts")

    assert [fact.text for fact in recalled] == ["Peanuts make me ill"]
    assert embedder.calls == 0


def test_the_skip_does_not_survive_the_first_remembered_fact(workspace, embedder):
    """The failure that would have been invisible.

    Skipping the embed is only safe if "there is nothing to rank against" stops
    being true the moment something is. A cache that answered "empty" for the
    rest of the process would leave semantic recall switched off for every
    user, with every lexical test still passing -- which is exactly how the
    resident matrix in index/search.py was once dead for a whole release.
    """
    assert long_term.relevant("am I allergic to anything") == []

    long_term.add("Peanuts make me ill")
    long_term.add("The bicycle needs a new chain")

    recalled = long_term.relevant("am I allergic to anything")

    assert recalled, "nothing was recalled by meaning"
    assert "Peanuts" in recalled[0].text, "the question was not embedded after all"


def test_a_question_that_needs_meaning_still_needs_the_model(workspace, embedder):
    """The test the skip had to not break.

    "am I allergic to anything" and "peanuts make me ill" share no word longer
    than two letters, so overlap cannot connect them. This is the capability
    embeddings were added for, and no amount of skipping may remove it.
    """
    long_term.add("Peanuts make me ill")
    embedder.calls = 0

    recalled = long_term.relevant("am I allergic to anything")

    assert recalled and "Peanuts" in recalled[0].text
    assert embedder.calls == 1, "a store with vectors must still embed the question"


def test_the_store_size_is_not_what_decides(workspace, embedder):
    """Deliberately not a threshold, and worth pinning as a decision.

    "Skip the embed while the store is small, lexical is good enough there" was
    the obvious optimisation and it is wrong. A personal fact store never
    leaves that size -- this module's own docstring says tens to low hundreds
    -- so any size threshold above one turns semantic recall off permanently
    for almost every user. One fact is enough to need it.
    """
    long_term.add("Peanuts make me ill")

    assert len(long_term.all_facts()) == 1
    recalled = long_term.relevant("am I allergic to anything")
    assert recalled and "Peanuts" in recalled[0].text


# -- the resident matrix --------------------------------------------------


def test_the_vectors_are_read_once_and_then_held(workspace, embedder, monkeypatch):
    long_term.add("Peanuts make me ill")
    long_term.add("My landlord is called Hendricks")

    reads = count_vector_reads(monkeypatch)
    assert long_term.relevant("am I allergic to anything")
    after_first = reads[0]
    assert after_first > 0, "the first recall must read the vectors from somewhere"

    for _ in range(5):
        assert long_term.relevant("am I allergic to anything")

    assert reads[0] == after_first, "the table is still being read every turn"


def test_the_resident_path_is_the_one_being_used(workspace, embedder):
    """Not a quietly dead fast path.

    index/search.py's equivalent sized its budget from a 768-dimension
    constant while its tests stored eight, so every row was rejected as the
    wrong width and the whole thing fell through to a slow path with the tests
    green. Here the width is taken from the stored rows, and this says so.
    """
    long_term.add("Peanuts make me ill")
    long_term.relevant("am I allergic to anything")

    assert long_term._cache is not None
    assert long_term._cache["matrix"] is not None
    assert long_term._cache["matrix"].shape == (1, WIDTH)


def test_a_vector_rewritten_behind_this_module_is_noticed(workspace, embedder):
    """Why the counter is a trigger and not an invalidate() call.

    A re-embedding under a new model, a backfill, a repair -- anything that
    writes this table without going through this module. Done here in SQL
    directly, which is the one thing every such writer has in common.

    Asserted through `_by_meaning` rather than `relevant()`, which fuses it
    with word overlap: a lexical ranking that lands on the same fact either way
    would mask the thing being checked. And the peanut fact deliberately
    carries some bicycle too, so the rewritten vector below can beat it
    outright -- a test whose outcome rests on how ties are broken is testing
    the tie-break.
    """
    long_term.add("Peanuts make me ill bicycle")
    landlord = long_term.add("My landlord is called Hendricks")

    before = long_term._by_meaning("allergic", 2)
    assert "Peanuts" in before[0].text
    generation = long_term._generation()

    # Point the landlord fact's vector straight at the allergy axis, leaving
    # its text exactly where it is. Nothing in this module did it, and nothing
    # told the cache.
    moved = embeddings.normalise(np.eye(WIDTH, dtype=np.float32)[0])
    db.execute(
        "UPDATE fact_vectors SET vector = ? WHERE fact_id = ?",
        (embeddings.pack(moved), landlord.id),
    )
    assert long_term._generation() > generation, "the trigger did not fire"

    after = long_term._by_meaning("allergic", 2)
    assert "landlord" in after[0].text, (
        "the rewritten vector did not change the ranking: "
        "the cache is serving a stale matrix"
    )


def test_forgetting_a_fact_drops_it_from_the_matrix(workspace, embedder):
    peanuts = long_term.add("Peanuts make me ill")
    long_term.add("My landlord is called Hendricks")
    assert long_term.relevant("am I allergic to anything")

    long_term.delete(peanuts.id)

    recalled = long_term.relevant("am I allergic to anything")
    assert all("Peanuts" not in fact.text for fact in recalled)


def test_the_counter_moves_on_every_kind_of_write(workspace, embedder):
    start = long_term._generation()

    fact = long_term.add("Peanuts make me ill")
    after_insert = long_term._generation()
    assert after_insert > start

    long_term.add("Peanuts make me ill and so do walnuts")  # a restate re-embeds
    after_update = long_term._generation()
    assert after_update > after_insert

    long_term.delete(fact.id)
    assert long_term._generation() > after_update


def test_two_models_are_not_compared_across(workspace, embedder):
    """A stored vector of another width is not a weaker answer. It is different
    arithmetic, and the old code's `rank()` dropped such rows for the same
    reason."""
    long_term.add("Peanuts make me ill")
    db.execute("UPDATE fact_vectors SET vector = ?", (embeddings.pack(np.zeros(4, np.float32)),))
    long_term._invalidate()

    assert long_term.relevant("am I allergic to anything") == []


# -- with no model at all -------------------------------------------------


def test_recall_without_a_model_is_what_it_always_was(workspace, monkeypatch):
    monkeypatch.setattr(embeddings, "available", lambda: False)
    long_term.add("Peanuts make me ill")

    assert long_term.relevant("peanuts")[0].text == "Peanuts make me ill"


def test_a_model_that_disappears_mid_session_does_not_take_recall_with_it(
    workspace, embedder, monkeypatch
):
    long_term.add("Peanuts make me ill")
    # Ollama stopped. The vectors are still in the table; nothing can embed.
    monkeypatch.setattr(embeddings, "embed", lambda texts: None)

    assert long_term.relevant("peanuts")[0].text == "Peanuts make me ill"

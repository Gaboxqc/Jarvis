"""The cover itself — that a semantic test is testing something.

`workspace` sets `documents.semantic_search: False`, for good reasons stated
where it does it. The consequence is that a test which forgets to turn it back
on does not fail and does not skip: it *passes*, having asserted something
about meaning that was never evaluated.

Two did. `test_unrelated_facts_are_not_returned` asserted the property that
semantic recall needs a similarity floor, and it held for nearly a year while
there was no floor in the code, because `_by_meaning()` was returning an empty
list before it reached anything. Nothing about that test looked wrong, and
nothing could have: a vacuous assertion and a satisfied one are the same green
dot.

So this file is about the machinery rather than about recall. It checks that
the switch works in both directions, that the shared fake is wired to the
attribute that actually gates the feature, and -- the point -- that forgetting
to flip it is reproducible on demand rather than something to be careful about.
"""

from __future__ import annotations


from app.index import embeddings
from app.memory import long_term

from .conftest import FakeEmbedder, fake_embedder, no_embedder

AXES = {"allergic": 0, "allergy": 0, "peanuts": 0, "nuts": 0, "bicycle": 1}


# -- the switch works, in both directions --------------------------------


def test_the_fixture_default_leaves_semantics_off(workspace):
    """The state every test starts in, asserted rather than assumed.

    If this ever fails, every opt-in in the suite has become redundant and
    every no-model test has quietly started testing the model.
    """
    assert embeddings.available() is False
    assert embeddings.embed_one("am I allergic to anything") is None


def test_the_shared_fake_actually_produces_vectors(workspace, monkeypatch):
    """One assertion standing in for every opt-in in the suite.

    Four files turn semantic search on through `fake_embedder`. If it stopped
    working -- a renamed attribute in app/index/embeddings.py, a changed
    signature -- every one of those files would go vacuous at once and all of
    them would still pass. This is the single test that would not.
    """
    fake = fake_embedder(monkeypatch, AXES)

    assert embeddings.available() is True
    vector = embeddings.embed_one("peanuts")
    assert vector is not None
    assert vector.shape == (8,)
    assert fake.calls == 1


def test_the_fake_honours_the_switch_it_was_given(workspace, monkeypatch):
    """A fake that ignored `available()` would let a no-model test keep
    getting vectors -- the same failure as forgetting to turn it on, arriving
    from the other side. One of the four copies this replaced did ignore it."""
    fake = fake_embedder(monkeypatch, AXES)
    no_embedder(monkeypatch)

    assert fake(["peanuts"]) is None


def test_turning_it_off_explicitly_matches_the_default(workspace, monkeypatch):
    no_embedder(monkeypatch)

    assert embeddings.available() is False
    assert embeddings.embed_one("anything") is None


# -- and what being vacuous looks like -----------------------------------


def test_semantic_recall_is_dead_without_the_opt_in(workspace):
    """The mechanism of the original bug, pinned.

    Two facts that no shared word connects to the question. With the model on,
    meaning finds the allergy one -- that is the whole reason embeddings are
    here, and test_semantic_search_slow.py asserts it against the real model.
    With it off, `_by_meaning` returns nothing and the answer comes from word
    overlap alone.

    A test that asserted something about the *semantic* answer here would be
    asserting it about the lexical one, and would pass.
    """
    long_term.add("Peanuts make me ill", "fact")
    long_term.add("The bicycle chain needs oil", "fact")

    assert long_term._by_meaning("am I allergic to anything", 6) == []


def test_with_the_opt_in_the_same_call_reaches_the_model(workspace, monkeypatch):
    """The other half, so the test above is a statement about the switch
    rather than about `_by_meaning` being empty in general."""
    fake = fake_embedder(monkeypatch, AXES)
    fact = long_term.add("Peanuts make me ill", "fact")
    long_term.add("The bicycle chain needs oil", "fact")

    found = long_term._by_meaning("am I allergic to anything", 6)

    assert fake.calls > 0, "the question never reached the model"
    assert [item.id for item in found] == [fact.id]


# -- the vocabulary is the test's, the algorithm is not -------------------


def test_the_tokenizer_splits_on_everything_that_is_not_a_word():
    """The four fakes this replaced had four tokenizers: one split on commas,
    one did not, one split on "/" because its example was `D:/work/clients`.
    Each was right in its own file and the set of them meant two files asked
    slightly different questions of the same production code."""
    fake = FakeEmbedder({"work": 0, "clients": 0, "peanuts": 1, "walnuts": 1})

    # Nothing is patched, so `available()` is still false and the contract
    # holds -- the tokenizer is checked directly.
    assert fake.axes["work"] == 0
    from .conftest import _words

    assert _words("D:/work/clients") == ["d", "work", "clients"]
    assert _words("peanuts,walnuts") == ["peanuts", "walnuts"]
    assert _words("What's the deposit?") == ["what", "s", "the", "deposit"]


def test_no_test_module_rolls_its_own_fake_embedder():
    """A fifth hand-rolled fake is how the next vacuous test gets written.

    Not a style rule. The four copies this replaced differed in their
    tokenizer -- commas, slashes -- and in whether they honoured
    `available()`, and the one that ignored it is the shape that makes a
    no-model test pass against a model. One algorithm means one set of
    answers to "what does this fake do".

    Deliberately narrow: patching `embeddings.embed` to return None is a
    legitimate way to say "the model went away mid-session", and
    test_semantic_search.py flips `available` back on to exercise the first
    pull on an install that already has memories. Neither of those is a
    second fake. What is forbidden is defining the embedder itself.
    """
    import pathlib as _pathlib
    import re

    offenders = []
    for path in sorted(_pathlib.Path(__file__).parent.glob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"^class (\w*Embedder\w*)", source, re.MULTILINE):
            offenders.append(f"{path.name}:{match.group(1)}")

    assert not offenders, (
        "these define their own fake embedder; import conftest.fake_embedder "
        f"instead: {offenders}"
    )

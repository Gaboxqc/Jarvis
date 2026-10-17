from __future__ import annotations

import os
import re
import sys
from typing import Any
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, security, settings  # noqa: E402
from app.skills import registry  # noqa: E402
from app.skills.base import Skill, SkillParam, SkillResult  # noqa: E402

# -- API credentials for every TestClient in the suite --------------------
#
# The API refuses anything without a bearer token (app/security.py). Fifteen
# test modules build their own `TestClient(app)` inline, and threading a header
# through every call site would bury the thing each of those tests is actually
# about under plumbing.
#
# So the token is pinned to a known value here and attached once, at the client
# rather than at the call. `setdefault` rather than assignment, so a test that
# wants to say something about authentication can still say it -- either by
# passing its own header, or by deleting this one:
#
#     client.headers.pop("Authorization")   # now an anonymous caller
#
# which is exactly what test_api_auth.py does.
TEST_API_TOKEN = "test-token-not-a-secret"
os.environ[security.API_TOKEN_ENV] = TEST_API_TOKEN
security.reset()

_unauthenticated_client_init = TestClient.__init__


def _authenticated_client_init(self, *args, **kwargs):
    _unauthenticated_client_init(self, *args, **kwargs)
    self.headers.setdefault("Authorization", f"Bearer {TEST_API_TOKEN}")


TestClient.__init__ = _authenticated_client_init

# What the gated test double has actually done. Assertions read this instead of
# a real store, so the gate's invariants stay independent of any product
# decision about which real skills deserve a confirmation prompt.
performed: list[str] = []


class GatedTestSkill(Skill):
    """A stand-in for 'an action worth interrupting someone for'."""

    name = "test.gated"
    description = "Test double for a gated, reversible action."
    parameters = (SkillParam("label", "string", "Something to record."),)
    consequential = True
    reversible = True

    def preview(self, args):
        return f"Do the gated thing with \"{args['label']}\""

    def run(self, args, ctx):
        performed.append(args["label"])
        return SkillResult(
            ok=True,
            message=f"Did {args['label']}",
            undo_payload={"label": args["label"]},
        )

    def undo(self, undo_payload):
        label = undo_payload["label"]
        if label in performed:
            performed.remove(label)
        return SkillResult(ok=True, message=f"Took back {label}")


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated data dir, database and config for each test.

    Two of the settings below are switched *off*, and both of them make a test
    that forgets to turn them back on pass rather than fail:

      documents.semantic_search   every embedding call returns None, so
                                  anything about meaning is evaluated against
                                  an empty list. `fake_embedder` turns it on.
      KAI_NO_BACKGROUND_SCAN      the indexer never runs on its own, so a
                                  scanner test has to call scan() itself.

    The reasons are good and are stated where each one is set. The cost is
    that "it passed" and "it was never evaluated" look identical, and the
    first of those has already cost this suite a test that held for nearly a
    year while the thing it asserted was untrue. tests/test_semantic_cover.py
    is the cover for the cover.
    """
    data = tmp_path / "data"
    sandbox = tmp_path / "sandbox"
    docs = tmp_path / "docs"
    data.mkdir()
    sandbox.mkdir()
    docs.mkdir()

    config = {
        "documents": {
            "indexed_folders": [str(docs)],
            "max_file_mb": 5,
            "rescan_minutes": 15,
            # Tests must not depend on whether the laptop happens to be plugged in.
            "pause_on_battery": False,
            # Or on whether the developer happens to have pulled the embedding
            # model. With it present, every test that indexes a document or
            # remembers a fact would make real HTTP calls to Ollama -- slow, and
            # a different suite on two machines. test_semantic_search.py opts
            # back in with a fake, and test_semantic_search_slow.py with the
            # real one.
            "semantic_search": False,
        },
        "persona": {"name": "Kai", "verbosity": "terse", "idle_timeout_minutes": 30},
        "brain": {"provider": "ollama", "model": "llama3"},
        "privacy": {"allow_web_search": True, "allow_live_data": True},
        "actions": {"pre_approved": [], "confirmation_ttl_minutes": 10},
        "system": {"allowed_roots": [str(sandbox)]},
        "skills": {"disabled": []},
    }
    config_file = tmp_path / "kai.config.yaml"
    config_file.write_text(yaml.safe_dump(config), encoding="utf-8")

    # Keeps the lifespan from starting a scanner thread that writes to the same
    # database the test is using. See the note in app/main.py:lifespan.
    monkeypatch.setenv("KAI_NO_BACKGROUND_SCAN", "1")
    monkeypatch.setenv("KAI_DATA_DIR", str(data))
    monkeypatch.setenv("KAI_CONFIG", str(config_file))
    settings.reset_config_cache()
    from app.index import embeddings

    embeddings.reset_cache()
    db.set_db_path(data / "test.db")
    registry.reset()
    registry.load_skills()[GatedTestSkill.name] = GatedTestSkill()
    performed.clear()

    from app import focus
    from app.capture import session as capture
    from app.index import scanner

    scanner.reset_state()
    focus.reset()
    capture.reset()

    yield sandbox

    db.close_connection()
    db.set_db_path(None)
    settings.reset_config_cache()
    embeddings.reset_cache()
    registry.reset()
    scanner.reset_state()
    focus.reset()
    capture.reset()


@pytest.fixture
def docs_folder(workspace: Path, tmp_path: Path) -> Path:
    """The folder wired into documents.indexed_folders for this test."""
    return tmp_path / "docs"


@pytest.fixture
def config_file() -> Path:
    return Path(os.environ["KAI_CONFIG"])


def minimal_pdf(pages: list[list[str]]) -> bytes:
    """Build a small, spec-valid PDF with real text operators.

    Written by hand rather than pulled in as a test dependency: PDF is the format
    document search exists for, so its extraction path deserves coverage against
    an actual PDF rather than a mock that would pass whatever pypdf did.
    """
    objects: list[bytes] = []
    page_ids = [3 + i * 2 for i in range(len(pages))]
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)

    objects.append(b"<</Type/Catalog/Pages 2 0 R>>")
    objects.append(
        f"<</Type/Pages/Kids[{kids}]/Count {len(pages)}>>".encode()
    )

    font_id = 3 + len(pages) * 2
    for index, lines in enumerate(pages):
        content = "BT /F1 14 Tf 72 720 Td 18 TL\n"
        for line in lines:
            escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            content += f"({escaped}) Tj T*\n"
        content += "ET"
        body = content.encode("latin-1")
        objects.append(
            f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents {4 + index * 2} 0 R"
            f"/Resources<</Font<</F1 {font_id} 0 R>>>>>>".encode()
        )
        objects.append(b"<</Length " + str(len(body)).encode() + b">>stream\n" + body + b"\nendstream")

    objects.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj".encode() + body + b"endobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer<</Size {len(objects) + 1}/Root 1 0 R>>\nstartxref\n{xref_at}\n%%EOF"
    ).encode()
    return bytes(out)


def make_files(folder: Path, names: list[str]) -> list[Path]:
    created = []
    for name in names:
        path = folder / name
        path.write_text(f"content of {name}", encoding="utf-8")
        created.append(path)
    return created


# -- turning semantic search on, in one place -----------------------------
#
# `workspace` above sets `documents.semantic_search: False`, so by default
# every embedding call returns None and `_by_meaning()` returns an empty list
# before it reaches anything. That default is right -- with it on, every test
# that indexes a document or remembers a fact would make real HTTP calls to
# Ollama, which is slow and makes the suite a different suite on two machines.
#
# What it also does is make a test that forgets to opt back in *pass*. Not
# fail, not skip: pass, while asserting something about meaning that was never
# evaluated. Two of them did. `test_unrelated_facts_are_not_returned` asserted
# the property that semantic recall needs a similarity floor, and it held for
# nearly a year with no floor in the code, because the path it names was
# switched off underneath it. The other was in test_memory_and_scheduling.py,
# found the same week.
#
# Four files had grown four hand-rolled ways to turn it on: three `embedder`
# fixtures patching the same three attributes, a fourth parametrised variant,
# five fake embedders implementing the same algorithm, and two copies of the
# read-counter below. All of them worked. The trouble with four copies of the
# thing that decides whether a test is real is that the fifth is the one that
# gets written slightly wrong, and nothing about a vacuous test looks wrong.
#
# So: one algorithm here, vocabularies stay with the tests that read them.


def _words(text: str) -> list[str]:
    """Split on anything that is not a word character.

    One tokenizer, because the four copies this replaced had four. One
    replaced "," with a space and the others only stripped it from the edges;
    a third replaced "/" because its example was a path, `D:/work/clients`,
    which the other three would have read as one word. None of that was
    wrong in the file it lived in and all of it was a reason two files
    answered slightly different questions about the same production code.

    Splitting on non-word characters subsumes all four and needs no
    per-file argument. Every vocabulary in the suite is plain words, so
    nothing is lost by it.
    """
    return re.findall(r"[a-z0-9]+", text.lower())


class FakeEmbedder:
    """Deterministic vectors from a tiny hand-built vocabulary.

    Words that mean the same thing share an axis, which is the one property a
    real embedding model provides that these tests read. No model, no network,
    same answer every run.

    `axes` stays with the test file: each one has its own examples -- a lease's
    deposit, a peanut allergy, a landlord's name -- and a shared vocabulary
    would be a list nobody could change without reading four files.
    """

    def __init__(self, axes: dict[str, int], width: int = 8) -> None:
        self.axes = axes
        self.width = width
        #: How many times the model was asked. "Did this reach Ollama at all"
        #: is the question the recall-latency tests exist to answer.
        self.calls = 0

    def __call__(self, texts: list[str]) -> list[Any] | None:
        from app.index import embeddings

        # Same contract as the real one: None when there is no model. A fake
        # that ignored this would let a test "turn the model off" and keep
        # getting vectors, which is the no-model tests passing by accident --
        # the same failure as forgetting to turn it on, from the other side.
        if not embeddings.available():
            return None

        self.calls += 1
        import numpy as np

        out = []
        for text in texts:
            vector = np.zeros(self.width, dtype=np.float32)
            for word in _words(text):
                axis = self.axes.get(word)
                if axis is not None:
                    vector[axis] += 1.0
            out.append(embeddings.normalise(vector))
        return out


def fake_embedder(monkeypatch, axes: dict[str, int], width: int = 8) -> FakeEmbedder:
    """Turn semantic search on for one test, with a fake model behind it.

    Patches the three attributes that decide whether the feature is live.
    `available()` is the one that matters: `_by_meaning()` and the document
    search both ask it first, so patching `embed` alone would leave the
    feature off and the test vacuous.
    """
    from app.index import embeddings

    fake = FakeEmbedder(axes, width)
    monkeypatch.setattr(embeddings, "available", lambda: True)
    monkeypatch.setattr(embeddings, "embed", fake)
    monkeypatch.setattr(embeddings, "model_name", lambda: "fake-embed")
    return fake


def no_embedder(monkeypatch) -> None:
    """Turn it off explicitly, for the tests that are about the off state.

    `workspace` already leaves it off, so this changes nothing -- which is the
    point of saying it out loud. A test named "...without a model" that relies
    on a fixture default is a test that silently changes meaning the day the
    default does.
    """
    from app.index import embeddings

    monkeypatch.setattr(embeddings, "available", lambda: False)


def count_vector_reads(monkeypatch, table: str) -> list[int]:
    """Count SELECTs that pull vector blobs out of `table`.

    The cost the resident caches exist to remove, and the only way to see
    they are working: both of them are invisible from the outside except as
    a query that stops happening.
    """
    seen = [0]
    original = db.query

    def counting(sql, params=()):
        if f"FROM {table}" in sql and "vector" in sql.split("FROM")[0]:
            seen[0] += 1
        return original(sql, params)

    monkeypatch.setattr(db, "query", counting)
    return seen

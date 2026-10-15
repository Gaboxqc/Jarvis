"""One turn at a time per session — REQ-6, REQ-24.

Two overlapping turns in one session interleave, and the damage is not
theoretical. Both record the user's text, both then read a conversation window
that contains the other's message, both route against it, and both can park an
action awaiting approval. The model is shown a conversation that did not
happen, and the user can be shown a confirmation for something proposed in
answer to a different question.

The desktop UI serializes by disabling the composer while a turn runs, which is
a property of one client rather than of the API -- the CLI does not, a script
does not, and `/turn/stream` stays open long enough for a second request to be
an ordinary accident rather than a race someone had to engineer.
"""

from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

from app.brain import llm, orchestrator
from app.memory import short_term


@pytest.fixture
def client(workspace):
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def blocking_llm(monkeypatch, started: threading.Event, release: threading.Event):
    """A model call that parks until it is let go.

    The whole point is to hold a turn open while a second one arrives, which
    needs the first to be genuinely in flight rather than merely slow.
    """

    def fake_chat(messages, *, json_mode=False, temperature=None, settings=None, tools=None):
        started.set()
        release.wait(timeout=10)
        return llm.LLMReply(text=json.dumps({"skills": [], "reply": "Done."}), raw={})

    monkeypatch.setattr(llm, "chat", fake_chat)


def test_a_second_turn_in_the_same_session_is_refused(workspace, monkeypatch):
    started, release = threading.Event(), threading.Event()
    blocking_llm(monkeypatch, started, release)

    first: list[orchestrator.TurnResult] = []

    def run_first():
        first.append(orchestrator.handle_turn("what is on my list", "s"))

    worker = threading.Thread(target=run_first)
    worker.start()
    assert started.wait(timeout=5), "the first turn never reached the model"

    second = orchestrator.handle_turn("and the other thing", "s")

    release.set()
    worker.join(timeout=10)

    assert second.error == "turn_in_progress"
    assert second.reply
    assert first and first[0].error is None


def test_a_refused_turn_is_not_recorded(workspace, monkeypatch):
    """The refused text must not reach the conversation window.

    Recording it would put a message in the history that was never answered,
    so the retry would be routed against a conversation containing a question
    the assistant appears to have ignored -- which is the interleaving this
    exists to prevent, arriving by the other door.
    """
    started, release = threading.Event(), threading.Event()
    blocking_llm(monkeypatch, started, release)

    worker = threading.Thread(target=lambda: orchestrator.handle_turn("first question", "s"))
    worker.start()
    assert started.wait(timeout=5)

    orchestrator.handle_turn("second question", "s")

    release.set()
    worker.join(timeout=10)

    said = [turn.text for turn in short_term.window("s")]
    assert "first question" in said
    assert "second question" not in said


def test_a_different_session_is_not_blocked(workspace, monkeypatch):
    """The lock is per session, not global. Two windows, or the CLI alongside
    the app, are two conversations and must not block each other."""
    started, release = threading.Event(), threading.Event()
    blocking_llm(monkeypatch, started, release)

    worker = threading.Thread(target=lambda: orchestrator.handle_turn("hold this", "one"))
    worker.start()
    assert started.wait(timeout=5)

    # Same stub, so this one parks on the model too -- but it gets that far,
    # which is what is being asserted. Released immediately after.
    release.set()
    other = orchestrator.handle_turn("and this", "two")
    worker.join(timeout=10)

    assert other.error is None


def test_the_lock_is_released_when_a_stream_is_abandoned(workspace, monkeypatch):
    """A client that disconnects mid-stream must not wedge its session.

    The lock is held for the life of the generator, so the release depends on
    `finally` running when the generator is closed rather than drained. That is
    the behaviour this pins: without it, one dropped SSE connection would leave
    a session refusing every turn until the process restarted.
    """
    monkeypatch.setattr(
        llm,
        "chat",
        lambda *a, **k: llm.LLMReply(text=json.dumps({"skills": [], "reply": "Hi."}), raw={}),
    )

    events = orchestrator.run_turn("hello", "s", stream=False)
    next(events)                      # start it, so the lock is taken
    events.close()                    # abandon it, as a dropped client does

    assert orchestrator.handle_turn("hello again", "s").error is None


def test_the_api_reports_the_refusal_rather_than_failing(workspace, client, monkeypatch):
    """A refused turn is a 200 carrying an error code, not a 5xx.

    The turn did not happen, which is a thing the caller needs to know and act
    on -- retry in a moment -- and not a failure of the request.
    """
    started, release = threading.Event(), threading.Event()
    blocking_llm(monkeypatch, started, release)

    worker = threading.Thread(target=lambda: orchestrator.handle_turn("hold", "ui"))
    worker.start()
    assert started.wait(timeout=5)

    response = client.post("/turn", json={"text": "and this", "session_id": "ui"})

    release.set()
    worker.join(timeout=10)

    assert response.status_code == 200
    assert response.json()["error"] == "turn_in_progress"

"""Where a turn's milliseconds went — REQ-27.

The performance numbers this project quotes were all measured once and written
into a comment. Nothing measured the running app, so "it feels slow today" had
no answer. app/timings.py is that answer, and these are the things about it
that have to keep being true.

The two that matter most are not about arithmetic:

  a stage's clock must cover the call it names and nothing else -- a timer
      wrapped around the wrong line still produces plausible milliseconds, and
      a panel attributing the model's latency to the skills is worse than no
      panel at all

  the response must carry durations and nothing else -- this sits beside
      /diagnostics/logs, which deliberately serves a file's size and never its
      contents, and the same restraint has to hold here or a diagnostics
      counter quietly becomes a profile

The model is stubbed, with a *measured* delay. A stub that returns instantly
makes every stage round to 0.0ms, and `assert 0.0 == 0.0` passes whichever call
the clock was really wrapped around.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app import timings
from app.brain import llm, orchestrator

# Well above perf_counter's resolution on Windows (~100ns here), and small
# enough that the handful of turns below cost under a tenth of a second.
DELAY = 0.05
DELAY_MS = DELAY * 1000


@pytest.fixture(autouse=True)
def _empty_ring():
    """Every test starts with an empty window and leaves one behind.

    The ring is process-global, like the gate's and the log's. Without this the
    medians in one test would be computed over turns another test ran.
    """
    timings.clear()
    yield
    timings.clear()


@pytest.fixture
def client(workspace):
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def route(skills):
    return json.dumps({"skills": skills, "reply": None})


def slow_router(monkeypatch, *replies):
    """Stub the model so only the routing call is slow.

    Routing asks for JSON; the synthesis pass does not. Charging just that one
    call is what makes stage attribution testable at all: if the clock were
    wrapped around the wrong thing, `routing_ms` and `synthesis_ms` would be
    the wrong way round rather than both merely small.
    """
    queue = list(replies)

    def fake_chat(messages, *, json_mode=False, temperature=None, settings=None, tools=None):
        if json_mode:
            time.sleep(DELAY)
        reply = queue.pop(0) if queue else replies[-1]
        return llm.LLMReply(text=reply, raw={})

    monkeypatch.setattr(llm, "chat", fake_chat)


# -- the ring -------------------------------------------------------------


def test_the_window_is_bounded_and_newest_first():
    """A ring, not a list. This is a diagnostics surface in a process that is
    meant to run all day, and an unbounded one would be a slow leak that only
    shows up on the machines that use the app most."""
    for index in range(timings.CAPACITY + 10):
        timings.record(
            timings.TurnTiming(
                at=float(index),
                routing_ms=1.0,
                skills_ms=0.0,
                synthesis_ms=1.0,
                total_ms=2.0,
                skills=0,
                rounds=1,
            )
        )

    kept = timings.recent()

    assert len(kept) == timings.CAPACITY
    assert kept[0].at == float(timings.CAPACITY + 9)
    # The oldest ten fell off the other end rather than the newest ten being
    # refused, which is the mistake a fixed-size list makes.
    assert kept[-1].at == 10.0


def test_the_median_of_an_even_window_is_the_middle_pair():
    """Not a mean. A cold model load and a one-word greeting sit in the same
    fifty turns, and one of them moves a mean by seconds."""
    for total in (10.0, 20.0, 30.0, 1000.0):
        timings.record(
            timings.TurnTiming(
                at=0.0, routing_ms=0.0, skills_ms=0.0, synthesis_ms=0.0,
                total_ms=total, skills=0, rounds=1,
            )
        )

    assert timings.summary()["median"]["total_ms"] == 25.0


def test_an_empty_window_still_answers():
    """The panel renders before the first turn of a session, on every restart."""
    summary = timings.summary()

    assert summary["count"] == 0
    assert summary["turns"] == []
    assert summary["median"]["total_ms"] == 0.0
    # Null rather than a zero turn: there is no slowest turn yet, and inventing
    # one would put "0 ms" under a heading that claims to name the worst case.
    assert summary["slowest"] is None


# -- what the turn loop actually records ----------------------------------


def test_each_stage_is_charged_for_its_own_call(workspace, monkeypatch):
    """The assertion the whole feature rests on.

    A timer around the wrong line produces numbers that look entirely
    reasonable. Here only the routing call sleeps, so a swapped or widened
    stage shows up as `synthesis_ms` carrying routing's 50ms.
    """
    slow_router(monkeypatch, route([]), "Madrid is the capital of Spain.")

    orchestrator.handle_turn("what is the capital of spain", "t")

    recorded = timings.recent()
    assert len(recorded) == 1
    turn = recorded[0]
    assert turn.routing_ms >= DELAY_MS
    assert turn.synthesis_ms < DELAY_MS
    assert turn.total_ms >= turn.routing_ms
    assert turn.rounds == 1
    assert turn.skills == 0


def test_a_skill_turn_charges_the_skills_stage_and_counts_it(workspace, monkeypatch):
    slow_router(monkeypatch, route([
        {"name": "utils.calculate", "args": {"expression": "17*23"}}
    ]), "That comes to 391.")

    orchestrator.handle_turn("what's 17 times 23", "t")

    turn = timings.recent()[0]
    assert turn.skills == 1
    # The gate ran something, so the stage is not zero. Not given a floor: a
    # local arithmetic skill can genuinely finish inside a tenth of a
    # millisecond, and a budget here would be a flake rather than a guard.
    assert turn.skills_ms >= 0.0
    assert turn.total_ms >= turn.routing_ms + turn.skills_ms


def test_a_parked_action_is_not_counted_as_a_skill_that_ran(workspace, monkeypatch):
    """A confirmation prompt is not work done.

    Counting the parked submission would make every consequential request look
    like it executed a skill, which is the one thing the Action Gate exists to
    distinguish.
    """
    from .conftest import make_files

    make_files(workspace, ["a.pdf", "b.jpg"])
    slow_router(monkeypatch, route([
        {"name": "system.organize_folder", "args": {"folder": str(workspace)}}
    ]))

    result = orchestrator.handle_turn("tidy up that folder", "t")

    assert result.needs_confirmation
    turn = timings.recent()[0]
    assert turn.skills == 0
    assert turn.routing_ms >= DELAY_MS
    # Nothing was written, so the synthesis pass never ran: the prompt is
    # emitted whole. A number here would mean the gate had been talked past.
    assert turn.synthesis_ms == 0.0


def test_a_fast_path_turn_is_recorded_with_no_routing(workspace, monkeypatch):
    """The claim F1 made about pleasantries, now measurable.

    "Routing a greeting costs a whole model call to be told it is not a
    request" was the argument for the fast path, and until this existed the
    only evidence for it was the argument.
    """
    slow_router(monkeypatch, "You're welcome.")

    orchestrator.handle_turn("thanks", "t")

    turn = timings.recent()[0]
    assert turn.rounds == 0
    assert turn.routing_ms == 0.0


def test_a_second_routing_round_is_visible_as_a_round(workspace, monkeypatch):
    """Two rounds cost two model calls, which is the trade F1 made opt-in.

    `rounds` is what makes it checkable on a real machine rather than argued
    about: a turn that routed twice carries twice the routing time.
    """
    first = json.dumps({
        "skills": [{"name": "utils.calculate", "args": {"expression": "2+2"}}],
        "needs_followup": True,
    })
    slow_router(
        monkeypatch,
        first,
        route([{"name": "utils.calculate", "args": {"expression": "4*4"}}]),
        route([]),
        "Sixteen.",
    )

    orchestrator.handle_turn("double it and square it", "t")

    turn = timings.recent()[0]
    assert turn.rounds >= 2
    assert turn.routing_ms >= 2 * DELAY_MS


def test_a_turn_abandoned_mid_stream_is_still_recorded(workspace, monkeypatch):
    """The turns worth looking at are the ones nobody waited for.

    Recording on completion only would leave the window biased towards the
    turns that finished, so a user who gave up on a slow one would see a panel
    reporting that everything is fine. Closing the generator is what a client
    disconnecting does.
    """
    slow_router(monkeypatch, route([]), "A long answer.")

    stream = orchestrator.run_turn("what is the capital of spain", "t", stream=True)
    next(stream)
    stream.close()

    assert len(timings.recent()) == 1
    assert timings.recent()[0].routing_ms >= 0.0


def test_a_refused_concurrent_turn_records_nothing(workspace, monkeypatch):
    """A turn that was told to wait did no work and recorded nothing.

    Nothing else about a refusal is kept either -- the user's text never
    reaches the conversation window -- and a 0ms turn in the window would drag
    a median that exists to describe real ones.
    """
    slow_router(monkeypatch, route([]), "An answer.")

    held = orchestrator.run_turn("first", "same-session", stream=True)
    next(held)

    second = orchestrator.handle_turn("second", "same-session")

    assert second.error == "turn_in_progress"
    assert len(timings.recent()) == 0
    held.close()


# -- the endpoint ---------------------------------------------------------


def test_the_endpoint_serves_the_window(client, monkeypatch):
    slow_router(monkeypatch, route([]), "Madrid.")
    orchestrator.handle_turn("what is the capital of spain", "t")

    body = client.get("/diagnostics/timings").json()

    assert body["count"] == 1
    assert body["capacity"] == timings.CAPACITY
    assert body["turns"][0]["routing_ms"] >= DELAY_MS
    assert body["slowest"]["total_ms"] == body["turns"][0]["total_ms"]


def test_the_response_carries_durations_and_nothing_else(client, monkeypatch):
    """The privacy half, asserted rather than intended.

    /diagnostics/logs serves a file's name and size and never a line of it.
    This is the same surface and keeps the same rule: no user text, no skill
    names, no session id. The check is deliberately crude -- the whole response
    body as a string -- because a field added later without a thought about
    this is exactly what it is here to catch.
    """
    slow_router(monkeypatch, route([
        {"name": "utils.calculate", "args": {"expression": "17*23"}}
    ]), "That comes to 391.")
    orchestrator.handle_turn("what is my landlord called", "a-session-name")

    raw = client.get("/diagnostics/timings").text

    assert "landlord" not in raw
    assert "a-session-name" not in raw
    assert "utils.calculate" not in raw


def test_the_wipe_empties_the_window(client, monkeypatch):
    """"Delete everything" includes it.

    In memory rather than on disk, so no row count changes -- but a panel still
    reporting how long this morning's turns took, under a button that said it
    removed everything, would make the button a half-truth.
    """
    slow_router(monkeypatch, route([]), "Madrid.")
    orchestrator.handle_turn("what is the capital of spain", "t")
    assert timings.recent()

    client.post("/privacy/wipe")

    assert timings.recent() == []

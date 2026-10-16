"""A turn that looks, then acts — REQ-6, REQ-24, REQ-27.

One routing pass is a ceiling on what the assistant can be *asked*, not just on
what it costs. "Find the lease, then tell me what the deposit was" is two tool
calls where the second's arguments live in the first's result, so a router
planning both in one shot has to guess at them -- and a model that guesses
produces a confident call against a document it has not seen.

So a turn may route twice. The constraints that make that safe are what this
file is about:

* the second pass happens only when the first asked for it, because a routing
  call is half the latency of a turn and almost no request needs one;
* a parked action stops everything, in any round -- a "no" must never arrive
  after the work was done anyway;
* both rounds share one batch id, so undo still takes the whole turn back as
  one operation;
* and the count is bounded, because each round is a batch of real side effects
  and a loop with no fixed bound is one bad route away from running the Action
  Gate in circles.
"""

from __future__ import annotations

import json

import pytest

from app.actions import journal
from app.brain import llm, orchestrator
from tests.conftest import performed


def route(skills, *, followup=False, reply=None):
    body: dict = {"skills": skills, "reply": reply}
    if followup:
        body["needs_followup"] = True
    return json.dumps(body)


def stub_llm(monkeypatch, *replies):
    """Queue replies in call order, and record every prompt sent."""
    queue = list(replies)
    seen: list[list[dict]] = []

    def fake_chat(messages, *, json_mode=False, temperature=None, settings=None, tools=None):
        seen.append(messages)
        return llm.LLMReply(text=queue.pop(0) if queue else replies[-1], raw={})

    monkeypatch.setattr(llm, "chat", fake_chat)
    return seen


TIME = {"name": "utils.time", "args": {}}
SUM = {"name": "utils.calculate", "args": {"expression": "2+2"}}
GATED = {"name": "test.gated", "args": {"label": "send the mail"}}


# -- the second round happens, when it was asked for ---------------------


def test_a_dependent_request_gets_a_second_pass(workspace, monkeypatch):
    seen = stub_llm(
        monkeypatch,
        route([TIME], followup=True),   # round 1: look
        route([SUM]),                   # round 2: now act on what it saw
        "Here you go.",                 # synthesis
    )

    result = orchestrator.handle_turn("what time is it and then add it up", "t")

    assert [call["skill"] for call in result.skill_calls] == [
        "utils.time",
        "utils.calculate",
    ]
    # Three model calls: two routes and one synthesis.
    assert len(seen) == 3


def test_the_second_pass_is_shown_what_already_ran(workspace, monkeypatch):
    """Otherwise it is not a follow-up, it is the same question again -- and
    the model would re-ask for the tool it just used."""
    seen = stub_llm(
        monkeypatch,
        route([TIME], followup=True),
        route([]),
        "Done.",
    )

    orchestrator.handle_turn("what time is it", "t")

    second_route = seen[1]
    carried = "\n".join(message["content"] for message in second_route)
    assert "have already run" in carried
    assert "utils.time" in carried
    # And it is told not to repeat itself.
    assert "already ran" in carried


def test_the_request_stays_the_last_user_message(workspace, monkeypatch):
    """Small models reliably answer the final user turn, and the thing being
    routed is the request -- so the results go in ahead of it as context."""
    seen = stub_llm(monkeypatch, route([TIME], followup=True), route([]), "Done.")

    orchestrator.handle_turn("find the deposit", "t")

    assert seen[1][-1] == {"role": "user", "content": "find the deposit"}


# -- and does not, when it was not --------------------------------------


def test_one_round_is_the_default(workspace, monkeypatch):
    """No `needs_followup`, no second routing call. A model that never sets
    the field gets exactly the behaviour this had before it existed, which is
    what makes asking for it safe."""
    seen = stub_llm(monkeypatch, route([TIME]), "It is noon.")

    orchestrator.handle_turn("what time is it", "t")

    # One route, one synthesis. Nothing extra.
    assert len(seen) == 2


def test_a_turn_with_no_tools_does_not_route_twice(workspace, monkeypatch):
    seen = stub_llm(monkeypatch, route([]), "Madrid.")

    orchestrator.handle_turn("what is the capital of spain", "t")

    assert len(seen) == 2


@pytest.mark.parametrize("value", ["true", 1, "yes", None, {}])
def test_only_a_real_true_asks_for_another_round(workspace, monkeypatch, value):
    """A turn that re-routes because it misread a reply is a turn that runs
    side effects twice. Anything that is not the boolean is a no."""
    body = json.dumps({"skills": [TIME], "reply": None, "needs_followup": value})
    seen = stub_llm(monkeypatch, body, "Done.")

    orchestrator.handle_turn("what time is it", "t")

    assert len(seen) == 2


def test_a_second_pass_that_wants_nothing_just_finishes(workspace, monkeypatch):
    """The common case once it has looked: the results were enough."""
    stub_llm(monkeypatch, route([TIME], followup=True), route([]), "It is noon.")

    result = orchestrator.handle_turn("what time is it", "t")

    assert [call["skill"] for call in result.skill_calls] == ["utils.time"]
    assert result.reply.startswith("It is noon")


# -- the bound -----------------------------------------------------------


def test_routing_stops_at_the_round_limit(workspace, monkeypatch):
    """Every round asks for another one. It must still stop.

    Each round is a model call and a batch of real side effects, so an
    unbounded loop is one bad route away from running the Action Gate in
    circles.
    """
    # Every routing reply asks for another round. The queue is deliberately
    # one route per permitted round plus the synthesis -- if the bound did not
    # hold, the stub would keep handing out the same "and another" forever.
    seen = stub_llm(
        monkeypatch,
        route([TIME], followup=True),
        route([TIME], followup=True),
        "Done.",
    )

    result = orchestrator.handle_turn("go on forever", "t")

    assert len(result.skill_calls) == orchestrator.MAX_ROUNDS
    # MAX_ROUNDS routes plus one synthesis, and no more.
    assert len(seen) == orchestrator.MAX_ROUNDS + 1


# -- the gate is untouched ----------------------------------------------


def test_a_parked_action_stops_the_second_round(workspace, monkeypatch):
    """The rule that matters most. A "no" must never arrive after the rest of
    the turn went ahead anyway."""
    seen = stub_llm(monkeypatch, route([GATED], followup=True), route([SUM]), "Done.")

    result = orchestrator.handle_turn("send the mail then add it up", "t")

    assert result.needs_confirmation
    assert performed == []
    # One route, and no second one: the turn stopped at the question.
    assert len(seen) == 1


def test_a_parked_action_in_the_second_round_still_parks(workspace, monkeypatch):
    stub_llm(monkeypatch, route([TIME], followup=True), route([GATED]), "Done.")

    result = orchestrator.handle_turn("check the time then send the mail", "t")

    assert result.needs_confirmation
    assert result.pending is not None
    assert result.pending.skill == "test.gated"
    assert performed == []


def test_both_rounds_undo_as_one_batch(workspace, monkeypatch):
    """One turn is one operation. Two batch ids would make a two-round turn
    undoable in halves, which is not what the user did."""
    stub_llm(monkeypatch, route([SUM], followup=True), route([SUM]), "Done.")

    orchestrator.handle_turn("add it up twice", "t")

    batches = {record.batch_id for record in journal.history()}
    assert len(batches) == 1


def test_what_the_second_round_could_not_fit_is_still_named(workspace, monkeypatch):
    """`not_run` accumulates across rounds. A turn that quietly does part of
    what was asked and reports it as all of it is worse than one that fails."""
    many = [TIME, SUM, {"name": "memory.list", "args": {}}, {"name": "utils.time", "args": {}}]
    stub_llm(monkeypatch, route(many, followup=True), route(many), "Done.")

    result = orchestrator.handle_turn("do lots of things", "t")

    # One over the per-round budget, in each of two rounds.
    assert len(result.not_run) == 2

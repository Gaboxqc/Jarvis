"""Turns that skip the router — REQ-27.

Routing costs a full model call, and on a local 8B model that is roughly half
the latency of a turn. A greeting can never need a tool, so paying to be told
so is waste the user feels on every "thanks".

The risk runs one way. Skipping the router on something that *did* need a tool
gives a confident answer with nothing behind it, which is precisely the failure
_guard_ungrounded_reply exists to catch. So the tests here care much more about
what still routes than about what doesn't.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.brain import llm, orchestrator


@pytest.fixture
def counted(workspace):
    """Counts routing calls separately from reply calls.

    The router is the JSON-mode call; the reply pass is not. That distinction is
    the whole point of the optimisation, so the test asserts on it directly
    rather than on a total.
    """
    calls = {"router": 0, "reply": 0}

    def fake_chat(messages, *, json_mode=False, temperature=None, settings=None):
        calls["router" if json_mode else "reply"] += 1
        if json_mode:
            return llm.LLMReply(text='{"skills": [], "reply": null}', raw={})
        return llm.LLMReply(text="You're welcome.", raw={})

    with patch.object(orchestrator.llm, "chat", side_effect=fake_chat):
        yield calls


# -- what skips ------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["thanks", "Thanks!", "thank you", "hi", "Hello", "good morning",
     "ok", "got it", "bye", "gracias", "hola", "buenos días", "vale"],
)
def test_a_pleasantry_never_pays_for_routing(counted, text):
    result = orchestrator.handle_turn(text, session_id="fast")

    assert counted["router"] == 0, f"{text!r} should not have been routed"
    assert counted["reply"] == 1, "it still needs an answer"
    assert result.reply


def test_the_reply_still_comes_from_the_model(counted):
    """Not a canned string.

    A hardcoded "You're welcome" would ignore the configured persona and answer
    in English to a Spanish user, which is a worse bug than the latency.
    """
    result = orchestrator.handle_turn("thanks", session_id="fast")
    assert result.reply == "You're welcome."


# -- what must not skip ----------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # Each opens with a pleasantry and then asks for something real.
        "thanks, now what is on my calendar?",
        "hi, remind me to call the dentist at 5",
        "hello can you check my inbox",
        "ok delete those files",
        "gracias, ¿qué tengo mañana?",
        # Bare requests that share a prefix with the set.
        "great, add a task to buy milk",
        "perfect timing for a reminder at 6pm",
        # Not a greeting at all.
        "what time is it?",
        "okay so how much did I spend",
    ],
)
def test_anything_carrying_a_request_still_routes(counted, text):
    orchestrator.handle_turn(text, session_id="fast")
    assert counted["router"] == 1, f"{text!r} must go through the router"


def test_the_set_holds_no_phrase_that_could_be_a_request():
    """A regression guard on the list itself.

    Someone adding "sure" or "do it" here would break confirmation handling, and
    a verb anywhere in the set means it could be an instruction.
    """
    forbidden = {
        "yes", "no", "sure", "do it", "go ahead", "please", "help",
        "sí", "si", "dale", "hazlo",
    }
    assert not (orchestrator._PLEASANTRIES & forbidden)

    # A short fixed phrase can be checked by eye; a long one cannot, and the
    # whole safety argument rests on someone having checked.
    for phrase in orchestrator._PLEASANTRIES:
        assert len(phrase.split()) <= 4, f"{phrase!r} is long enough to hide a request"


def test_a_pending_confirmation_still_wins(counted, workspace):
    """"ok" with something parked is an answer, not small talk.

    _PLEASANTRIES contains "ok" and _AFFIRMATIVE contains it too. The
    confirmation branch runs first and must keep doing so, or approving an
    action would silently become a greeting.
    """
    from app.skills.base import SkillResult

    with patch.object(orchestrator.gate, "confirm") as confirm:
        confirm.return_value = orchestrator.gate.GateOutcome(
            status=orchestrator.gate.EXECUTED,
            skill_name="demo",
            result=SkillResult(ok=True, message="Done."),
        )
        orchestrator.handle_turn("ok", session_id="fast", pending_action_id="abc-123")

    confirm.assert_called_once()
    assert counted["router"] == 0 and counted["reply"] == 0

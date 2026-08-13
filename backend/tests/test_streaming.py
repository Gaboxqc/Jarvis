"""Streaming replies — REQ-27, REQ-32.

Three properties are load-bearing here, and only one of them is about speed.

The Action Gate must decide before anything streams. A confirmation prompt that
arrived token by token would read as an answer being given, when it is a
question waiting for approval — and the whole trust model rests on that
distinction being visible.

`done` must always arrive, carrying the authoritative reply. Receipts (REQ-7)
and the ungrounded-answer guard both revise text after generation, so what the
deltas showed is provisional.

And the streamed and non-streamed paths must not drift, which is why
handle_turn is defined in terms of run_turn rather than beside it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.brain import llm, orchestrator


def collect(text, session_id="stream", **kwargs):
    return list(orchestrator.run_turn(text, session_id, **kwargs))


def kinds(events):
    return [e["type"] for e in events]


def streamed_text(events):
    return "".join(e["text"] for e in events if e["type"] == "delta")


def final(events):
    return next(e["result"] for e in events if e["type"] == "done")


@pytest.fixture
def talking(workspace):
    """A model that routes nothing and answers in three pieces."""
    def fake_stream(messages, **kwargs):
        yield from ("Hello", " there", ".")

    def fake_chat(messages, *, json_mode=False, **kwargs):
        if json_mode:
            return llm.LLMReply(text='{"skills": [], "reply": null}', raw={})
        return llm.LLMReply(text="Hello there.", raw={})

    with patch.object(orchestrator.llm, "stream", side_effect=fake_stream), \
         patch.object(orchestrator.llm, "chat", side_effect=fake_chat):
        yield


# -- the shape of a stream -------------------------------------------------


def test_a_reply_arrives_in_pieces(talking):
    events = collect("tell me something")

    assert streamed_text(events) == "Hello there."
    assert kinds(events).count("delta") == 3
    assert kinds(events)[-1] == "done"


def test_done_always_comes_last_and_carries_the_whole_reply(talking):
    events = collect("tell me something")
    assert final(events).reply == "Hello there."


def test_stages_are_announced_before_the_slow_parts(talking):
    """Routing cannot stream, so the interface has to be able to say why it waits."""
    events = collect("tell me something")
    stages = [e["stage"] for e in events if e["type"] == "stage"]

    assert "routing" in stages
    assert stages.index("routing") < len(stages) - 1 or "writing" in stages


def test_an_empty_message_still_ends_properly(workspace):
    events = collect("   ")
    assert kinds(events) == ["done"]
    assert final(events).reply == ""


# -- the gate is not streamed ----------------------------------------------


def test_a_confirmation_prompt_is_never_streamed(workspace):
    """It is a question awaiting approval, not an answer being given."""
    def fake_chat(messages, *, json_mode=False, **kwargs):
        if json_mode:
            return llm.LLMReply(
                text='{"skills": [{"name": "test.gated", '
                     '"args": {"label": "the thing"}}], "reply": null}',
                raw={},
            )
        return llm.LLMReply(text="unused", raw={})

    def must_not_be_called(messages, **kwargs):
        raise AssertionError("the gate had not decided yet")
        yield  # pragma: no cover  -- makes this a generator

    with patch.object(orchestrator.llm, "chat", side_effect=fake_chat), \
         patch.object(orchestrator.llm, "stream", side_effect=must_not_be_called):
        events = collect("do the gated thing")

    assert "delta" not in kinds(events), "a pending confirmation must arrive whole"
    result = final(events)
    assert result.needs_confirmation
    assert result.pending.action_id


def test_nothing_streams_before_a_skill_has_actually_run(workspace):
    """Ordering guard: the 'working' stage precedes any reply text."""
    def fake_chat(messages, *, json_mode=False, **kwargs):
        if json_mode:
            return llm.LLMReply(
                text='{"skills": [{"name": "utils.calculate", '
                     '"args": {"expression": "2+2"}}], "reply": null}',
                raw={},
            )
        return llm.LLMReply(text="It is 4.", raw={})

    def fake_stream(messages, **kwargs):
        yield "It is 4."

    with patch.object(orchestrator.llm, "chat", side_effect=fake_chat), \
         patch.object(orchestrator.llm, "stream", side_effect=fake_stream):
        events = collect("what is 2+2")

    order = [e["type"] if e["type"] != "stage" else e["stage"] for e in events]
    assert order.index("working") < order.index("delta")


# -- the two paths cannot drift --------------------------------------------


def test_streamed_and_unstreamed_agree(talking):
    """handle_turn is run_turn with the events discarded, so this must hold."""
    streamed = final(collect("tell me something", session_id="a")).reply
    plain = orchestrator.handle_turn("tell me something", session_id="b").reply
    assert streamed == plain


def test_the_unstreamed_path_makes_no_streaming_calls(workspace):
    """It still goes through llm.chat, so the existing test suite stays honest."""
    def fake_chat(messages, *, json_mode=False, **kwargs):
        if json_mode:
            return llm.LLMReply(text='{"skills": [], "reply": null}', raw={})
        return llm.LLMReply(text="Fine.", raw={})

    with patch.object(orchestrator.llm, "chat", side_effect=fake_chat), \
         patch.object(orchestrator.llm, "stream") as streamer:
        orchestrator.handle_turn("say something", session_id="plain")

    streamer.assert_not_called()


# -- failure ---------------------------------------------------------------


def test_a_dead_model_still_produces_a_done(workspace):
    def dies(messages, *, json_mode=False, **kwargs):
        raise llm.LLMUnavailable("I can't reach the language model.")

    with patch.object(orchestrator.llm, "chat", side_effect=dies):
        events = collect("anything")

    result = final(events)
    assert result.error == "llm_unavailable"
    assert "can't reach" in result.reply


def test_a_stream_that_dies_partway_keeps_what_arrived(workspace):
    """Silently truncating a reply looks exactly like the model finishing."""
    def fake_chat(messages, *, json_mode=False, **kwargs):
        if json_mode:
            return llm.LLMReply(
                text='{"skills": [{"name": "utils.calculate", '
                     '"args": {"expression": "2+2"}}], "reply": null}',
                raw={},
            )
        return llm.LLMReply(text="", raw={})

    def dies_partway(messages, **kwargs):
        yield "It is "
        raise llm.LLMUnavailable("connection lost")

    with patch.object(orchestrator.llm, "chat", side_effect=fake_chat), \
         patch.object(orchestrator.llm, "stream", side_effect=dies_partway):
        events = collect("what is 2+2")

    # The skill ran, so its result must survive the phrasing pass dying.
    result = final(events)
    assert result.skill_calls
    assert result.reply, "a failed phrasing pass must not swallow completed work"

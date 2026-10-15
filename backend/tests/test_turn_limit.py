"""What happens to the skills a turn does not get to — MAX_SKILLS_PER_TURN.

The limit itself is a reasonable bound on a runaway tool loop. How it was
applied was not: `calls[:3]`, and nothing said a word. The router would ask
for five, three would run, and the synthesis pass -- which only ever sees
results -- would describe those three as though they were the whole request.
A turn that quietly does part of what was asked and reports it as all of it
is worse than one that fails, because the user has no reason to check.

Two places skills go missing, and the second is the easier to overlook:
over the limit, and behind an action still waiting for approval.
"""

from __future__ import annotations

import json

from app.brain import llm, orchestrator, prompts
from app.skills.registry import catalog

from .conftest import make_files


def stub_llm(monkeypatch, *replies):
    queue = list(replies)
    seen: list[dict] = []

    def fake_chat(messages, *, json_mode=False, temperature=None, settings=None):
        seen.append({"json_mode": json_mode, "messages": messages})
        return llm.LLMReply(text=queue.pop(0) if queue else replies[-1], raw={})

    monkeypatch.setattr(llm, "chat", fake_chat)
    return seen


def route(skills):
    return json.dumps({"skills": skills, "reply": None})


def five_harmless_calls():
    """More than the limit, none of them needing approval.

    All read-only, so nothing parks and the turn runs to the end -- which is
    the case where silent truncation was invisible.
    """
    return [
        {"name": "utils.time", "args": {}},
        {"name": "utils.calculate", "args": {"expression": "2+2"}},
        {"name": "memory.list", "args": {}},
        {"name": "utils.calculate", "args": {"expression": "3+3"}},
        {"name": "planning.briefing", "args": {}},
    ]


# -- the router is told, so it usually does not ask for too many ----------


def test_the_router_prompt_states_the_limit(workspace):
    prompt = prompts.router_prompt(catalog(), orchestrator.MAX_SKILLS_PER_TURN)

    assert f"more than {orchestrator.MAX_SKILLS_PER_TURN} tools" in prompt


def test_the_limit_in_the_prompt_is_the_limit_that_is_enforced(workspace):
    """One number, not two.

    A prompt that promises four while the slice takes three is the same bug
    with extra steps, and nothing else would notice.
    """
    prompt = prompts.router_prompt(catalog(), 7)

    assert "more than 7 tools" in prompt
    assert "more than 3 tools" not in prompt


# -- and when it asks anyway, the leftovers are not silent ----------------


def test_only_the_first_three_run(workspace, monkeypatch):
    stub_llm(monkeypatch, route(five_harmless_calls()), "Here you go.")

    result = orchestrator.handle_turn("do five things", "t")

    assert len(result.skill_calls) == orchestrator.MAX_SKILLS_PER_TURN


def test_the_ones_that_did_not_run_are_named(workspace, monkeypatch):
    stub_llm(monkeypatch, route(five_harmless_calls()), "Here you go.")

    result = orchestrator.handle_turn("do five things", "t")

    assert result.not_run == ["utils.calculate", "planning.briefing"]
    assert result.to_dict()["not_run"] == ["utils.calculate", "planning.briefing"]


def test_the_model_writing_the_reply_is_told_what_was_dropped(workspace, monkeypatch):
    """The reply is written from the results alone.

    Without this the synthesis pass has no way to know anything is missing,
    so it cannot mention it however well the system prompt is worded.
    """
    seen = stub_llm(monkeypatch, route(five_harmless_calls()), "Here you go.")

    orchestrator.handle_turn("do five things", "t")

    synthesis = seen[-1]["messages"][-1]["content"]
    assert "did NOT run" in synthesis
    assert "planning.briefing" in synthesis


def test_a_turn_within_the_limit_says_nothing_about_leftovers(workspace, monkeypatch):
    """No apology when nothing was dropped.

    The usual turn is one or two skills, and it must not pick up a trailing
    caveat about work that was never asked for.
    """
    seen = stub_llm(
        monkeypatch,
        route([{"name": "utils.calculate", "args": {"expression": "17*23"}}]),
        "That comes to 391.",
    )

    result = orchestrator.handle_turn("what's 17 times 23", "t")

    assert result.not_run == []
    assert "did NOT run" not in seen[-1]["messages"][-1]["content"]


def test_the_user_is_told_even_when_the_model_dies(workspace, monkeypatch):
    """The path where it matters most.

    With no model there is nothing to notice the omission on the user's
    behalf, so the fallback summary has to carry it itself.
    """
    calls = {"n": 0}

    def flaky(messages, *, json_mode=False, temperature=None, settings=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return llm.LLMReply(text=route(five_harmless_calls()), raw={})
        raise llm.LLMUnavailable("ollama is not running")

    monkeypatch.setattr(llm, "chat", flaky)

    result = orchestrator.handle_turn("do five things", "t")

    assert "didn't get to" in result.reply
    assert "planning.briefing" in result.reply


# -- the other way work goes missing --------------------------------------


def test_work_abandoned_behind_a_parked_action_is_named_too(workspace, monkeypatch):
    """Stopping at a confirmation abandons the rest of the batch.

    That is deliberate -- a "no" must not find half the work already done --
    but the abandoned calls are just as absent from the reply as the ones
    over the limit, and were just as unmentioned.
    """
    make_files(workspace, ["a.pdf"])
    stub_llm(
        monkeypatch,
        route(
            [
                {"name": "system.organize_folder", "args": {"folder": str(workspace)}},
                {"name": "planning.add_task", "args": {"text": "not created"}},
                {"name": "utils.time", "args": {}},
            ]
        ),
    )

    result = orchestrator.handle_turn("tidy up, add a task, tell me the time", "t")

    assert result.needs_confirmation
    assert result.not_run == ["planning.add_task", "utils.time"]


def test_a_parked_action_on_its_own_leaves_nothing_unsaid(workspace, monkeypatch):
    make_files(workspace, ["a.pdf"])
    stub_llm(
        monkeypatch,
        route([{"name": "system.organize_folder", "args": {"folder": str(workspace)}}]),
    )

    result = orchestrator.handle_turn("tidy up", "t")

    assert result.needs_confirmation
    assert result.not_run == []

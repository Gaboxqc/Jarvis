"""Memory, time parsing, scheduling and the skill contract.

REQ-6, REQ-7, REQ-9, REQ-10, REQ-18, REQ-33.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import db
from app.actions import gate
from app.memory import long_term, short_term
from app.scheduler import service as scheduler
from app.scheduler import store as sched_store
from app.skills.base import SkillContext, SkillError
from app.skills.planning import timeparse
from app.skills.registry import catalog, load_skills


# -- long-term memory (REQ-7) ---------------------------------------------


def test_memory_survives_and_is_retrievable_by_topic(workspace):
    long_term.add("The user's client work lives in D:/work/clients", "fact")
    long_term.add("The user is allergic to shellfish", "fact")

    hits = long_term.relevant("where is my client work again")

    assert hits
    assert "D:/work/clients" in hits[0].text


def test_restating_a_fact_updates_rather_than_duplicating(workspace):
    long_term.add("The user prefers replies kept short", "preference")
    long_term.add("The user prefers replies kept short and direct", "preference")

    assert len(long_term.all_facts()) == 1


def test_unrelated_facts_are_not_returned(workspace):
    long_term.add("The user is allergic to shellfish", "fact")
    assert long_term.relevant("what is the capital of France") == []


def test_forgetting_is_undoable(workspace):
    """Forgetting is not gated — it is reported and reversible instead."""
    long_term.add("Temporary fact about scheduling", "fact")

    executed = gate.submit("memory.forget", {"query": "Temporary fact"}, SkillContext())
    assert executed.status == gate.EXECUTED
    assert long_term.all_facts() == []

    from app.actions import undo

    result = undo.undo_last()
    assert result.ok
    assert len(long_term.all_facts()) == 1


# -- short-term memory (REQ-6) --------------------------------------------


def test_conversation_window_returns_turns_in_order(workspace):
    short_term.record("s1", "user", "first")
    short_term.record("s1", "assistant", "second")
    short_term.record("s1", "user", "third")

    window = short_term.window("s1")

    assert [t.text for t in window] == ["first", "second", "third"]


def test_window_is_cut_at_an_idle_gap(workspace):
    short_term.record("s1", "user", "ancient")
    short_term.record("s1", "assistant", "also ancient")
    old = (datetime.now(timezone.utc) - timedelta(hours=5)).replace(microsecond=0).isoformat()
    db.execute("UPDATE conversation_turns SET ts = ? WHERE session_id = 's1'", (old,))

    short_term.record("s1", "user", "current")

    window = short_term.window("s1")
    assert [t.text for t in window] == ["current"]


def test_sessions_do_not_leak_into_each_other(workspace):
    short_term.record("a", "user", "session a message")
    short_term.record("b", "user", "session b message")

    assert [t.text for t in short_term.window("a")] == ["session a message"]


# -- time parsing (REQ-9) -------------------------------------------------


NOW = datetime(2026, 2, 5, 14, 30, tzinfo=timezone.utc)  # a Thursday


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("in 20 minutes", datetime(2026, 2, 5, 14, 50, tzinfo=timezone.utc)),
        ("in 2 hours", datetime(2026, 2, 5, 16, 30, tzinfo=timezone.utc)),
        ("in an hour", datetime(2026, 2, 5, 15, 30, tzinfo=timezone.utc)),
        ("tomorrow at 9am", datetime(2026, 2, 6, 9, 0, tzinfo=timezone.utc)),
        ("tomorrow", datetime(2026, 2, 6, 9, 0, tzinfo=timezone.utc)),
        ("today at 18:00", datetime(2026, 2, 5, 18, 0, tzinfo=timezone.utc)),
        ("monday at 10:00", datetime(2026, 2, 9, 10, 0, tzinfo=timezone.utc)),
        ("2026-12-25 at 08:00", datetime(2026, 12, 25, 8, 0, tzinfo=timezone.utc)),
    ],
)
def test_time_phrases_resolve_correctly(phrase, expected):
    parsed = timeparse.parse(phrase, now=NOW)
    assert parsed is not None, phrase
    assert parsed.when == expected


def test_recurring_phrases_carry_a_recurrence_rule():
    parsed = timeparse.parse("every weekday at 18:00", now=NOW)

    assert parsed is not None
    assert parsed.recurrence["type"] == "weekdays"
    assert parsed.when == datetime(2026, 2, 5, 18, 0, tzinfo=timezone.utc)
    assert "every weekday" in parsed.describe()


def test_unparseable_time_returns_none_rather_than_guessing():
    assert timeparse.parse("sometime soonish", now=NOW) is None
    assert timeparse.parse("", now=NOW) is None


def test_recurrence_rolls_forward_to_the_next_weekday():
    friday_evening = datetime(2026, 2, 6, 18, 0, tzinfo=timezone.utc)
    following = timeparse.next_occurrence({"type": "weekdays", "time": "18:00"}, friday_evening)
    assert following.weekday() == 0  # Monday, not Saturday


# -- reminders (REQ-9) ----------------------------------------------------


def test_reminder_states_the_resolved_time(workspace):
    outcome = gate.submit(
        "planning.add_reminder", {"what": "call the dentist", "when": "in 30 minutes"},
        SkillContext(),
    )

    assert outcome.status == gate.EXECUTED
    # REQ-9: the resolved time is echoed, not just "ok".
    assert "at" in outcome.message and "call the dentist" in outcome.message


def test_reminder_in_the_past_is_refused(workspace):
    outcome = gate.submit(
        "planning.add_reminder", {"what": "too late", "when": "2020-01-01"}, SkillContext()
    )
    assert outcome.status == gate.FAILED
    assert "already passed" in (outcome.error or "")


def test_a_reminder_missed_while_closed_is_delivered_with_its_original_time(workspace):
    due = datetime.now(timezone.utc) - timedelta(hours=3)
    sched_store.add(kind="reminder", label="take the bins out", fire_at=due)

    deliveries = scheduler.collect_due()

    assert len(deliveries) == 1
    assert deliveries[0].was_missed
    assert "was due" in deliveries[0].message()
    assert "take the bins out" in deliveries[0].message()


def test_a_delivered_one_off_reminder_does_not_fire_twice(workspace):
    sched_store.add(
        kind="reminder", label="once only",
        fire_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )

    assert len(scheduler.collect_due()) == 1
    assert scheduler.collect_due() == []


def test_a_recurring_reminder_reschedules_itself(workspace):
    sched_store.add(
        kind="reminder", label="daily standup",
        fire_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        recurrence={"type": "daily", "time": "09:00"},
    )

    assert len(scheduler.collect_due()) == 1
    assert scheduler.collect_due() == []

    remaining = sched_store.active_items()
    assert len(remaining) == 1
    assert remaining[0].next_fire_at > datetime.now(timezone.utc)


# -- tasks (REQ-10) -------------------------------------------------------


def test_tasks_are_mirrored_to_readable_markdown(workspace):
    from app.settings import data_dir

    gate.submit("planning.add_task", {"text": "renew the passport #admin"}, SkillContext())

    mirror = (data_dir() / "tasks.md").read_text(encoding="utf-8")
    assert "renew the passport" in mirror
    assert "#admin" in mirror


def test_completing_a_task_is_undoable(workspace):
    gate.submit("planning.add_task", {"text": "water the plants"}, SkillContext())
    done = gate.submit("planning.complete_task", {"which": "water"}, SkillContext())
    assert done.status == gate.EXECUTED

    from app.actions import undo

    assert undo.undo_last().ok
    listed = gate.submit("planning.list_tasks", {}, SkillContext())
    assert "water the plants" in listed.message


def test_an_ambiguous_match_asks_instead_of_guessing(workspace):
    gate.submit("planning.add_task", {"text": "call mum"}, SkillContext())
    gate.submit("planning.add_task", {"text": "call the bank"}, SkillContext())

    outcome = gate.submit("planning.complete_task", {"which": "call"}, SkillContext())

    assert outcome.status == gate.FAILED
    assert "Which one" in (outcome.error or "")


# -- utilities (REQ-18) ---------------------------------------------------


def test_calculator_is_exact(workspace):
    outcome = gate.submit("utils.calculate", {"expression": "(1200 * 0.21) + 45"}, SkillContext())
    assert outcome.result.data["value"] == pytest.approx(297.0)


@pytest.mark.parametrize(
    "expression",
    ["__import__('os').system('dir')", "open('secret.txt').read()", "x + 1", "[].__class__"],
)
def test_calculator_refuses_anything_that_is_not_arithmetic(workspace, expression):
    outcome = gate.submit("utils.calculate", {"expression": expression}, SkillContext())
    assert outcome.status == gate.FAILED


def test_unit_conversion(workspace):
    outcome = gate.submit(
        "utils.convert", {"value": 180, "from_unit": "lb", "to_unit": "kg"}, SkillContext()
    )
    assert outcome.status == gate.EXECUTED
    assert outcome.result.data["value"] == pytest.approx(81.6, abs=0.2)


# -- registry contract (REQ-33) -------------------------------------------


def test_every_registered_skill_satisfies_the_contract(workspace):
    for name, skill in load_skills().items():
        assert skill.description, f"{name} has no description"
        if skill.reversible:
            assert type(skill).undo is not type(skill).__mro__[-2].undo or hasattr(skill, "undo")
        if skill.consequential:
            preview = skill.preview.__qualname__
            assert not preview.startswith("Skill."), f"{name} has no real preview"


def test_catalog_is_what_the_router_sees(workspace):
    entries = catalog()
    assert entries
    for entry in entries:
        assert {"name", "description", "consequential", "parameters"} <= entry.keys()


def test_a_skill_declaring_reversible_without_undo_is_rejected(workspace):
    from app.skills import registry
    from app.skills.base import Skill

    class Broken(Skill):
        name = "test.broken"
        description = "Claims to be reversible but is not."
        reversible = True

        def run(self, args, ctx):
            ...

    with pytest.raises(SkillError, match="does not implement undo"):
        registry._validate(Broken())

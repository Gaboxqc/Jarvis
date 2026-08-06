"""Focus sessions — REQ-23, REQ-12."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import focus
from app.actions import gate
from app.scheduler import service as scheduler
from app.scheduler import store as sched_store
from app.skills.base import SkillContext


def test_starting_a_session_with_nothing_to_close_does_not_ask(workspace):
    """Severity is per call: no running apps means nothing is at stake."""
    outcome = gate.submit("system.start_focus", {"minutes": 25}, SkillContext())

    assert outcome.status == gate.EXECUTED
    assert focus.is_active()


def test_a_session_reports_time_remaining(workspace):
    focus.start(30)
    state = focus.state()

    assert state.active
    assert 28 <= state.minutes_left <= 30
    assert "30 minutes" in state.describe() or "29 minutes" in state.describe()


def test_a_session_expires_on_its_own(workspace):
    focus.start(1)
    assert focus.is_active()

    # Expiry is evaluated from the clock, not a timer, so a stalled thread or a
    # sleeping machine cannot leave a session running past its duration.
    focus._until = datetime.now(timezone.utc) - timedelta(seconds=1)

    assert not focus.is_active()


def test_reminders_are_held_during_a_session_not_dropped(workspace):
    """REQ-23/REQ-12 — suppressed, then delivered afterwards."""
    sched_store.add(
        kind="reminder",
        label="stand up and stretch",
        fire_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    focus.start(25)

    assert scheduler.collect_due() == []

    focus.end()
    deliveries = scheduler.collect_due()

    assert len(deliveries) == 1
    assert "stand up and stretch" in deliveries[0].message()


def test_ending_a_session_early_works(workspace):
    focus.start(25)
    outcome = gate.submit("system.end_focus", {}, SkillContext())

    assert outcome.status == gate.EXECUTED
    assert not focus.is_active()


def test_ending_when_none_is_running_is_not_an_error(workspace):
    outcome = gate.submit("system.end_focus", {}, SkillContext())

    assert outcome.status == gate.EXECUTED
    assert "No focus session" in outcome.message


def test_a_second_session_cannot_be_started_over_a_running_one(workspace):
    focus.start(25)

    outcome = gate.submit("system.start_focus", {"minutes": 10}, SkillContext())

    assert outcome.status == gate.FAILED
    assert "already running" in (outcome.error or "")


def test_a_session_pauses_document_indexing(workspace):
    from app.index import scanner

    focus.start(25)
    assert scanner.is_paused()
    assert scanner.should_defer() == "a focus session is active"

    focus.end()
    assert not scanner.is_paused()


def test_direct_requests_still_work_during_a_session(workspace):
    """Focus silences the assistant's interruptions, not the assistant."""
    focus.start(25)

    outcome = gate.submit("utils.calculate", {"expression": "6*7"}, SkillContext())

    assert outcome.status == gate.EXECUTED
    assert "42" in outcome.message

"""Routines — REQ-12, REQ-24, REQ-25.

`KIND_ROUTINE` sat reserved in scheduler/store.py from the beginning, filtered
out of reminder listings, created by nothing. The reason it stayed unbuilt is
visible in the requirement:

    WHERE a routine includes an action classed as consequential, THE SYSTEM
    SHALL require the user to approve that action at routine-creation time, and
    SHALL re-prompt if the routine is later edited.

A scheduler that can run gated actions is a way around the Action Gate unless
that sentence holds exactly. So most of this file is adversarial: it is a set of
attempts to get a consequential action to run without the user having agreed to
that action, in those arguments, in that routine.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.actions import gate, journal
from app.scheduler import routines, service, store
from app.skills.base import SkillContext
from tests.conftest import performed


def soon() -> datetime:
    return (datetime.now(timezone.utc) + timedelta(minutes=5)).astimezone()


def past() -> datetime:
    return (datetime.now(timezone.utc) - timedelta(minutes=5)).astimezone()


GATED = {"skill": "test.gated", "args": {"label": "send the mail"}}
HARMLESS = {"skill": "utils.calculate", "args": {"expression": "2+2"}}


@pytest.fixture
def client(workspace):
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


# -- creating --------------------------------------------------------------


def test_a_routine_of_harmless_steps_is_not_worth_interrupting_anyone_for(workspace):
    outcome = gate.submit(
        "planning.add_routine",
        {"name": "Morning", "when": "every weekday at 9am", "actions": [HARMLESS]},
        SkillContext(),
    )

    assert outcome.status == gate.EXECUTED
    assert len(routines.all_routines()) == 1


def test_a_routine_containing_a_gated_action_asks_first(workspace):
    outcome = gate.submit(
        "planning.add_routine",
        {"name": "Standup", "when": "every weekday at 9am", "actions": [HARMLESS, GATED]},
        SkillContext(),
    )

    assert outcome.status == gate.NEEDS_CONFIRMATION
    assert routines.all_routines() == []


def test_the_preview_names_every_step_and_what_approving_means(workspace):
    outcome = gate.submit(
        "planning.add_routine",
        {"name": "Standup", "when": "every weekday at 9am", "actions": [HARMLESS, GATED]},
        SkillContext(),
    )

    preview = outcome.preview
    # The trigger, so nobody agrees to a schedule they did not read.
    assert "09:00" in preview
    # Each step in the skill's own words, not a count.
    assert "gated thing" in preview
    assert "send the mail" in preview
    # And what saying yes buys, named rather than numbered.
    assert "test.gated" in preview
    assert "without asking again" in preview


def test_a_routine_that_cannot_run_is_refused_while_the_user_is_still_here(workspace):
    """Rather than at 9am on a Tuesday."""
    outcome = gate.submit(
        "planning.add_routine",
        {"name": "Broken", "when": "every weekday at 9am",
         "actions": [{"skill": "no.such.skill", "args": {}}]},
        SkillContext(),
    )

    assert outcome.status == gate.FAILED
    assert "not an available skill" in (outcome.error or "")


# -- running ---------------------------------------------------------------


def _approved_routine(**overrides):
    kwargs = {
        "label": "Standup",
        "fire_at": soon(),
        "steps": [HARMLESS, GATED],
        "approved": True,
    }
    kwargs.update(overrides)
    return routines.create(**kwargs)


def test_an_approved_routine_runs_its_gated_step(workspace):
    item = _approved_routine()

    outcome = routines.run(item)

    assert outcome["ran"] == 2
    assert outcome["skipped"] == 0
    assert performed == ["send the mail"]


def test_every_step_is_journalled_like_any_other_action(workspace):
    """REQ-25. A routine that acts without leaving a record would be the one
    part of the app whose history has a hole in it."""
    item = _approved_routine()

    routines.run(item)

    records = journal.history(limit=20)
    assert {r.skill_name for r in records} >= {"test.gated", "utils.calculate"}
    assert all(r.status == journal.STATUS_EXECUTED for r in records)


def test_a_run_shares_one_batch_so_undo_takes_back_the_routine(workspace):
    from app.actions import undo

    item = routines.create(label="Standup", fire_at=soon(), steps=[GATED], approved=True)
    routines.run(item)

    result = undo.undo_last()

    assert result.ok
    assert performed == [], "undo took back the routine's own step"


def test_undoing_a_routine_with_an_irreversible_step_says_so(workspace):
    """One batch, and an honest report of the part that could not come back --
    rather than a cheerful "undone" covering a step that is still done."""
    from app.actions import undo

    routines.run(_approved_routine())   # a calculation and a gated action

    result = undo.undo_last()

    assert result.ok is False
    assert performed == [], "the reversible step was still taken back"
    assert "not reversible" in result.message


# -- approval, and the ways it should not be inherited ---------------------


def test_an_unapproved_routine_skips_the_gated_step_and_runs_the_rest(workspace):
    item = routines.create(
        label="Standup", fire_at=soon(), steps=[HARMLESS, GATED], approved=False
    )

    outcome = routines.run(item)

    assert outcome["ran"] == 1
    assert outcome["skipped"] == 1
    assert performed == []
    assert outcome["needs_approval"] is True


def test_an_unapproved_gated_step_is_declined_not_left_pending(workspace):
    """A confirmation nobody will ever see is a prompt that expires quietly and
    an action that looks like it might still happen."""
    item = routines.create(label="Standup", fire_at=soon(), steps=[GATED], approved=False)

    routines.run(item)

    assert journal.pending() == []
    assert [r.status for r in journal.history(limit=5)] == [journal.STATUS_DECLINED]


def test_editing_a_routine_revokes_its_approval(workspace):
    """The second half of REQ-12, and the reason the fingerprint exists."""
    item = _approved_routine()
    assert routines.needs_approval(item) is False

    edited = routines.set_steps(
        item.id, [HARMLESS, {"skill": "test.gated", "args": {"label": "send something else"}}]
    )

    assert edited is not None
    assert routines.needs_approval(edited) is True
    routines.run(edited)
    assert performed == [], "an edited routine ran a step nobody approved"


def test_re_approving_after_an_edit_lets_it_run_again(workspace):
    item = _approved_routine()
    routines.set_steps(item.id, [{"skill": "test.gated", "args": {"label": "the new thing"}}])

    approved = routines.approve(item.id)

    assert approved is not None
    assert routines.needs_approval(approved) is False
    routines.run(approved)
    assert performed == ["the new thing"]


def test_approval_does_not_leak_to_a_different_routine(workspace):
    """A routine's approval is narrower than a pre-approval: these arguments,
    in this routine."""
    _approved_routine()
    other = routines.create(label="Other", fire_at=soon(), steps=[GATED], approved=False)

    outcome = routines.run(other)

    assert outcome["skipped"] == 1


def test_approval_does_not_become_a_standing_approval_for_the_skill(workspace):
    """Running an approved routine must not make the same action free in
    conversation afterwards."""
    routines.run(_approved_routine())
    performed.clear()

    outcome = gate.submit("test.gated", {"label": "asked in chat"}, SkillContext())

    assert outcome.status == gate.NEEDS_CONFIRMATION
    assert performed == []


def test_creating_a_routine_cannot_be_pre_approved(workspace):
    """A standing approval for "create routines" would let one containing a
    send be created without its preview ever being shown."""
    from app.skills.registry import get_skill

    skill = get_skill("planning.add_routine")
    assert skill is not None
    assert skill.allow_pre_approval is False

    gate.grant_pre_approval("planning.add_routine")
    outcome = gate.submit(
        "planning.add_routine",
        {"name": "Standup", "when": "every weekday at 9am", "actions": [GATED]},
        SkillContext(),
    )

    assert outcome.status == gate.NEEDS_CONFIRMATION


def test_a_fingerprint_ignores_key_order_but_not_values(workspace):
    a = [{"skill": "test.gated", "args": {"label": "x", "other": 1}}]
    b = [{"skill": "test.gated", "args": {"other": 1, "label": "x"}}]
    c = [{"skill": "test.gated", "args": {"label": "y", "other": 1}}]

    assert routines.fingerprint(a) == routines.fingerprint(b)
    assert routines.fingerprint(a) != routines.fingerprint(c)


# -- the scheduler ---------------------------------------------------------


def test_a_due_routine_executes_rather_than_announcing_itself(workspace):
    """A routine is not a reminder. "Reminder: morning start" followed by
    nothing happening would be exactly backwards."""
    from app import notifications

    notifications.drain()
    routines.create(label="Standup", fire_at=past(), steps=[GATED], approved=True)

    service.tick()

    assert performed == ["send the mail"]
    queued = notifications.drain()
    assert len(queued) == 1
    assert queued[0].kind == "routine"
    assert "1 step(s) ran" in queued[0].body


def test_a_routine_that_raises_does_not_stop_the_scheduler(workspace, monkeypatch):
    routines.create(label="Standup", fire_at=past(), steps=[HARMLESS], approved=True)
    monkeypatch.setattr(routines, "run", lambda item: 1 / 0)

    # The thread that delivers every reminder in the app also runs this.
    service.tick()


def test_routines_stay_out_of_the_reminder_list(workspace):
    """They were filtered out before any existed; now that they do, it matters."""
    from app.skills.registry import get_skill

    routines.create(label="Standup", fire_at=soon(), steps=[HARMLESS], approved=True)
    store.add(kind=store.KIND_REMINDER, label="Call the dentist", fire_at=soon())

    skill = get_skill("planning.list_reminders")
    assert skill is not None
    listed = skill.run({}, SkillContext()).message

    assert "dentist" in listed
    assert "Standup" not in listed


# -- through the API -------------------------------------------------------


def test_the_api_lists_routines_with_their_steps_and_approval_state(client):
    item = routines.create(label="Standup", fire_at=soon(), steps=[GATED], approved=False)

    body = client.get("/routines").json()["routines"]

    assert len(body) == 1
    assert body[0]["id"] == item.id
    assert body[0]["needs_approval"] is True
    assert "gated thing" in body[0]["steps"][0]


def test_the_api_can_re_approve_and_delete(client):
    item = routines.create(label="Standup", fire_at=soon(), steps=[GATED], approved=False)

    assert client.post(f"/routines/{item.id}/approve").status_code == 200
    assert client.get("/routines").json()["routines"][0]["needs_approval"] is False

    assert client.delete(f"/routines/{item.id}").status_code == 200
    assert client.get("/routines").json()["routines"] == []


def test_running_one_by_hand_reports_what_happened(client):
    item = routines.create(label="Standup", fire_at=soon(), steps=[GATED], approved=True)

    body = client.post(f"/routines/{item.id}/run").json()

    assert body["ran"] == 1
    assert body["steps"][0]["skill"] == "test.gated"


def test_an_unknown_routine_is_a_404_rather_than_a_500(client):
    assert client.post("/routines/nope/approve").status_code == 404
    assert client.post("/routines/nope/run").status_code == 404
    assert client.delete("/routines/nope").status_code == 404

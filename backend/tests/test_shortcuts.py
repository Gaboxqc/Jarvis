"""Named shortcuts — REQ-22, REQ-24, REQ-25.

    THE SYSTEM SHALL let the user define named shortcuts for multi-step
    sequences ("work setup" → open these three apps).

A shortcut is a routine with a name where its trigger time would be, so the
approval machinery is shared and test_routines.py already covers most of it.
What is tested here is the part that is genuinely different: no trigger, name
lookup, and the several ways a shortcut must not become a way to run gated
actions without having agreed to them.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.actions import gate, journal
from app.scheduler import routines, sequences, service, shortcuts, store
from app.skills.base import SkillContext
from tests.conftest import performed

GATED = {"skill": "test.gated", "args": {"label": "send the mail"}}
HARMLESS = {"skill": "utils.calculate", "args": {"expression": "2+2"}}


@pytest.fixture
def client(workspace):
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def add(name="Work setup", steps=(HARMLESS,), approved=True):
    return shortcuts.create(label=name, steps=list(steps), approved=approved)


# -- the thing that makes it a shortcut ------------------------------------


def test_a_shortcut_has_no_trigger_time(workspace):
    item = add()

    assert item.next_fire_at is None


def test_a_shortcut_never_fires_on_its_own(workspace):
    """The property the whole storage choice rests on. `due_items()` refuses
    rows with no time, so a shortcut can sit in the same table as the reminders
    forever without one."""
    add(steps=[GATED])

    service.tick()

    assert performed == []
    assert store.due_items() == []


def test_a_shortcut_is_not_a_reminder(workspace):
    from app.skills.registry import get_skill

    add(name="Work setup")
    store.add(kind=store.KIND_REMINDER, label="Call the dentist",
              fire_at=__import__("datetime").datetime.now().astimezone())

    listed = get_skill("planning.list_reminders").run({}, SkillContext()).message

    assert "dentist" in listed
    assert "Work setup" not in listed


def test_a_shortcut_is_not_a_routine(workspace):
    """They share a table and their machinery; they must not share a list."""
    add(name="Work setup")

    assert routines.all_routines() == []
    assert [i.label for i in shortcuts.all_shortcuts()] == ["Work setup"]


# -- creating --------------------------------------------------------------


def test_saving_a_harmless_shortcut_does_not_interrupt_anyone(workspace):
    outcome = gate.submit(
        "system.add_shortcut",
        {"name": "Work setup", "actions": [HARMLESS]},
        SkillContext(),
    )

    assert outcome.status == gate.EXECUTED
    assert len(shortcuts.all_shortcuts()) == 1


def test_a_shortcut_containing_a_gated_action_asks_first(workspace):
    outcome = gate.submit(
        "system.add_shortcut",
        {"name": "Wind down", "actions": [HARMLESS, GATED]},
        SkillContext(),
    )

    assert outcome.status == gate.NEEDS_CONFIRMATION
    assert shortcuts.all_shortcuts() == []


def test_the_preview_names_every_step_and_what_approving_buys(workspace):
    outcome = gate.submit(
        "system.add_shortcut",
        {"name": "Wind down", "actions": [HARMLESS, GATED]},
        SkillContext(),
    )

    preview = outcome.preview
    assert "Wind down" in preview
    assert "gated thing" in preview and "send the mail" in preview
    assert "test.gated" in preview
    assert "whenever you use the shortcut" in preview


def test_two_shortcuts_cannot_share_a_name(workspace):
    """The name is how one is invoked, so a duplicate is an ambiguity that would
    surface later as the wrong sequence running."""
    add(name="Work setup")

    with pytest.raises(sequences.SequenceError, match="already a shortcut"):
        add(name="work setup")


def test_creating_a_shortcut_cannot_be_pre_approved(workspace):
    from app.skills.registry import get_skill

    assert get_skill("system.add_shortcut").allow_pre_approval is False

    gate.grant_pre_approval("system.add_shortcut")
    outcome = gate.submit(
        "system.add_shortcut", {"name": "Wind down", "actions": [GATED]}, SkillContext()
    )

    assert outcome.status == gate.NEEDS_CONFIRMATION


# -- running by name -------------------------------------------------------


def test_running_one_by_name_runs_its_steps(workspace):
    add(name="Wind down", steps=[GATED])

    outcome = gate.submit("system.run_shortcut", {"name": "wind down"}, SkillContext())

    assert outcome.status == gate.EXECUTED
    assert performed == ["send the mail"]


def test_the_name_match_is_not_case_sensitive_but_is_exact_when_it_can_be(workspace):
    add(name="Work")
    add(name="Work setup")

    assert shortcuts.find("work").label == "Work"
    assert shortcuts.find("WORK SETUP").label == "Work setup"


def test_an_ambiguous_name_asks_rather_than_guesses(workspace):
    """Running the wrong multi-step sequence is exactly the mistake the gate
    exists to prevent; picking one would be making it on the user's behalf."""
    add(name="Morning routine")
    add(name="Morning coffee")

    outcome = gate.submit("system.run_shortcut", {"name": "morning"}, SkillContext())

    assert outcome.status == gate.FAILED
    assert "more than one" in (outcome.error or "")
    assert performed == []


def test_an_unknown_name_says_so(workspace):
    outcome = gate.submit("system.run_shortcut", {"name": "nothing"}, SkillContext())

    assert outcome.status == gate.FAILED
    assert performed == []


def test_a_run_is_journalled_and_undoable_as_one_batch(workspace):
    from app.actions import undo

    add(name="Wind down", steps=[GATED])
    gate.submit("system.run_shortcut", {"name": "wind down"}, SkillContext())

    result = undo.undo_last()

    assert result.ok
    assert performed == []


# -- approval, and the ways it must not be inherited -----------------------


def test_an_unapproved_shortcut_skips_its_gated_step(workspace):
    add(name="Wind down", steps=[HARMLESS, GATED], approved=False)

    outcome = shortcuts.run(shortcuts.find("wind down"))

    assert outcome["ran"] == 1
    assert outcome["skipped"] == 1
    assert performed == []


def test_editing_a_shortcut_revokes_its_approval(workspace):
    item = add(name="Wind down", steps=[GATED])
    assert shortcuts.needs_approval(item) is False

    edited = shortcuts.set_steps(
        item.id, [{"skill": "test.gated", "args": {"label": "something else"}}]
    )

    assert shortcuts.needs_approval(edited) is True
    shortcuts.run(edited)
    assert performed == [], "an edited shortcut ran a step nobody approved"


def test_re_approving_lets_it_run_again(workspace):
    item = add(name="Wind down", steps=[GATED])
    shortcuts.set_steps(item.id, [{"skill": "test.gated", "args": {"label": "the new thing"}}])

    approved = shortcuts.approve(item.id)

    shortcuts.run(approved)
    assert performed == ["the new thing"]


def test_running_a_shortcut_does_not_pre_approve_the_skill(workspace):
    """It must not make the same action free in conversation afterwards."""
    add(name="Wind down", steps=[GATED])
    shortcuts.run(shortcuts.find("wind down"))
    performed.clear()

    outcome = gate.submit("test.gated", {"label": "asked in chat"}, SkillContext())

    assert outcome.status == gate.NEEDS_CONFIRMATION
    assert performed == []


def test_approval_does_not_leak_between_a_shortcut_and_a_routine(workspace):
    """They share a table, a payload shape and their machinery. They must not
    share an approval."""
    from datetime import datetime, timedelta, timezone

    add(name="Wind down", steps=[GATED], approved=True)
    routine = routines.create(
        label="Wind down",
        fire_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).astimezone(),
        steps=[GATED],
        approved=False,
    )

    outcome = routines.run(routine)

    assert outcome["skipped"] == 1
    assert performed == []


def test_an_unapproved_step_is_declined_not_left_pending(workspace):
    add(name="Wind down", steps=[GATED], approved=False)

    shortcuts.run(shortcuts.find("wind down"))

    assert journal.pending() == []
    assert [r.status for r in journal.history(limit=5)] == [journal.STATUS_DECLINED]


# -- listing and deleting --------------------------------------------------


def test_listing_says_which_ones_need_approving_again(workspace):
    item = add(name="Wind down", steps=[GATED])
    shortcuts.set_steps(item.id, [{"skill": "test.gated", "args": {"label": "changed"}}])

    from app.skills.registry import get_skill

    listed = get_skill("system.list_shortcuts").run({}, SkillContext()).message

    assert "Wind down" in listed
    assert "needs approving again" in listed


def test_deleting_one_by_name_and_putting_it_back(workspace):
    from app.actions import undo

    add(name="Work setup")

    gate.submit("system.delete_shortcut", {"name": "work setup"}, SkillContext())
    assert shortcuts.all_shortcuts() == []

    undo.undo_last()
    assert [i.label for i in shortcuts.all_shortcuts()] == ["Work setup"]


# -- through the API -------------------------------------------------------


def test_the_api_lists_runs_approves_and_deletes(client):
    item = add(name="Wind down", steps=[GATED], approved=False)

    listed = client.get("/shortcuts").json()["shortcuts"]
    assert len(listed) == 1
    assert listed[0]["needs_approval"] is True
    assert "gated thing" in listed[0]["steps"][0]

    assert client.post(f"/shortcuts/{item.id}/approve").status_code == 200
    body = client.post(f"/shortcuts/{item.id}/run").json()
    assert body["ran"] == 1

    assert client.delete(f"/shortcuts/{item.id}").status_code == 200
    assert client.get("/shortcuts").json()["shortcuts"] == []


def test_an_unknown_shortcut_is_a_404(client):
    assert client.post("/shortcuts/nope/run").status_code == 404
    assert client.post("/shortcuts/nope/approve").status_code == 404
    assert client.delete("/shortcuts/nope").status_code == 404


def test_a_routine_id_is_not_a_shortcut_id(client):
    """Both live in scheduled_items, so the endpoints have to check the kind
    rather than just the id."""
    from datetime import datetime, timedelta, timezone

    routine = routines.create(
        label="Morning",
        fire_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).astimezone(),
        steps=[HARMLESS],
        approved=True,
    )

    assert client.post(f"/shortcuts/{routine.id}/run").status_code == 404
    assert client.delete(f"/shortcuts/{routine.id}").status_code == 404
    # And still a perfectly good routine afterwards.
    assert routines.get(routine.id) is not None

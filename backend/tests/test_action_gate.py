"""Action Gate tests — REQ-24.

Two things are pinned here.

The leak tests are the important ones: everything else in the system assumes the
gate cannot be talked past, so that assumption is encoded directly.

The classification tests pin the other half — that the gate stays *quiet* for
low-stakes work. A gate that asks about everything gets clicked through without
reading, which costs more safety than it buys.

These use a test double (`test.gated`) rather than a real skill, so deciding
that some particular skill no longer deserves a prompt cannot silently delete
coverage of the gate itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import yaml

from app import db
from app.actions import gate, journal
from app.settings import reset_config_cache
from app.skills.base import SkillContext

from .conftest import performed


# -- what gets gated, and what does not -----------------------------------


def test_routine_skill_executes_without_confirmation(workspace):
    outcome = gate.submit("utils.calculate", {"expression": "2 + 2"}, SkillContext())

    assert outcome.status == gate.EXECUTED
    assert "4" in outcome.message


def test_storing_a_memory_does_not_interrupt_the_user(workspace):
    """REQ-7 asks for the write to be visible, not approved."""
    from app.memory import long_term

    outcome = gate.submit("memory.remember", {"text": "The user prefers short replies"},
                          SkillContext())

    assert outcome.status == gate.EXECUTED
    # Visible: the write is stated in the reply...
    assert "prefers short replies" in outcome.message
    # ...recorded...
    assert len(long_term.all_facts()) == 1
    # ...and reversible, which is the right cost for a low-stakes write.
    assert outcome.reversible


def test_adding_a_reminder_does_not_interrupt_the_user(workspace):
    outcome = gate.submit(
        "planning.add_reminder", {"what": "stretch", "when": "in 20 minutes"}, SkillContext()
    )
    assert outcome.status == gate.EXECUTED


def test_volume_is_routine_but_sleeping_the_machine_is_not(workspace):
    """Same skill, different stakes — severity is decided per call."""
    from app.skills.registry import get_skill

    control = get_skill("system.control")

    assert control.severity({"action": "volume_down"}) == "routine"
    assert control.severity({"action": "mute"}) == "routine"
    assert control.severity({"action": "list_running"}) == "routine"
    assert control.severity({"action": "lock"}) == "consequential"
    assert control.severity({"action": "sleep"}) == "consequential"


def test_an_interrupting_system_action_is_still_gated(workspace):
    outcome = gate.submit("system.control", {"action": "lock"}, SkillContext())

    assert outcome.status == gate.NEEDS_CONFIRMATION
    assert "Lock the screen" in outcome.preview
    # And it is honest that this cannot be taken back.
    assert outcome.reversible is False


def test_a_gated_skill_is_parked_not_executed(workspace):
    outcome = gate.submit("test.gated", {"label": "alpha"}, SkillContext())

    assert outcome.status == gate.NEEDS_CONFIRMATION
    assert outcome.action_id
    # The preview names the actual content, not just the skill.
    assert "alpha" in outcome.preview
    assert performed == []


# -- the leak tests -------------------------------------------------------


def test_confirmation_executes_the_parked_action(workspace):
    parked = gate.submit("test.gated", {"label": "alpha"}, SkillContext())
    result = gate.confirm(parked.action_id, SkillContext())

    assert result.status == gate.EXECUTED
    assert performed == ["alpha"]


def test_confirming_one_action_does_not_authorise_another(workspace):
    """T3.2 — the invariant the whole trust model rests on.

    Two identical requests are parked. Approving the first must leave the second
    exactly as it was: still pending, still unexecuted.
    """
    first = gate.submit("test.gated", {"label": "alpha"}, SkillContext())
    second = gate.submit("test.gated", {"label": "beta"}, SkillContext())

    assert first.action_id != second.action_id

    gate.confirm(first.action_id, SkillContext())

    assert journal.get(second.action_id).status == journal.STATUS_PENDING
    assert performed == ["alpha"]


def test_a_consumed_action_id_cannot_be_replayed(workspace):
    parked = gate.submit("test.gated", {"label": "once"}, SkillContext())
    gate.confirm(parked.action_id, SkillContext())

    replay = gate.confirm(parked.action_id, SkillContext())

    assert replay.status == gate.FAILED
    assert "already" in (replay.error or "")
    assert performed == ["once"]


def test_expired_confirmation_is_refused(workspace):
    parked = gate.submit("test.gated", {"label": "stale"}, SkillContext())

    stale = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(microsecond=0).isoformat()
    db.execute("UPDATE action_records SET expires_at = ? WHERE id = ?", (stale, parked.action_id))

    result = gate.confirm(parked.action_id, SkillContext())

    assert result.status == gate.FAILED
    assert "expired" in (result.error or "").lower()
    assert performed == []


def test_declining_leaves_nothing_behind(workspace):
    parked = gate.submit("test.gated", {"label": "unwanted"}, SkillContext())
    outcome = gate.decline(parked.action_id)

    assert outcome.status == gate.DECLINED
    assert journal.get(parked.action_id).status == journal.STATUS_DECLINED
    assert performed == []

    # And it cannot be resurrected by confirming afterwards.
    assert gate.confirm(parked.action_id, SkillContext()).status == gate.FAILED
    assert performed == []


def test_unknown_skill_is_refused_not_invented(workspace):
    outcome = gate.submit("system.format_c_drive", {}, SkillContext())

    assert outcome.status == gate.FAILED
    assert "not an available skill" in (outcome.error or "")


# -- pre-approvals --------------------------------------------------------


def test_pre_approval_skips_the_prompt_and_is_revocable(workspace):
    gate.grant_pre_approval("test.gated")
    assert gate.submit("test.gated", {"label": "auto"}, SkillContext()).status == gate.EXECUTED

    gate.revoke_pre_approval("test.gated")
    assert gate.submit("test.gated", {"label": "asked"}, SkillContext()).status == gate.NEEDS_CONFIRMATION


def test_pre_approvals_are_listed_so_they_can_be_reviewed(workspace):
    gate.grant_pre_approval("test.gated")
    assert "test.gated" in [entry["skill"] for entry in gate.list_pre_approvals()]


# -- validation and availability ------------------------------------------


def test_missing_required_argument_fails_before_anything_runs(workspace):
    outcome = gate.submit("test.gated", {}, SkillContext())

    assert outcome.status == gate.FAILED
    assert "required" in (outcome.error or "")
    assert journal.pending() == []


def test_disabled_skill_is_not_reachable(workspace, config_file):
    from app.skills import registry

    raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    raw["skills"]["disabled"] = ["utils.calculate"]
    config_file.write_text(yaml.safe_dump(raw), encoding="utf-8")
    reset_config_cache()

    assert gate.submit("utils.calculate", {"expression": "1+1"}, SkillContext()).status == gate.FAILED


def test_privacy_switch_withdraws_a_networked_skill(workspace, config_file):
    from app.skills import registry

    raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    raw["privacy"]["allow_web_search"] = False
    config_file.write_text(yaml.safe_dump(raw), encoding="utf-8")
    reset_config_cache()

    assert "knowledge.web_search" not in {entry["name"] for entry in registry.catalog()}
    assert gate.submit("knowledge.web_search", {"query": "x"}, SkillContext()).status == gate.FAILED


def test_every_action_is_journalled_including_failures(workspace):
    gate.submit("utils.calculate", {"expression": "2+2"}, SkillContext())
    gate.submit("utils.calculate", {"expression": "1/0"}, SkillContext())

    statuses = {record.status for record in journal.history(limit=10)}
    assert journal.STATUS_EXECUTED in statuses
    assert journal.STATUS_FAILED in statuses

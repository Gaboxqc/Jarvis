"""Action Gate tests — REQ-24.

The leak tests are the important ones. Everything else in the system assumes the
gate cannot be talked past, so these encode that assumption directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import yaml

from app import db
from app.actions import gate, journal
from app.settings import reset_config_cache
from app.skills.base import SkillContext


def test_routine_skill_executes_without_confirmation(workspace):
    outcome = gate.submit("utils.calculate", {"expression": "2 + 2"}, SkillContext())

    assert outcome.status == gate.EXECUTED
    assert "4" in outcome.message


def test_consequential_skill_is_parked_not_executed(workspace):
    outcome = gate.submit("memory.remember", {"text": "The user drives a red car"}, SkillContext())

    assert outcome.status == gate.NEEDS_CONFIRMATION
    assert outcome.action_id
    # The preview has to name the actual content, not just the skill.
    assert "red car" in outcome.preview
    # Nothing was written.
    from app.memory import long_term

    assert long_term.all_facts() == []


def test_confirmation_executes_the_parked_action(workspace):
    parked = gate.submit("memory.remember", {"text": "The user drives a red car"}, SkillContext())
    result = gate.confirm(parked.action_id, SkillContext())

    assert result.status == gate.EXECUTED
    from app.memory import long_term

    assert len(long_term.all_facts()) == 1


def test_confirming_one_action_does_not_authorise_another(workspace):
    """T3.2 — the invariant the whole trust model rests on.

    Two identical consequential requests are parked. Approving the first must
    leave the second exactly as it was: still pending, still unexecuted.
    """
    first = gate.submit("memory.remember", {"text": "Fact number one"}, SkillContext())
    second = gate.submit("memory.remember", {"text": "Fact number two"}, SkillContext())

    assert first.action_id != second.action_id

    gate.confirm(first.action_id, SkillContext())

    second_record = journal.get(second.action_id)
    assert second_record.status == journal.STATUS_PENDING

    from app.memory import long_term

    stored = [f.text for f in long_term.all_facts()]
    assert stored == ["Fact number one"]


def test_a_consumed_action_id_cannot_be_replayed(workspace):
    parked = gate.submit("memory.remember", {"text": "Only once"}, SkillContext())
    gate.confirm(parked.action_id, SkillContext())

    replay = gate.confirm(parked.action_id, SkillContext())

    assert replay.status == gate.FAILED
    assert "already" in (replay.error or "")

    from app.memory import long_term

    assert len(long_term.all_facts()) == 1


def test_expired_confirmation_is_refused(workspace):
    parked = gate.submit("memory.remember", {"text": "Stale approval"}, SkillContext())

    stale = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(microsecond=0).isoformat()
    db.execute("UPDATE action_records SET expires_at = ? WHERE id = ?", (stale, parked.action_id))

    result = gate.confirm(parked.action_id, SkillContext())

    assert result.status == gate.FAILED
    assert "expired" in (result.error or "").lower()
    from app.memory import long_term

    assert long_term.all_facts() == []


def test_declining_leaves_nothing_behind(workspace):
    parked = gate.submit("memory.remember", {"text": "Not wanted"}, SkillContext())
    outcome = gate.decline(parked.action_id)

    assert outcome.status == gate.DECLINED
    assert journal.get(parked.action_id).status == journal.STATUS_DECLINED

    # And it cannot be resurrected by confirming afterwards.
    late = gate.confirm(parked.action_id, SkillContext())
    assert late.status == gate.FAILED


def test_unknown_skill_is_refused_not_invented(workspace):
    outcome = gate.submit("system.format_c_drive", {}, SkillContext())

    assert outcome.status == gate.FAILED
    assert "not an available skill" in (outcome.error or "")


def test_pre_approval_skips_the_prompt_and_is_revocable(workspace):
    gate.grant_pre_approval("memory.remember")
    outcome = gate.submit("memory.remember", {"text": "Auto approved"}, SkillContext())
    assert outcome.status == gate.EXECUTED

    gate.revoke_pre_approval("memory.remember")
    outcome = gate.submit("memory.remember", {"text": "Back to asking"}, SkillContext())
    assert outcome.status == gate.NEEDS_CONFIRMATION


def test_pre_approvals_are_listed_so_they_can_be_reviewed(workspace):
    gate.grant_pre_approval("memory.remember")
    listed = [entry["skill"] for entry in gate.list_pre_approvals()]
    assert "memory.remember" in listed


def test_missing_required_argument_fails_before_anything_runs(workspace):
    outcome = gate.submit("memory.remember", {}, SkillContext())

    assert outcome.status == gate.FAILED
    assert "required" in (outcome.error or "")


def test_disabled_skill_is_not_reachable(workspace, config_file):
    from app.skills import registry

    raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    raw["skills"]["disabled"] = ["utils.calculate"]
    config_file.write_text(yaml.safe_dump(raw), encoding="utf-8")
    reset_config_cache()
    registry.reset()
    registry.load_skills()

    outcome = gate.submit("utils.calculate", {"expression": "1+1"}, SkillContext())
    assert outcome.status == gate.FAILED


def test_privacy_switch_withdraws_a_networked_skill(workspace, config_file):
    from app.skills import registry

    raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    raw["privacy"]["allow_web_search"] = False
    config_file.write_text(yaml.safe_dump(raw), encoding="utf-8")
    reset_config_cache()
    registry.reset()
    registry.load_skills()

    assert "knowledge.web_search" not in {entry["name"] for entry in registry.catalog()}
    outcome = gate.submit("knowledge.web_search", {"query": "anything"}, SkillContext())
    assert outcome.status == gate.FAILED


def test_every_action_is_journalled_including_failures(workspace):
    gate.submit("utils.calculate", {"expression": "2+2"}, SkillContext())
    gate.submit("utils.calculate", {"expression": "1/0"}, SkillContext())

    history = journal.history(limit=10)
    statuses = {record.status for record in history}
    assert journal.STATUS_EXECUTED in statuses
    assert journal.STATUS_FAILED in statuses

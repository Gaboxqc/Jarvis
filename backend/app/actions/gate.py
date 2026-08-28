"""The Action Gate — REQ-24.

Nothing in Kai performs a side effect except through here. The rules, in order:

1. The skill decides whether approval is needed, per call, via `severity()`.
   The brain does not get a vote; a persuasive turn of phrase in the
   conversation cannot downgrade an action.

   Only genuinely costly actions are gated — irreversible loss, bulk changes to
   the user's files, or anything that interrupts what they are doing. Low-stakes
   writes are reported in the reply and left undoable instead. Confirming
   everything is not a safer default: it trains people to approve without
   reading, which is worse than not asking.
2. Approval is bound to one ActionRecord id. Confirming action A never
   authorizes action B, even for the identical skill with identical arguments
   one second later. `confirm()` takes an id, and the id is consumed.
3. Pre-approvals are per skill name, explicit, listed, and revocable. They are
   the only way to skip the prompt, and the user has to have set one up.
4. Whether an action is reversible is stated in the preview, before the user
   answers — not discovered afterwards.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from .. import db
from ..settings import load_config
from ..skills.base import Skill, SkillContext, SkillError, SkillResult
from ..skills.registry import get_skill
from . import journal

log = logging.getLogger(__name__)

EXECUTED = "executed"
NEEDS_CONFIRMATION = "needs_confirmation"
FAILED = "failed"
DECLINED = "declined"


@dataclass
class GateOutcome:
    status: str
    skill_name: str
    action_id: str | None = None
    preview: str = ""
    reversible: bool = False
    result: SkillResult | None = None
    error: str | None = None
    batch_id: str | None = None

    @property
    def message(self) -> str:
        if self.result is not None:
            return self.result.message
        return self.error or self.preview

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "skill": self.skill_name,
            "action_id": self.action_id,
            "preview": self.preview,
            "reversible": self.reversible,
            "message": self.message,
            "data": self.result.data if self.result else {},
            "error": self.error,
        }


# -- pre-approvals (REQ-24) ------------------------------------------------


def pre_approved_skills() -> set[str]:
    """Union of the config file list and anything granted at runtime."""
    names = set(load_config().actions.pre_approved)
    for row in db.query("SELECT skill_name FROM pre_approvals"):
        names.add(row["skill_name"])
    return names


def grant_pre_approval(skill_name: str) -> None:
    db.execute(
        "INSERT INTO pre_approvals(skill_name, granted_at) VALUES(?, ?) "
        "ON CONFLICT(skill_name) DO NOTHING",
        (skill_name, db.now()),
    )


def revoke_pre_approval(skill_name: str) -> None:
    db.execute("DELETE FROM pre_approvals WHERE skill_name = ?", (skill_name,))


def list_pre_approvals() -> list[dict[str, Any]]:
    from_config = [
        {"skill": name, "source": "config file", "granted_at": None}
        for name in load_config().actions.pre_approved
    ]
    from_db = [
        {"skill": row["skill_name"], "source": "granted in app", "granted_at": row["granted_at"]}
        for row in db.query("SELECT * FROM pre_approvals ORDER BY granted_at DESC")
    ]
    return from_config + from_db


# -- submission ------------------------------------------------------------


def submit(
    skill_name: str,
    args: dict[str, Any],
    ctx: SkillContext | None = None,
    *,
    batch_id: str | None = None,
) -> GateOutcome:
    """Route one skill call. Executes it, or parks it awaiting confirmation."""
    ctx = ctx or SkillContext()
    skill = get_skill(skill_name)
    if skill is None:
        # Either it does not exist, the user disabled it, or a privacy switch
        # withdrew it. All three are the same answer to the user: can't do that.
        return GateOutcome(
            status=FAILED,
            skill_name=skill_name,
            error=f"'{skill_name}' is not an available skill right now.",
        )

    # Validation and preview both happen before anything is journalled, so an
    # invalid request never becomes a confirmation prompt the user cannot act on.
    try:
        cleaned = skill.validate(args)
        preview = _safe_preview(skill, cleaned)
    except SkillError as exc:
        return GateOutcome(status=FAILED, skill_name=skill_name, error=str(exc))

    # Severity is asked of the skill per call, so arguments can downgrade it
    # (volume vs. sleep). The skill decides; the conversation cannot.
    severity = skill.severity(cleaned)

    if severity != "consequential":
        record = journal.create(
            skill_name=skill.name,
            params=cleaned,
            severity="routine",
            reversible=skill.reversible,
            preview=preview,
            status=journal.STATUS_PENDING,
            batch_id=batch_id,
        )
        return _execute(skill, record, ctx)

    # A skill can refuse to be pre-approvable at all. Sending a message is the
    # case that matters: REQ-14 requires per-message confirmation, so a standing
    # approval must not be able to satisfy it (REQ-24).
    if skill.name in pre_approved_skills() and skill.allow_pre_approval:
        record = journal.create(
            skill_name=skill.name,
            params=cleaned,
            severity="consequential",
            reversible=skill.reversible,
            preview=preview,
            status=journal.STATUS_PENDING,
            batch_id=batch_id,
        )
        log.info("Skipping confirmation for pre-approved skill %s", skill.name)
        return _execute(skill, record, ctx)

    ttl = load_config().actions.confirmation_ttl_minutes
    record = journal.create(
        skill_name=skill.name,
        params=cleaned,
        severity="consequential",
        reversible=skill.reversible,
        preview=preview,
        status=journal.STATUS_PENDING,
        batch_id=batch_id,
        ttl_minutes=ttl,
    )
    return GateOutcome(
        status=NEEDS_CONFIRMATION,
        skill_name=skill.name,
        action_id=record.id,
        preview=record.preview,
        reversible=skill.reversible,
        batch_id=record.batch_id,
    )


def confirm(action_id: str, ctx: SkillContext | None = None) -> GateOutcome:
    """Approve exactly one parked action.

    Takes an id, not a skill name and not "yes". That is the whole point: there
    is no call shape here that could approve an action the user has not seen.
    """
    ctx = ctx or SkillContext()
    journal.expire_stale()
    record = journal.get(action_id)

    if record is None:
        return GateOutcome(status=FAILED, skill_name="", error="That action no longer exists.")

    if record.status != journal.STATUS_PENDING:
        # Replay protection: an executed id is spent. Re-sending it does not run
        # the action a second time.
        return GateOutcome(
            status=FAILED,
            skill_name=record.skill_name,
            action_id=record.id,
            error=f"That action was already {record.status.replace('_', ' ')}.",
        )

    if record.is_expired:
        journal.mark_status(record.id, journal.STATUS_EXPIRED)
        return GateOutcome(
            status=FAILED,
            skill_name=record.skill_name,
            action_id=record.id,
            error="That confirmation expired. Ask me again if you still want it.",
        )

    skill = get_skill(record.skill_name)
    if skill is None:
        journal.mark_failed(record.id, "skill unavailable at confirmation time")
        return GateOutcome(
            status=FAILED,
            skill_name=record.skill_name,
            error=f"'{record.skill_name}' is no longer available.",
        )

    return _execute(skill, record, ctx)


def decline(action_id: str) -> GateOutcome:
    record = journal.get(action_id)
    if record is None:
        return GateOutcome(status=FAILED, skill_name="", error="That action no longer exists.")
    if record.status == journal.STATUS_PENDING:
        journal.mark_status(record.id, journal.STATUS_DECLINED)
    return GateOutcome(
        status=DECLINED,
        skill_name=record.skill_name,
        action_id=record.id,
        preview=record.preview,
    )


def new_batch_id() -> str:
    return str(uuid.uuid4())


# -- execution -------------------------------------------------------------


def _execute(skill: Skill, record: journal.ActionRecord, ctx: SkillContext) -> GateOutcome:
    try:
        result = skill.run(record.params, ctx)
    except SkillError as exc:
        journal.mark_failed(record.id, str(exc))
        return GateOutcome(
            status=FAILED, skill_name=skill.name, action_id=record.id, error=str(exc)
        )
    except Exception as exc:  # noqa: BLE001 — a skill bug must not kill the turn (REQ-27)
        log.exception("Skill %s raised", skill.name)
        journal.mark_failed(record.id, repr(exc))
        return GateOutcome(
            status=FAILED,
            skill_name=skill.name,
            action_id=record.id,
            error=f"{skill.name} hit an unexpected error and did not complete.",
        )

    if not result.ok:
        journal.mark_failed(record.id, result.message or "failed")
        return GateOutcome(
            status=FAILED,
            skill_name=skill.name,
            action_id=record.id,
            error=result.message or f"{skill.name} did not complete.",
            result=result,
        )

    journal.mark_executed(record.id, result=result.data, undo_payload=result.undo_payload)
    return GateOutcome(
        status=EXECUTED,
        skill_name=skill.name,
        action_id=record.id,
        preview=record.preview,
        reversible=skill.reversible and result.undo_payload is not None,
        result=result,
        batch_id=record.batch_id,
    )


def _safe_preview(skill: Skill, args: dict[str, Any]) -> str:
    """Describe the action, or let a validation failure through.

    A SkillError here means the action is already known to be invalid — a path
    outside the allowed roots, a folder that doesn't exist. That must surface as
    a refusal now, not as a confirmation prompt for something that will fail the
    moment it is approved. Only unexpected exceptions fall back to a generic
    description, and even then the action stays gated.
    """
    try:
        return skill.preview(args)
    except SkillError:
        raise
    except Exception:  # noqa: BLE001
        log.exception("preview() failed for %s", skill.name)
        return f"Run {skill.name} with {args}"

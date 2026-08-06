"""Undo — REQ-25.

A skill reverses itself from the undo_payload it recorded at execution time.
One organize run is one batch and undoes as one operation, not 47 separate
"put that file back" steps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..skills.base import SkillError
from ..skills.registry import get_skill
from . import journal

log = logging.getLogger(__name__)


@dataclass
class UndoOutcome:
    ok: bool
    message: str
    undone: list[str]
    failed: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "message": self.message,
            "undone": self.undone,
            "failed": self.failed,
        }


def undo_action(action_id: str) -> UndoOutcome:
    record = journal.get(action_id)
    if record is None:
        return UndoOutcome(False, "I have no record of that action.", [], [])
    return _undo_records([record])


def undo_batch(batch_id: str) -> UndoOutcome:
    records = [r for r in journal.batch(batch_id) if r.status == journal.STATUS_EXECUTED]
    if not records:
        return UndoOutcome(False, "Nothing in that batch is undoable.", [], [])
    # Reverse order: the last change made is the first one taken back.
    return _undo_records(list(reversed(records)))


def undo_last() -> UndoOutcome:
    record = journal.last_undoable()
    if record is None:
        return UndoOutcome(False, "There's nothing recent I can undo.", [], [])
    return undo_batch(record.batch_id)


def _undo_records(records: list[journal.ActionRecord]) -> UndoOutcome:
    undone: list[str] = []
    failed: list[str] = []

    for record in records:
        if record.status == journal.STATUS_UNDONE:
            continue
        if record.status != journal.STATUS_EXECUTED:
            failed.append(f"{record.skill_name} ({record.status.replace('_', ' ')})")
            continue
        if not record.reversible or record.undo_payload is None:
            # This was disclosed at the confirmation step, so it should not be a
            # surprise now — but say it plainly rather than silently skipping.
            failed.append(f"{record.skill_name} (not reversible)")
            continue

        skill = get_skill(record.skill_name)
        if skill is None:
            failed.append(f"{record.skill_name} (skill unavailable)")
            continue

        try:
            result = skill.undo(record.undo_payload)
        except (SkillError, NotImplementedError) as exc:
            failed.append(f"{record.skill_name} ({exc})")
            continue
        except Exception as exc:  # noqa: BLE001
            log.exception("undo failed for %s", record.skill_name)
            failed.append(f"{record.skill_name} ({exc!r})")
            continue

        if result.ok:
            journal.mark_status(record.id, journal.STATUS_UNDONE)
            undone.append(result.message or record.preview)
        else:
            failed.append(f"{record.skill_name} ({result.message})")

    if undone and not failed:
        message = "; ".join(undone)
        return UndoOutcome(True, message, undone, failed)
    if undone and failed:
        # Partial undo is reported as partial. Rounding up to "done" here would
        # leave the user believing the filesystem is in a state it is not.
        return UndoOutcome(
            False,
            f"Partly undone: {'; '.join(undone)}. Could not undo: {'; '.join(failed)}.",
            undone,
            failed,
        )
    return UndoOutcome(False, f"Could not undo: {'; '.join(failed)}.", undone, failed)

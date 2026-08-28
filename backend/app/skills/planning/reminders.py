"""Reminder skills — REQ-9.

Adding a reminder is routine, not consequential: it creates nothing destructive
and is trivially cancelled. What REQ-9 requires instead is that the *resolved*
time is stated back — "Friday 6 February at 18:00", never "ok, done" — because a
misparsed time is the failure mode that matters here, and only the user can catch it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...scheduler import store
from ..base import Skill, SkillContext, SkillError, SkillParam, SkillResult
from . import timeparse


class AddReminderSkill(Skill):
    name = "planning.add_reminder"
    description = (
        "Set a reminder, timer or alarm. Handles 'in 20 minutes', 'tomorrow at 9', "
        "'every weekday at 6pm', 'friday at 14:30'. Pass the user's own time phrase "
        "through unchanged — do not convert it to a date yourself."
    )
    parameters = (
        SkillParam("what", "string", "What to remind them about, in their words."),
        SkillParam("when", "string", "The time phrase exactly as the user said it."),
    )
    reversible = True

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        label = str(args["what"]).strip()
        phrase = str(args["when"]).strip()
        if not label:
            raise SkillError("There was nothing to be reminded about.")

        parsed = timeparse.parse(phrase)
        if parsed is None:
            raise SkillError(
                f"I couldn't work out when '{phrase}' is. Try something like "
                "'in 20 minutes', 'tomorrow at 9am', or 'every weekday at 18:00'."
            )
        if parsed.recurrence is None and parsed.when <= datetime.now().astimezone():
            raise SkillError(f"'{phrase}' resolves to a time that has already passed.")

        kind = store.KIND_TIMER if phrase.strip().lower().startswith("in ") else store.KIND_REMINDER
        item = store.add(
            kind=kind,
            label=label,
            fire_at=parsed.when,
            recurrence=parsed.recurrence,
            payload={"phrase": phrase},
        )

        return SkillResult(
            ok=True,
            message=f"Reminder set: \"{label}\" — {parsed.describe()}.",
            data=item.to_dict(),
            undo_payload={"item_id": item.id},
        )

    def undo(self, undo_payload: dict[str, Any]) -> SkillResult:
        item = store.cancel(str(undo_payload.get("item_id", "")))
        if item is None:
            return SkillResult(ok=False, message="That reminder no longer exists.")
        return SkillResult(ok=True, message=f"Cancelled the reminder \"{item.label}\".")


class ListRemindersSkill(Skill):
    name = "planning.list_reminders"
    description = "List reminders, timers and alarms that are still pending."
    parameters = ()

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        items = [i for i in store.active_items() if i.kind in store.REMINDER_KINDS]
        if not items:
            return SkillResult(ok=True, message="Nothing is scheduled.", data={"reminders": []})
        lines = [f"{index}. {item.describe()}" for index, item in enumerate(items, start=1)]
        return SkillResult(
            ok=True,
            message=f"{len(items)} pending:\n" + "\n".join(lines),
            data={"reminders": [i.to_dict() for i in items]},
        )


class CancelReminderSkill(Skill):
    name = "planning.cancel_reminder"
    description = "Cancel a pending reminder, matched by its text."
    parameters = (SkillParam("which", "string", "Words from the reminder to cancel."),)
    reversible = True

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        query = str(args["which"]).strip()
        matches = [i for i in store.find(query) if i.active]
        if not matches:
            raise SkillError(f"I don't have a pending reminder matching '{query}'.")
        if len(matches) > 1:
            listed = "; ".join(f"\"{m.label}\"" for m in matches[:5])
            raise SkillError(f"That matches {len(matches)} reminders ({listed}). Which one?")

        item = store.cancel(matches[0].id)
        assert item is not None
        return SkillResult(
            ok=True,
            message=f"Cancelled \"{item.label}\".",
            data=item.to_dict(),
            undo_payload={"item_id": item.id},
        )

    def undo(self, undo_payload: dict[str, Any]) -> SkillResult:
        item = store.get(str(undo_payload.get("item_id", "")))
        if item is None:
            return SkillResult(ok=False, message="That reminder no longer exists.")
        store.restore(item)
        return SkillResult(ok=True, message=f"Restored the reminder \"{item.label}\".")

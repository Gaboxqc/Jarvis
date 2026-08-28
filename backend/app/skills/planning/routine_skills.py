"""Routine skills — REQ-12, REQ-24, REQ-25.

Creating a routine is where "approve the consequential actions at creation time"
happens, and the Action Gate already does that job. `AddRoutineSkill` is
consequential when — and only when — the routine it is creating contains a step
that would need confirming, and its preview lists the trigger and every action
in the skills' own words.

So the requirement is satisfied by the machinery that was already there. The
user sees "Every weekday at 09:00, this will: start a focus session; send the
standup mail to team@..." and answers once. Saying yes creates the routine
already approved; the approval carries a fingerprint of those exact steps, and
editing any of them revokes it. See scheduler/routines.py.

`allow_pre_approval` is off. A standing approval for "create routines" would let
a routine containing a send be created without the preview ever being shown,
which is the one thing this arrangement exists to prevent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...scheduler import routines, store
from ..base import Severity, Skill, SkillContext, SkillError, SkillParam, SkillResult
from . import timeparse


def _steps_from(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise SkillError(
            "The actions should be a list, each with a skill name and its arguments."
        )
    return [step for step in raw if isinstance(step, dict)]


class AddRoutineSkill(Skill):
    name = "planning.add_routine"
    description = (
        "Create a routine: a trigger time and a list of things to do when it fires. "
        "For example 'every weekday at 9, start a focus session and give me the "
        "briefing'. Each action names a skill and its arguments."
    )
    parameters = (
        SkillParam("name", "string", "What to call the routine, in the user's words."),
        SkillParam("when", "string", "The trigger time phrase exactly as the user said it."),
        SkillParam(
            "actions", "array",
            "The steps, each {\"skill\": \"<skill name>\", \"args\": {...}}, in order.",
        ),
    )
    # Declared so the class *may* gate; severity() decides per call. A routine
    # that only reads is not worth interrupting anyone for.
    consequential = True
    reversible = True
    allow_pre_approval = False

    def _parsed(self, args: dict[str, Any]) -> tuple[str, timeparse.ParsedTime, list[dict[str, Any]]]:
        label = str(args.get("name", "")).strip()
        phrase = str(args.get("when", "")).strip()
        if not label:
            raise SkillError("The routine needs a name.")

        parsed = timeparse.parse(phrase)
        if parsed is None:
            raise SkillError(
                f"I couldn't work out when '{phrase}' is. Try something like "
                "'every weekday at 9am' or 'every Monday at 18:00'."
            )

        try:
            steps = routines.validate(_steps_from(args.get("actions")))
        except routines.RoutineError as exc:
            raise SkillError(str(exc)) from exc
        return label, parsed, steps

    def severity(self, args: dict[str, Any]) -> Severity:
        try:
            _label, _parsed, steps = self._parsed(args)
        except SkillError:
            # Invalid input is refused by validate() before anything is
            # journalled; treating it as routine here keeps the refusal a
            # refusal rather than a confirmation prompt for something broken.
            return "routine"
        return "consequential" if routines.consequential_steps(steps) else "routine"

    def preview(self, args: dict[str, Any]) -> str:
        label, parsed, steps = self._parsed(args)
        lines = [f'Create the routine "{label}" — {parsed.describe()} — which will:']
        lines += [f"  {i}. {line}" for i, line in enumerate(routines.describe(steps), 1)]
        gated = routines.consequential_steps(steps)
        if gated:
            # The whole reason this prompt exists. Named, not counted: "1 action
            # needs approval" is not something anyone can agree to.
            lines.append(
                "Approving this now means "
                + ", ".join(step["skill"] for step in gated)
                + " will run each time without asking again. Editing the routine "
                "cancels that."
            )
        return "\n".join(lines)

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        label, parsed, steps = self._parsed(args)
        if parsed.recurrence is None and parsed.when <= datetime.now().astimezone():
            raise SkillError(f"'{args.get('when')}' resolves to a time that has already passed.")

        # Reaching run() at all means the gate let it through: either nothing
        # here is consequential, or the user has just read the preview and
        # agreed. Both are approval of exactly these steps.
        item = routines.create(
            label=label,
            fire_at=parsed.when,
            steps=steps,
            recurrence=parsed.recurrence,
            approved=True,
            phrase=str(args.get("when", "")),
        )

        return SkillResult(
            ok=True,
            message=(
                f'Routine "{label}" created — {parsed.describe()}, '
                f"{len(steps)} step(s)."
            ),
            data=item.to_dict(),
            undo_payload={"item_id": item.id},
        )

    def undo(self, undo_payload: dict[str, Any]) -> SkillResult:
        routines.cancel(str(undo_payload["item_id"]))
        return SkillResult(ok=True, message="Routine removed.")


class ListRoutinesSkill(Skill):
    name = "planning.list_routines"
    description = "List the routines that are set up, what they do and when they run."

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        items = routines.all_routines()
        if not items:
            return SkillResult(ok=True, message="No routines are set up.", data={"routines": []})

        lines = []
        for item in items:
            steps = item.payload.get("steps") or []
            suffix = " — needs approving again since it changed" if routines.needs_approval(item) else ""
            lines.append(f"{item.describe()} · {len(steps)} step(s){suffix}")
        return SkillResult(
            ok=True,
            message="\n".join(lines),
            data={"routines": [i.to_dict() for i in items]},
        )


class CancelRoutineSkill(Skill):
    name = "planning.cancel_routine"
    description = "Delete a routine by name."
    parameters = (SkillParam("name", "string", "Which routine to remove."),)
    reversible = True

    def _match(self, name: str) -> store.ScheduledItem:
        needle = name.strip().lower()
        if not needle:
            raise SkillError("Which routine?")
        found = [i for i in routines.all_routines() if needle in i.label.lower()]
        if not found:
            raise SkillError(f"There's no routine matching '{name}'.")
        if len(found) > 1:
            names = ", ".join(f'"{i.label}"' for i in found)
            raise SkillError(f"That matches more than one routine: {names}. Which one?")
        return found[0]

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        item = self._match(str(args["name"]))
        routines.cancel(item.id)
        return SkillResult(
            ok=True,
            message=f'Routine "{item.label}" removed.',
            data={"id": item.id},
            undo_payload={"item": item.id, "label": item.label},
        )

    def undo(self, undo_payload: dict[str, Any]) -> SkillResult:
        item = store.get(str(undo_payload["item"]))
        if item is not None:
            store.restore(item)
        return SkillResult(ok=True, message=f"Routine \"{undo_payload['label']}\" is back.")

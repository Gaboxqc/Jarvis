"""Shortcut skills — REQ-22, REQ-24, REQ-25.

    THE SYSTEM SHALL let the user define named shortcuts for multi-step
    sequences ("work setup" → open these three apps).

Creating one is where its actions get approved, and the Action Gate already does
that job: `system.add_shortcut` is consequential exactly when the shortcut it
builds contains a step that would be, and its preview lists every action in the
skills' own words. So the user reads "Work setup will: open Slack; open VS Code;
start a focus session" and answers once.

Running one is deliberately *not* gated a second time. The approval was taken in
full, from a preview naming every step, and the fingerprint means an edit
revokes it. Asking again on every invocation would be asking someone to
re-approve what they are in the middle of asking for, which is the training in
approving-without-reading that REQ-24's own reasoning warns about.

`allow_pre_approval` is off on creation, for the same reason it is off for
routines: a standing approval for "make shortcuts" would let one containing a
send be created without the preview ever being shown.
"""

from __future__ import annotations

from typing import Any

from ...scheduler import sequences, shortcuts
from ..base import Severity, Skill, SkillContext, SkillError, SkillParam, SkillResult


def _steps_from(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise SkillError(
            "The actions should be a list, each with a skill name and its arguments."
        )
    return [step for step in raw if isinstance(step, dict)]


class AddShortcutSkill(Skill):
    name = "system.add_shortcut"
    description = (
        "Save a named shortcut for a sequence of actions, so it can be run later by "
        "name. For example 'make a shortcut called work setup that opens Slack, VS "
        "Code and Spotify'. Each action names a skill and its arguments."
    )
    parameters = (
        SkillParam("name", "string", "What to call the shortcut, in the user's words."),
        SkillParam(
            "actions", "array",
            "The steps, each {\"skill\": \"<skill name>\", \"args\": {...}}, in order.",
        ),
    )
    consequential = True
    reversible = True
    allow_pre_approval = False

    def _parsed(self, args: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        label = str(args.get("name", "")).strip()
        if not label:
            raise SkillError("The shortcut needs a name.")
        try:
            steps = sequences.validate(_steps_from(args.get("actions")))
        except sequences.SequenceError as exc:
            raise SkillError(str(exc)) from exc
        return label, steps

    def severity(self, args: dict[str, Any]) -> Severity:
        try:
            _label, steps = self._parsed(args)
        except SkillError:
            # Invalid input is refused before anything is journalled; treating
            # it as routine here keeps the refusal a refusal rather than a
            # confirmation prompt for something broken.
            return "routine"
        return "consequential" if sequences.consequential_steps(steps) else "routine"

    def preview(self, args: dict[str, Any]) -> str:
        label, steps = self._parsed(args)
        lines = [f'Save the shortcut "{label}", which will:']
        lines += [f"  {i}. {line}" for i, line in enumerate(sequences.describe(steps), 1)]
        gated = sequences.consequential_steps(steps)
        if gated:
            # Named, not counted: "1 action needs approval" is not something
            # anyone can agree to.
            lines.append(
                "Approving this now means "
                + ", ".join(step["skill"] for step in gated)
                + " will run whenever you use the shortcut, without asking again. "
                "Editing it cancels that."
            )
        return "\n".join(lines)

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        label, steps = self._parsed(args)
        # Reaching run() means the gate let it through: either nothing here is
        # consequential, or the user has just read the preview and agreed.
        try:
            item = shortcuts.create(label=label, steps=steps, approved=True)
        except sequences.SequenceError as exc:
            raise SkillError(str(exc)) from exc

        return SkillResult(
            ok=True,
            message=f'Shortcut "{label}" saved — {len(steps)} step(s). Say "run {label}".',
            data=item.to_dict(),
            undo_payload={"item_id": item.id},
        )

    def undo(self, undo_payload: dict[str, Any]) -> SkillResult:
        shortcuts.cancel(str(undo_payload["item_id"]))
        return SkillResult(ok=True, message="Shortcut removed.")


class RunShortcutSkill(Skill):
    name = "system.run_shortcut"
    description = (
        "Run a saved shortcut by name, e.g. 'run work setup' or 'do my wind down'."
    )
    parameters = (SkillParam("name", "string", "Which shortcut to run."),)

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        try:
            item = shortcuts.find(str(args["name"]))
        except sequences.SequenceError as exc:
            raise SkillError(str(exc)) from exc

        outcome = shortcuts.run(item)
        message = f'"{item.label}" — {sequences.summarise(outcome)}'
        if outcome["needs_approval"]:
            message += (
                " It changed since you approved it, so approve it again in Planner "
                "to let those steps run."
            )
        return SkillResult(ok=True, message=message, data=outcome)


class ListShortcutsSkill(Skill):
    name = "system.list_shortcuts"
    description = "List the saved shortcuts and what each one does."

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        items = shortcuts.all_shortcuts()
        if not items:
            return SkillResult(
                ok=True,
                message="No shortcuts saved yet.",
                data={"shortcuts": []},
            )

        lines = []
        for item in items:
            steps = item.payload.get("steps") or []
            suffix = " — needs approving again since it changed" if shortcuts.needs_approval(item) else ""
            lines.append(f"{item.label} · {len(steps)} step(s){suffix}")
        return SkillResult(
            ok=True,
            message="\n".join(lines),
            data={"shortcuts": [i.to_dict() for i in items]},
        )


class DeleteShortcutSkill(Skill):
    name = "system.delete_shortcut"
    description = "Delete a saved shortcut by name."
    parameters = (SkillParam("name", "string", "Which shortcut to remove."),)
    reversible = True

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        try:
            item = shortcuts.find(str(args["name"]))
        except sequences.SequenceError as exc:
            raise SkillError(str(exc)) from exc

        shortcuts.cancel(item.id)
        return SkillResult(
            ok=True,
            message=f'Shortcut "{item.label}" removed.',
            data={"id": item.id},
            undo_payload={"item": item.id, "label": item.label},
        )

    def undo(self, undo_payload: dict[str, Any]) -> SkillResult:
        from ...scheduler import store

        item = store.get(str(undo_payload["item"]))
        if item is not None:
            store.restore(item)
        return SkillResult(ok=True, message=f"Shortcut \"{undo_payload['label']}\" is back.")

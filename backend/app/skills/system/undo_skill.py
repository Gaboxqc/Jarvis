"""Undo as a skill — REQ-25.

The orchestrator already catches a bare "undo" before the model runs, because
that phrasing must never be at the mercy of routing. This skill covers the other
half: "put the Downloads folder back how it was", "actually revert what you just
did to my files" — phrasings a regex should not try to own.
"""

from __future__ import annotations

from typing import Any

from ..base import Skill, SkillContext, SkillParam, SkillResult


class UndoSkill(Skill):
    name = "system.undo_last"
    description = (
        "Reverse the most recent reversible thing Kai did — a folder organize, a "
        "remembered fact, a task change. Use when the user asks to undo, revert, or "
        "put something back."
    )
    parameters = ()

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        # Imported here: actions.undo depends on the skill registry, and the
        # registry imports this module. A module-level import would cycle.
        from ...actions import undo

        outcome = undo.undo_last()
        return SkillResult(ok=outcome.ok, message=outcome.message, data=outcome.to_dict())


class ActionHistorySkill(Skill):
    name = "system.action_history"
    description = (
        "Show what Kai has actually done recently, and whether each thing can still "
        "be undone. Use when the user asks what you did or what changed."
    )
    parameters = (
        SkillParam("limit", "integer", "How many entries (default 10).",
                   required=False, default=10),
    )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        from ...actions import journal

        limit = max(1, min(int(args.get("limit", 10) or 10), 50))
        records = journal.history(limit=limit)
        if not records:
            return SkillResult(ok=True, message="I haven't done anything yet.", data={"history": []})

        lines = []
        for record in records:
            when = record.executed_at.astimezone().strftime("%d %b %H:%M") if record.executed_at else "—"
            state = record.status.replace("_", " ")
            undoable = " (undoable)" if record.can_undo else ""
            lines.append(f"{when} — {record.preview} [{state}]{undoable}")

        return SkillResult(
            ok=True,
            message="\n".join(lines),
            data={"history": [{"skill": r.skill_name, "status": r.status,
                               "preview": r.preview, "can_undo": r.can_undo} for r in records]},
        )

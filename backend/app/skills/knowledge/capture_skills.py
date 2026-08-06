"""Meeting capture skills — REQ-19, REQ-10.

Starting a recording is routine, not gated: the user asking for it *is* the
explicit start REQ-19 requires, and making them confirm a thing they just asked
for is the prompt fatigue that devalues real confirmations.

What REQ-19 actually requires instead is that recording is never ambient and
never invisible. So the reply states plainly that recording has begun, names
which sources are captured, and `capture.status` reports it for as long as it
runs. Deleting a transcript is gated, because that is the irreversible one.
"""

from __future__ import annotations

from typing import Any

from ...capture import session as capture
from ...capture import store, summarize
from ..base import Skill, SkillContext, SkillError, SkillParam, SkillResult


class StartCaptureSkill(Skill):
    name = "capture.start"
    description = (
        "Start recording and transcribing a meeting or call. Captures this "
        "microphone and the audio coming out of the speakers, so both sides of a "
        "video call are included. Use for 'record this meeting', 'take notes on this call'."
    )
    parameters = (
        SkillParam("label", "string", "What to call it, e.g. 'standup' or 'client call'.",
                   required=False, default="Meeting"),
    )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        label = str(args.get("label", "Meeting") or "Meeting").strip()
        try:
            status = capture.start(label)
        except capture.CaptureError as exc:
            raise SkillError(str(exc)) from exc

        lines = [f"Recording \"{label}\" now, capturing {' and '.join(status.sources or [])}."]
        if status.note:
            # An incomplete recording says so at the start, not at the end.
            lines.append(f"Note: {status.note}.")
        lines.append(
            "Recording others may need their agreement - that part is your call. "
            "Say stop recording when you're done."
        )

        return SkillResult(ok=True, message=" ".join(lines), data=status.to_dict())


class StopCaptureSkill(Skill):
    name = "capture.stop"
    description = (
        "Stop the current recording and summarise it into what was discussed, what "
        "was decided, and who agreed to do what."
    )
    parameters = ()

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        try:
            transcript = capture.stop()
        except capture.CaptureError as exc:
            raise SkillError(str(exc)) from exc

        if transcript is None:
            raise SkillError("The recording ended but nothing was saved.")

        if not transcript.text.strip():
            return SkillResult(
                ok=True,
                message=(
                    f"Stopped recording \"{transcript.label}\", but nothing was "
                    "transcribed - no speech was picked up."
                ),
                data=transcript.to_dict(),
            )

        result = summarize.summarise(transcript.text)
        store.set_summary(transcript.id, result.to_dict())

        minutes = int(transcript.duration_seconds // 60)
        header = (
            f"\"{transcript.label}\" - {minutes} min, {transcript.word_count} words "
            f"transcribed.\n\n"
        )
        tail = ""
        if result.actions:
            tail = (
                f"\n\nSay save the action items and I'll add those "
                f"{len(result.actions)} to your task list."
            )

        return SkillResult(
            ok=True,
            message=header + result.render() + tail,
            data={**transcript.to_dict(), "summary": result.to_dict()},
        )


class CaptureStatusSkill(Skill):
    name = "capture.status"
    description = "Report whether a recording is running, and for how long."
    parameters = ()

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        status = capture.status()
        return SkillResult(ok=True, message=status.describe(), data=status.to_dict())


class ListTranscriptsSkill(Skill):
    name = "capture.list"
    description = "List recorded meetings, or search them by name or content."
    parameters = (
        SkillParam("query", "string", "Optional words to search for.", required=False),
    )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        query = str(args.get("query", "") or "").strip()
        found = store.find(query) if query else store.recent()
        if not found:
            return SkillResult(
                ok=True,
                message="No recordings match that." if query else "No meetings recorded yet.",
                data={"transcripts": []},
            )
        lines = [f"  {t.describe()}" for t in found]
        return SkillResult(
            ok=True,
            message=f"{len(found)} recording(s):\n" + "\n".join(lines),
            data={"transcripts": [t.to_dict() for t in found]},
        )


class RecallMeetingSkill(Skill):
    name = "capture.recall"
    description = (
        "Answer a question about a recorded meeting, or re-read its summary. Use "
        "for 'what did we decide in the standup', 'what were my action items'."
    )
    parameters = (
        SkillParam("query", "string", "Which meeting, or what to look for.", required=False),
    )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        query = str(args.get("query", "") or "").strip()
        transcript = store.find(query)[0] if query and store.find(query) else store.latest()
        if transcript is None:
            raise SkillError("There are no recorded meetings yet.")

        parts = [f"\"{transcript.label}\" - {transcript.describe()}"]
        if transcript.summary:
            summary = summarize.Summary(**{
                k: v for k, v in transcript.summary.items()
                if k in {"summary", "decisions", "actions", "truncated", "error"}
            })
            parts.append(summary.render())
        else:
            parts.append(transcript.text[:2000])

        return SkillResult(ok=True, message="\n\n".join(parts), data=transcript.to_dict())


class SaveActionItemsSkill(Skill):
    name = "capture.save_actions"
    description = (
        "Save the action items from a recorded meeting onto the task list. Use after "
        "a meeting summary when the user says to save or keep the action items."
    )
    parameters = (
        SkillParam("query", "string", "Which meeting; defaults to the most recent.",
                   required=False),
    )
    reversible = True

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        query = str(args.get("query", "") or "").strip()
        transcript = store.find(query)[0] if query and store.find(query) else store.latest()
        if transcript is None:
            raise SkillError("There are no recorded meetings yet.")

        actions = (transcript.summary or {}).get("actions") or []
        if not actions:
            raise SkillError(f"\"{transcript.label}\" has no action items recorded.")

        from ...skills.planning.tasks import AddTaskSkill

        adder = AddTaskSkill()
        created: list[str] = []
        for action in actions:
            result = adder.run({"text": str(action), "kind": "task"}, ctx)
            if result.undo_payload:
                created.append(str(result.undo_payload.get("task_id", "")))

        return SkillResult(
            ok=True,
            message=f"Added {len(created)} action item(s) from \"{transcript.label}\" "
                    "to your task list.",
            data={"added": len(created)},
            undo_payload={"task_ids": created},
        )

    def undo(self, undo_payload: dict[str, Any]) -> SkillResult:
        from ... import db

        removed = 0
        for task_id in undo_payload.get("task_ids", []):
            cursor = db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            removed += cursor.rowcount or 0
        return SkillResult(ok=True, message=f"Removed {removed} saved action item(s).")


class DeleteTranscriptSkill(Skill):
    name = "capture.delete"
    description = "Delete a recorded meeting and its transcript permanently."
    parameters = (
        SkillParam("query", "string", "Which meeting to delete."),
    )
    consequential = True
    reversible = False

    def preview(self, args: dict[str, Any]) -> str:
        found = store.find(str(args.get("query", "")))
        if not found:
            return f"Delete a recording matching '{args.get('query')}' - nothing matches."
        listed = "; ".join(f"\"{t.label}\" ({t.word_count} words)" for t in found[:5])
        return (
            f"Permanently delete {len(found)} recording(s): {listed}. "
            "The transcript and its summary go with it, and this cannot be undone."
        )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        found = store.find(str(args["query"]))
        if not found:
            raise SkillError(f"No recording matches '{args.get('query')}'.")
        if len(found) > 1:
            listed = "; ".join(f"\"{t.label}\"" for t in found[:5])
            raise SkillError(f"That matches {len(found)} recordings ({listed}). Which one?")

        deleted = store.delete(found[0].id)
        assert deleted is not None
        return SkillResult(ok=True, message=f"Deleted \"{deleted.label}\".",
                           data={"id": deleted.id})

"""Task and note skills — REQ-10.

Stored in SQLite for querying, and mirrored to a plain Markdown file after every
change. The mirror is the requirement that the user's data is "not trapped in the
app": if Kai is uninstalled tomorrow, the list is still readable in any editor.
The mirror is one-way — SQLite stays the source of truth.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from ... import db
from ...settings import data_dir
from ..base import Skill, SkillContext, SkillError, SkillParam, SkillResult

MIRROR_NAME = "tasks.md"


def _mirror() -> None:
    rows = db.query("SELECT * FROM tasks ORDER BY done ASC, created_at DESC")
    open_lines, done_lines = [], []
    for row in rows:
        tags = db.loads(row["tags"], []) or []
        suffix = ("  " + " ".join(f"#{t}" for t in tags)) if tags else ""
        due = f"  (due {row['due']})" if row["due"] else ""
        line = f"- [{'x' if row['done'] else ' '}] {row['text']}{due}{suffix}"
        (done_lines if row["done"] else open_lines).append(line)

    content = ["# Tasks", "", f"_Mirrored from Kai on {datetime.now().strftime('%Y-%m-%d %H:%M')}._", ""]
    content += ["## Open", ""] + (open_lines or ["_nothing open_"]) + [""]
    content += ["## Done", ""] + (done_lines or ["_nothing done yet_"]) + [""]

    try:
        (data_dir() / MIRROR_NAME).write_text("\n".join(content), encoding="utf-8")
    except OSError:
        # The mirror is a convenience. Failing to write it must not fail the task.
        pass


# -- direct access, for the API ------------------------------------------
#
# The skills below are how the *model* touches the task list, and they go
# through the Action Gate. These are how the *user* touches it, by clicking
# something they are looking at.
#
# Not putting the UI through the gate is deliberate. The gate exists so the
# assistant cannot act without the user knowing; a person pressing "Done" on a
# task already knows. Routing that through a confirmation would ask them to
# approve the click they just made, which is the prompt-fatigue REQ-24 warns
# about. Every one of these is a click on a visible row, and the destructive one
# still asks.
#
# They live here rather than in a store module so there is one copy of the SQL
# and the Markdown mirror cannot be forgotten by a second writer.


def list_tasks(include_done: bool = True) -> list[dict[str, Any]]:
    sql = "SELECT * FROM tasks"
    if not include_done:
        sql += " WHERE done = 0"
    sql += " ORDER BY done ASC, created_at DESC"
    return [
        {
            "id": row["id"],
            "text": row["text"],
            "kind": row["kind"],
            "tags": db.loads(row["tags"], []) or [],
            "due": row["due"],
            "done": bool(row["done"]),
            "created_at": row["created_at"],
        }
        for row in db.query(sql)
    ]


def create_task(text: str, kind: str = "task", due: str | None = None) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise SkillError("A task needs some text.")
    cleaned, tags = _extract_tags(text)
    task_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO tasks(id, text, kind, tags, due, done, created_at) "
        "VALUES(?, ?, ?, ?, ?, 0, ?)",
        (task_id, cleaned, kind if kind in {"task", "note"} else "task",
         db.dumps(tags), due, db.now()),
    )
    _mirror()
    return {"id": task_id, "text": cleaned, "tags": tags, "due": due, "done": False}


def set_done(task_id: str, done: bool) -> dict[str, Any]:
    row = db.query_one("SELECT id, text FROM tasks WHERE id = ?", (task_id,))
    if row is None:
        raise SkillError("That task isn't on the list any more.")
    if done:
        db.execute("UPDATE tasks SET done = 1, completed_at = ? WHERE id = ?", (db.now(), task_id))
    else:
        db.execute("UPDATE tasks SET done = 0, completed_at = NULL WHERE id = ?", (task_id,))
    _mirror()
    return {"id": task_id, "text": row["text"], "done": done}


def delete_task(task_id: str) -> dict[str, Any]:
    row = db.query_one("SELECT id, text FROM tasks WHERE id = ?", (task_id,))
    if row is None:
        raise SkillError("That task isn't on the list any more.")
    db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    _mirror()
    return {"id": task_id, "text": row["text"]}


def _extract_tags(text: str) -> tuple[str, list[str]]:
    tags = re.findall(r"#(\w+)", text)
    cleaned = re.sub(r"\s*#\w+", "", text).strip()
    return cleaned or text, tags


class AddTaskSkill(Skill):
    name = "planning.add_task"
    description = (
        "Capture a task or a note. Use for anything the user wants written down but "
        "not alarmed about at a specific time — if they want to be interrupted at a "
        "time, use planning.add_reminder instead."
    )
    parameters = (
        SkillParam("text", "string", "The task or note, in the user's words."),
        SkillParam("kind", "string", "'task' or 'note'.", required=False, default="task",
                   enum=("task", "note")),
        SkillParam("due", "string", "Optional due date, YYYY-MM-DD.", required=False),
    )
    reversible = True

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        raw = str(args["text"]).strip()
        if not raw:
            raise SkillError("There was nothing to write down.")
        text, tags = _extract_tags(raw)
        kind = str(args.get("kind", "task"))
        kind = kind if kind in {"task", "note"} else "task"

        due = str(args.get("due", "") or "").strip() or None
        if due and not re.match(r"^\d{4}-\d{2}-\d{2}$", due):
            raise SkillError(f"'{due}' isn't a date I can store (use YYYY-MM-DD).")

        task_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO tasks(id, text, kind, tags, due, done, created_at) "
            "VALUES(?, ?, ?, ?, ?, 0, ?)",
            (task_id, text, kind, db.dumps(tags), due, db.now()),
        )
        _mirror()

        detail = f" (due {due})" if due else ""
        tag_note = f" tagged {', '.join(tags)}" if tags else ""
        return SkillResult(
            ok=True,
            message=f"Added {kind}: \"{text}\"{detail}{tag_note}.",
            data={"id": task_id, "text": text, "tags": tags, "due": due},
            undo_payload={"task_id": task_id},
        )

    def undo(self, undo_payload: dict[str, Any]) -> SkillResult:
        task_id = str(undo_payload.get("task_id", ""))
        row = db.query_one("SELECT text FROM tasks WHERE id = ?", (task_id,))
        if row is None:
            return SkillResult(ok=False, message="That entry is already gone.")
        db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        _mirror()
        return SkillResult(ok=True, message=f"Removed \"{row['text']}\".")


class ListTasksSkill(Skill):
    name = "planning.list_tasks"
    description = "List tasks and notes. Can filter by text, tag, or completion state."
    parameters = (
        SkillParam("filter", "string", "Optional words or #tag to filter by.", required=False),
        SkillParam("include_done", "boolean", "Include completed items.",
                   required=False, default=False),
    )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        query = str(args.get("filter", "") or "").strip().lstrip("#")
        include_done = bool(args.get("include_done", False))

        sql = "SELECT * FROM tasks WHERE 1=1"
        params: list[Any] = []
        if not include_done:
            sql += " AND done = 0"
        if query:
            sql += " AND (text LIKE ? OR tags LIKE ?)"
            params += [f"%{query}%", f"%{query}%"]
        sql += " ORDER BY done ASC, COALESCE(due, '9999') ASC, created_at DESC"

        rows = db.query(sql, params)
        if not rows:
            return SkillResult(ok=True, message="Nothing on the list.", data={"tasks": []})

        lines, payload = [], []
        for index, row in enumerate(rows, start=1):
            mark = "x" if row["done"] else " "
            due = f" (due {row['due']})" if row["due"] else ""
            lines.append(f"{index}. [{mark}] {row['text']}{due}")
            payload.append({"id": row["id"], "text": row["text"], "done": bool(row["done"])})

        return SkillResult(
            ok=True,
            message=f"{len(rows)} item(s):\n" + "\n".join(lines),
            data={"tasks": payload},
        )


class CompleteTaskSkill(Skill):
    name = "planning.complete_task"
    description = "Mark a task as done, matched by its text."
    parameters = (SkillParam("which", "string", "Words from the task."),)
    reversible = True

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        query = str(args["which"]).strip()
        rows = db.query(
            "SELECT * FROM tasks WHERE done = 0 AND text LIKE ? ORDER BY created_at DESC",
            (f"%{query}%",),
        )
        if not rows:
            raise SkillError(f"I don't have an open task matching '{query}'.")
        if len(rows) > 1:
            listed = "; ".join(f"\"{r['text']}\"" for r in rows[:5])
            raise SkillError(f"That matches {len(rows)} tasks ({listed}). Which one?")

        row = rows[0]
        db.execute("UPDATE tasks SET done = 1, completed_at = ? WHERE id = ?", (db.now(), row["id"]))
        _mirror()
        return SkillResult(
            ok=True,
            message=f"Done: \"{row['text']}\".",
            data={"id": row["id"]},
            undo_payload={"task_id": row["id"]},
        )

    def undo(self, undo_payload: dict[str, Any]) -> SkillResult:
        task_id = str(undo_payload.get("task_id", ""))
        row = db.query_one("SELECT text FROM tasks WHERE id = ?", (task_id,))
        if row is None:
            return SkillResult(ok=False, message="That task no longer exists.")
        db.execute("UPDATE tasks SET done = 0, completed_at = NULL WHERE id = ?", (task_id,))
        _mirror()
        return SkillResult(ok=True, message=f"Reopened \"{row['text']}\".")


class DeleteTaskSkill(Skill):
    name = "planning.delete_task"
    description = "Delete a task or note entirely, matched by its text."
    parameters = (SkillParam("which", "string", "Words from the task to delete."),)
    reversible = True

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        query = str(args["which"]).strip()
        rows = db.query("SELECT * FROM tasks WHERE text LIKE ?", (f"%{query}%",))
        if not rows:
            raise SkillError(f"I don't have anything matching '{query}'.")
        if len(rows) > 1:
            listed = "; ".join(f"\"{r['text']}\"" for r in rows[:5])
            raise SkillError(f"That matches {len(rows)} entries ({listed}). Which one?")

        row = rows[0]
        snapshot = {
            "id": row["id"], "text": row["text"], "kind": row["kind"], "tags": row["tags"],
            "due": row["due"], "done": row["done"], "created_at": row["created_at"],
            "completed_at": row["completed_at"],
        }
        db.execute("DELETE FROM tasks WHERE id = ?", (row["id"],))
        _mirror()
        return SkillResult(
            ok=True,
            message=f"Deleted \"{row['text']}\".",
            data={"id": row["id"]},
            undo_payload={"snapshot": snapshot},
        )

    def undo(self, undo_payload: dict[str, Any]) -> SkillResult:
        snapshot = undo_payload.get("snapshot") or {}
        if not snapshot:
            return SkillResult(ok=False, message="I don't have enough to restore that.")
        db.execute(
            "INSERT OR REPLACE INTO tasks(id, text, kind, tags, due, done, created_at, completed_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snapshot["id"], snapshot["text"], snapshot.get("kind", "task"),
                snapshot.get("tags", "[]"), snapshot.get("due"), snapshot.get("done", 0),
                snapshot.get("created_at", db.now()), snapshot.get("completed_at"),
            ),
        )
        _mirror()
        return SkillResult(ok=True, message=f"Restored \"{snapshot['text']}\".")

"""Reminders, routines, shortcuts and tasks — REQ-9, REQ-10, REQ-12, REQ-22.

One module because they are one table. A routine is a scheduled item with
steps, a shortcut is a routine with a name where its trigger time would be,
and `due_items()` refuses rows with no time -- which is what keeps a shortcut
from ever firing on its own.
"""

from __future__ import annotations

from typing import Any

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..scheduler import service as scheduler
from ..scheduler import store as sched_store
from . import schemas

log = logging.getLogger("kai")

router = APIRouter()


class TaskRequest(BaseModel):
    text: str
    kind: str = "task"
    due: str | None = None


@router.get("/reminders", response_model=schemas.Reminders)
def list_reminders() -> dict[str, Any]:
    return {"reminders": [i.to_dict() for i in sched_store.active_items()]}


@router.get("/routines", response_model=schemas.Routines)
def list_routines() -> dict[str, Any]:
    from ..scheduler import routines

    return {
        "routines": [
            {
                **item.to_dict(),
                "steps": routines.describe(item.payload.get("steps") or []),
                "needs_approval": routines.needs_approval(item),
            }
            for item in routines.all_routines()
        ]
    }


@router.post("/routines/{routine_id}/approve", response_model=schemas.RoutineApproved)
def approve_routine(routine_id: str) -> dict[str, Any]:
    """Re-approve a routine whose steps changed — REQ-12, REQ-24.

    The other half of "re-prompt if the routine is later edited". Editing
    revokes the approval; this is where the user gives it again, having seen the
    steps listed on the screen that offers this button.
    """
    from ..scheduler import routines

    item = routines.approve(routine_id)
    if item is None:
        raise HTTPException(status_code=404, detail="No such routine")
    return {"approved": True, "routine": item.to_dict()}


@router.post("/routines/{routine_id}/run", response_model=schemas.SequenceRun)
def run_routine(routine_id: str) -> dict[str, Any]:
    """Run a routine now. Useful for checking one does what you meant."""
    from ..scheduler import routines

    item = routines.get(routine_id)
    if item is None:
        raise HTTPException(status_code=404, detail="No such routine")
    return routines.run(item)


@router.delete("/routines/{routine_id}", response_model=schemas.Deleted)
def delete_routine(routine_id: str) -> dict[str, Any]:
    from ..scheduler import routines

    if not routines.cancel(routine_id):
        raise HTTPException(status_code=404, detail="No such routine")
    return {"deleted": routine_id}


@router.get("/shortcuts", response_model=schemas.Shortcuts)
def list_shortcuts() -> dict[str, Any]:
    from ..scheduler import sequences, shortcuts

    return {
        "shortcuts": [
            {
                "id": item.id,
                "label": item.label,
                "steps": sequences.describe(item.payload.get("steps") or []),
                "needs_approval": shortcuts.needs_approval(item),
            }
            for item in shortcuts.all_shortcuts()
        ]
    }


@router.post("/shortcuts/{shortcut_id}/run", response_model=schemas.SequenceRun)
def run_shortcut(shortcut_id: str) -> dict[str, Any]:
    from ..scheduler import shortcuts

    item = shortcuts.get(shortcut_id)
    if item is None:
        raise HTTPException(status_code=404, detail="No such shortcut")
    return shortcuts.run(item)


@router.post("/shortcuts/{shortcut_id}/approve", response_model=schemas.ShortcutApproved)
def approve_shortcut(shortcut_id: str) -> dict[str, Any]:
    """Re-approve a shortcut whose steps changed — REQ-22, REQ-24."""
    from ..scheduler import shortcuts

    item = shortcuts.approve(shortcut_id)
    if item is None:
        raise HTTPException(status_code=404, detail="No such shortcut")
    return {"approved": True, "shortcut": item.to_dict()}


@router.delete("/shortcuts/{shortcut_id}", response_model=schemas.Deleted)
def delete_shortcut(shortcut_id: str) -> dict[str, Any]:
    from ..scheduler import shortcuts

    if not shortcuts.cancel(shortcut_id):
        raise HTTPException(status_code=404, detail="No such shortcut")
    return {"deleted": shortcut_id}


@router.get("/tasks", response_model=schemas.Tasks)
def list_tasks(include_done: bool = True) -> dict[str, Any]:
    from ..skills.planning import tasks as task_store

    return {"tasks": task_store.list_tasks(include_done)}


@router.post("/tasks", response_model=schemas.CreatedTask)
def add_task(request: TaskRequest) -> dict[str, Any]:
    from ..skills.base import SkillError
    from ..skills.planning import tasks as task_store

    try:
        return task_store.create_task(request.text, request.kind, request.due)
    except SkillError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/tasks/{task_id}", response_model=schemas.TaskDone)
def complete_task(task_id: str, done: bool = True) -> dict[str, Any]:
    from ..skills.base import SkillError
    from ..skills.planning import tasks as task_store

    try:
        return task_store.set_done(task_id, done)
    except SkillError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/tasks/{task_id}", response_model=schemas.TaskRemoved)
def remove_task(task_id: str) -> dict[str, Any]:
    from ..skills.base import SkillError
    from ..skills.planning import tasks as task_store

    try:
        return task_store.delete_task(task_id)
    except SkillError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/reminders/{reminder_id}", response_model=schemas.ReminderCancelled)
def cancel_reminder(reminder_id: str) -> dict[str, Any]:
    item = sched_store.cancel(reminder_id)
    if item is None:
        raise HTTPException(status_code=404, detail="That reminder isn't set any more.")
    return {"cancelled": item.id, "label": item.label}


@router.post("/reminders/tick", response_model=schemas.Delivered)
def force_tick() -> dict[str, Any]:
    """Deliver anything due right now. Used by tests and by the UI on wake.

    Waits for any routine the tick queued. Routines run on their own worker so
    a slow one cannot hold up reminder delivery or the indexer -- but this
    endpoint exists to make the due thing happen while somebody is looking at
    it, so returning before the routine has run would report a tick that had
    only been scheduled.
    """
    deliveries = scheduler.tick()
    if not scheduler.wait_for_routines():
        log.warning("a routine was still running when /reminders/tick returned")
    return {"delivered": [d.message() for d in deliveries]}

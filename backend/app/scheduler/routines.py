"""Routines — REQ-12, REQ-24, REQ-25.

A trigger and a list of actions. "Every weekday at 9, start a focus session and
read me the briefing."

`KIND_ROUTINE` has been reserved in store.py since the scheduler was written,
filtered out of reminder listings, and created by nothing. This is the part that
was missing, and the whole of the difficulty is in one requirement:

    WHERE a routine includes an action classed as consequential, THE SYSTEM
    SHALL require the user to approve that action at routine-creation time, and
    SHALL re-prompt if the routine is later edited.

Approval that outlives the moment
---------------------------------

The Action Gate's central rule is that approval binds to one ActionRecord id and
is consumed. A routine needs something the gate deliberately does not offer: a
yes that applies later, more than once. Getting that wrong turns a scheduler
into a way around the gate.

Three things keep it honest.

**It is narrower than a pre-approval.** A pre-approval says "this skill, any
arguments, forever". A routine's approval says "these arguments, in this
routine". Sending mail to one address every Monday does not become permission to
send mail anywhere.

**It runs through the gate, not around it.** Every step is submitted normally.
A consequential step comes back NEEDS_CONFIRMATION with an action id, and the
runner answers it with `gate.confirm(id)` — the same call the UI makes, on the
same journalled record, producing the same history and the same undo. There is
no second execution path, so there is nothing for the gate's rules to be missing
from.

**Editing revokes it.** The approval is stamped with a fingerprint of the whole
step list. Change any argument and the fingerprint no longer matches, the
consequential steps are skipped, and the user is told the routine needs
approving again. Whole-list rather than per-step on purpose: "re-prompt if the
routine is later edited" is a statement about the routine, and a step whose own
arguments did not change can still mean something different in new company.

A routine that has never been approved runs its routine steps and skips its
consequential ones. It does not fail, and it does not ask at 9am on a Tuesday
when nobody is looking at the screen — the record says what was skipped and why.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any

from .. import db
from . import store

log = logging.getLogger(__name__)

MAX_STEPS = 8


class RoutineError(Exception):
    """A routine could not be created or changed."""


def fingerprint(steps: list[dict[str, Any]]) -> str:
    """A stable digest of what the routine will do.

    Over the canonical JSON of the whole list, so key order and whitespace
    cannot make an unchanged routine look edited, and an edited one cannot look
    unchanged.
    """
    # json.dumps with sort_keys, not db.dumps, which does not sort. Without it
    # the digest depends on dictionary ordering -- so a routine reloaded from
    # SQLite could hash differently from the one that was approved, and read as
    # edited when nothing had changed.
    canonical = json.dumps(
        [
            {"skill": str(step.get("skill", "")), "args": step.get("args") or {}}
            for step in steps
        ],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _payload(steps: list[dict[str, Any]], *, approved: bool, phrase: str = "") -> dict[str, Any]:
    return {
        "steps": steps,
        "phrase": phrase,
        # The fingerprint of what was approved, or empty for a routine whose
        # consequential steps nobody has agreed to yet.
        "approved_fingerprint": fingerprint(steps) if approved else "",
        "approved_at": db.now() if approved else "",
    }


def validate(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check the steps name real skills, and normalise them."""
    from ..skills.registry import get_skill

    if not steps:
        raise RoutineError("A routine needs at least one thing to do.")
    if len(steps) > MAX_STEPS:
        raise RoutineError(f"A routine can have at most {MAX_STEPS} steps.")

    cleaned: list[dict[str, Any]] = []
    for step in steps:
        name = str(step.get("skill", "")).strip()
        skill = get_skill(name)
        if skill is None:
            raise RoutineError(f"'{name}' is not an available skill.")
        args = step.get("args") or {}
        if not isinstance(args, dict):
            raise RoutineError(f"The arguments for '{name}' should be a set of named values.")
        # Validated now rather than at 9am on a Tuesday: a routine that cannot
        # possibly run should fail while the user is still looking at it.
        try:
            args = skill.validate(args)
        except Exception as exc:  # noqa: BLE001 — SkillError and anything it wraps
            raise RoutineError(f"{name}: {exc}") from exc
        cleaned.append({"skill": name, "args": args})
    return cleaned


def consequential_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The steps that would need a confirmation if they were asked for now."""
    from ..skills.registry import get_skill

    found = []
    for step in steps:
        skill = get_skill(step["skill"])
        if skill is not None and skill.severity(step["args"]) == "consequential":
            found.append(step)
    return found


def describe(steps: list[dict[str, Any]]) -> list[str]:
    """One line per step, in the skill's own words where it has any."""
    from ..skills.registry import get_skill

    lines = []
    for step in steps:
        skill = get_skill(step["skill"])
        if skill is None:
            lines.append(f"{step['skill']} (no longer available)")
            continue
        try:
            lines.append(skill.preview(step["args"]))
        except Exception:  # noqa: BLE001 — a skill without a preview still gets a line
            lines.append(f"{skill.name} with {step['args']}")
    return lines


# -- lifecycle -------------------------------------------------------------


def create(
    *,
    label: str,
    fire_at: datetime,
    steps: list[dict[str, Any]],
    recurrence: dict[str, Any] | None = None,
    approved: bool = False,
    phrase: str = "",
) -> store.ScheduledItem:
    cleaned = validate(steps)
    return store.add(
        kind=store.KIND_ROUTINE,
        label=label,
        fire_at=fire_at,
        recurrence=recurrence,
        payload=_payload(cleaned, approved=approved, phrase=phrase),
    )


def all_routines() -> list[store.ScheduledItem]:
    return [item for item in store.active_items() if item.kind == store.KIND_ROUTINE]


def get(routine_id: str) -> store.ScheduledItem | None:
    item = store.get(routine_id)
    if item is None or item.kind != store.KIND_ROUTINE:
        return None
    return item


def needs_approval(item: store.ScheduledItem) -> bool:
    """Whether consequential steps would be skipped as things stand."""
    steps = item.payload.get("steps") or []
    if not consequential_steps(steps):
        return False
    return item.payload.get("approved_fingerprint") != fingerprint(steps)


def approve(routine_id: str) -> store.ScheduledItem | None:
    """Record that the user has agreed to this routine's steps, as they are now."""
    item = get(routine_id)
    if item is None:
        return None
    steps = item.payload.get("steps") or []
    payload = dict(item.payload)
    payload["approved_fingerprint"] = fingerprint(steps)
    payload["approved_at"] = db.now()
    store.set_payload(routine_id, payload)
    return get(routine_id)


def set_steps(routine_id: str, steps: list[dict[str, Any]]) -> store.ScheduledItem | None:
    """Change what a routine does, which revokes its approval.

    Not a separate "revoke" call that an editor could forget: rewriting the
    steps and clearing the approval are one operation, so there is no ordering
    in which a routine ends up approved for something the user never saw.
    """
    item = get(routine_id)
    if item is None:
        return None
    cleaned = validate(steps)
    payload = dict(item.payload)
    payload["steps"] = cleaned
    payload["approved_fingerprint"] = ""
    payload["approved_at"] = ""
    store.set_payload(routine_id, payload)
    return get(routine_id)


def cancel(routine_id: str) -> bool:
    item = get(routine_id)
    if item is None:
        return False
    store.cancel(routine_id)
    return True


# -- running ---------------------------------------------------------------


def run(item: store.ScheduledItem) -> dict[str, Any]:
    """Execute a routine's steps. Returns what happened, per step.

    Every step goes through `gate.submit`, so each one is journalled, previewed
    and undoable exactly as if the user had asked for it in conversation. A
    consequential step comes back needing confirmation, and the only thing this
    does that a chat turn would not is answer that confirmation on the strength
    of an approval the user gave when the routine was created -- and only when
    the fingerprint still matches what they approved.

    One batch id for the whole run, so "undo" takes the routine back rather than
    its last step.
    """
    from ..actions import gate
    from ..skills.base import SkillContext

    steps = item.payload.get("steps") or []
    approved = item.payload.get("approved_fingerprint") == fingerprint(steps)
    batch_id = gate.new_batch_id()
    context = SkillContext(session_id=f"routine:{item.id}")

    results: list[dict[str, Any]] = []
    for step in steps:
        outcome = gate.submit(step["skill"], step["args"], context, batch_id=batch_id)

        action_id = outcome.action_id
        if outcome.status == gate.NEEDS_CONFIRMATION and action_id:
            if approved:
                outcome = gate.confirm(action_id, context)
            else:
                # Declined rather than left pending. A confirmation nobody will
                # ever see is a prompt that expires quietly and an action that
                # looks like it might still happen; saying no now is honest, and
                # the record says why.
                gate.decline(action_id)
                results.append({
                    "skill": step["skill"],
                    "status": "skipped",
                    "message": "Needs approving before this routine can do it.",
                })
                continue

        results.append({
            "skill": step["skill"],
            "status": outcome.status,
            "message": outcome.message,
        })

    ran = sum(1 for r in results if r["status"] == gate.EXECUTED)
    skipped = sum(1 for r in results if r["status"] == "skipped")
    log.info("routine %s: %d of %d steps ran, %d skipped", item.label, ran, len(steps), skipped)
    return {
        "routine_id": item.id,
        "label": item.label,
        "batch_id": batch_id,
        "steps": results,
        "ran": ran,
        "skipped": skipped,
        "needs_approval": bool(skipped),
    }


def summarise(outcome: dict[str, Any]) -> str:
    """One line for the notification the user actually sees."""
    ran, skipped = outcome["ran"], outcome["skipped"]
    parts = [f"{ran} step(s) ran"]
    if skipped:
        parts.append(
            f"{skipped} skipped because the routine has not been approved since it changed"
        )
    failed = [r for r in outcome["steps"] if r["status"] == "failed"]
    if failed:
        parts.append(f"{len(failed)} failed: {failed[0]['message']}")
    return "; ".join(parts) + "."

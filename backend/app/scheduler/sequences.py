"""A named list of actions, and the approval that lets it run — REQ-12, REQ-22,
REQ-24, REQ-25.

Two things in this app are a stored list of skill calls the user has agreed to:

    a routine    runs when its trigger time comes round      (REQ-12)
    a shortcut   runs when the user asks for it by name      (REQ-22)

They differ in exactly one respect, which is what starts them. Everything that
makes either of them safe -- validating the steps, deciding which of them the
gate would stop, taking the approval, revoking it when the list changes, and
running the thing through the gate rather than around it -- is identical, and
lives here so it cannot come to differ.

That is not tidiness. The approval logic is the security-critical part: it is
the one place in the app where a yes outlives the moment it was given. Two
copies of it would be two things to keep right, and the second copy is always
the one that gets the fix late.

Approval that outlives the moment
---------------------------------

The Action Gate's central rule is that approval binds to one ActionRecord id and
is consumed. A stored sequence needs something the gate deliberately does not
offer: a yes that applies later, possibly more than once. Getting that wrong
turns a scheduler into a way around the gate.

Three things keep it honest.

**It is narrower than a pre-approval.** A pre-approval says "this skill, any
arguments, forever". This says "these arguments, in this sequence". Sending mail
to one address every Monday does not become permission to send mail anywhere.

**It runs through the gate, not around it.** Every step is submitted normally. A
consequential step comes back NEEDS_CONFIRMATION with an action id, and `run()`
answers it with `gate.confirm(id)` — the same call the UI makes, on the same
journalled record, producing the same history and the same undo. There is no
second execution path, so there is nothing for the gate's rules to be missing
from.

**Editing revokes it.** The approval is stamped with a fingerprint of the whole
step list. Change any argument and the fingerprint no longer matches, the
consequential steps are skipped, and the user is told it needs approving again.
Whole-list rather than per-step on purpose: "re-prompt if it is later edited" is
a statement about the sequence, and a step whose own arguments did not change can
still mean something different in new company.

A sequence that has never been approved runs its routine steps and skips its
consequential ones. It does not fail, and a routine does not ask at 9am on a
Tuesday when nobody is looking at the screen — the record says what was skipped
and why.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from .. import db
from . import store

log = logging.getLogger(__name__)

MAX_STEPS = 8


class SequenceError(Exception):
    """A routine or shortcut could not be created or changed."""


def fingerprint(steps: list[dict[str, Any]]) -> str:
    """A stable digest of what the sequence will do.

    json.dumps with sort_keys, not db.dumps, which does not sort. Without it the
    digest depends on dictionary ordering -- so a sequence reloaded from SQLite
    could hash differently from the one that was approved, and read as edited
    when nothing had changed.
    """
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


def payload(steps: list[dict[str, Any]], *, approved: bool, phrase: str = "") -> dict[str, Any]:
    return {
        "steps": steps,
        "phrase": phrase,
        # The fingerprint of what was approved, or empty for a sequence whose
        # consequential steps nobody has agreed to yet.
        "approved_fingerprint": fingerprint(steps) if approved else "",
        "approved_at": db.now() if approved else "",
    }


def validate(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check the steps name real skills, and normalise them."""
    from ..skills.registry import get_skill

    if not steps:
        raise SequenceError("It needs at least one thing to do.")
    if len(steps) > MAX_STEPS:
        raise SequenceError(f"There can be at most {MAX_STEPS} steps.")

    cleaned: list[dict[str, Any]] = []
    for step in steps:
        name = str(step.get("skill", "")).strip()
        skill = get_skill(name)
        if skill is None:
            raise SequenceError(f"'{name}' is not an available skill.")
        args = step.get("args") or {}
        if not isinstance(args, dict):
            raise SequenceError(f"The arguments for '{name}' should be a set of named values.")
        # Validated now rather than at 9am on a Tuesday: something that cannot
        # possibly run should fail while the user is still looking at it.
        try:
            args = skill.validate(args)
        except Exception as exc:  # noqa: BLE001 — SkillError and anything it wraps
            raise SequenceError(f"{name}: {exc}") from exc
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


# -- stored sequences, by kind ---------------------------------------------


def of_kind(kind: str) -> list[store.ScheduledItem]:
    return [item for item in store.active_items() if item.kind == kind]


def get(item_id: str, kind: str) -> store.ScheduledItem | None:
    item = store.get(item_id)
    if item is None or item.kind != kind:
        return None
    return item


def find(name: str, kind: str) -> store.ScheduledItem:
    """The one sequence of this kind matching `name`, or a SequenceError saying why not."""
    needle = name.strip().lower()
    if not needle:
        raise SequenceError("Which one?")
    matches = [item for item in of_kind(kind) if needle in item.label.lower()]
    exact = [item for item in matches if item.label.lower() == needle]
    if exact:
        return exact[0]
    if not matches:
        raise SequenceError(f"There's nothing called '{name}'.")
    if len(matches) > 1:
        names = ", ".join(f'"{item.label}"' for item in matches)
        raise SequenceError(f"That matches more than one: {names}. Which one?")
    return matches[0]


def needs_approval(item: store.ScheduledItem) -> bool:
    """Whether consequential steps would be skipped as things stand."""
    steps = item.payload.get("steps") or []
    if not consequential_steps(steps):
        return False
    return item.payload.get("approved_fingerprint") != fingerprint(steps)


def approve(item_id: str, kind: str) -> store.ScheduledItem | None:
    """Record that the user has agreed to these steps, as they are now."""
    item = get(item_id, kind)
    if item is None:
        return None
    steps = item.payload.get("steps") or []
    updated = dict(item.payload)
    updated["approved_fingerprint"] = fingerprint(steps)
    updated["approved_at"] = db.now()
    store.set_payload(item_id, updated)
    return get(item_id, kind)


def set_steps(item_id: str, kind: str, steps: list[dict[str, Any]]) -> store.ScheduledItem | None:
    """Change what it does, which revokes its approval.

    Not a separate "revoke" call that an editor could forget: rewriting the steps
    and clearing the approval are one operation, so there is no ordering in which
    something ends up approved for what the user never saw.
    """
    item = get(item_id, kind)
    if item is None:
        return None
    cleaned = validate(steps)
    updated = dict(item.payload)
    updated["steps"] = cleaned
    updated["approved_fingerprint"] = ""
    updated["approved_at"] = ""
    store.set_payload(item_id, updated)
    return get(item_id, kind)


def cancel(item_id: str, kind: str) -> bool:
    if get(item_id, kind) is None:
        return False
    store.cancel(item_id)
    return True


# -- running ---------------------------------------------------------------


def run(item: store.ScheduledItem) -> dict[str, Any]:
    """Execute the steps. Returns what happened, per step.

    Every step goes through `gate.submit`, so each one is journalled, previewed
    and undoable exactly as if the user had asked for it in conversation. A
    consequential step comes back needing confirmation, and the only thing this
    does that a chat turn would not is answer that confirmation on the strength
    of an approval given earlier -- and only when the fingerprint still matches
    what was approved.

    One batch id for the whole run, so "undo" takes the sequence back rather
    than its last step.
    """
    from ..actions import gate
    from ..skills.base import SkillContext

    steps = item.payload.get("steps") or []
    approved = item.payload.get("approved_fingerprint") == fingerprint(steps)
    batch_id = gate.new_batch_id()
    context = SkillContext(session_id=f"{item.kind}:{item.id}")

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
                    "message": "Needs approving before this can do it.",
                })
                continue

        results.append({
            "skill": step["skill"],
            "status": outcome.status,
            "message": outcome.message,
        })

    ran = sum(1 for r in results if r["status"] == gate.EXECUTED)
    skipped = sum(1 for r in results if r["status"] == "skipped")
    log.info("%s %s: %d of %d steps ran, %d skipped",
             item.kind, item.label, ran, len(steps), skipped)
    return {
        "id": item.id,
        "routine_id": item.id,   # the name routines used before shortcuts existed
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
            f"{skipped} skipped because it has not been approved since it changed"
        )
    failed = [r for r in outcome["steps"] if r["status"] == "failed"]
    if failed:
        parts.append(f"{len(failed)} failed: {failed[0]['message']}")
    return "; ".join(parts) + "."

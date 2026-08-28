"""Routines — REQ-12, REQ-24, REQ-25.

A trigger time and a list of actions. "Every weekday at 9, start a focus session
and read me the briefing."

Everything that makes a routine safe now lives in sequences.py, because a
shortcut (REQ-22) turned out to be the same thing triggered by name instead of
by time. The approval logic is the one place in this app where a yes outlives
the moment it was given, and two copies of it would be two things to keep right
— with the second copy always the one that gets the fix late.

What is left here is the part that is actually about routines: they have a
time, they are listed as routines, and the scheduler fires them.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from . import sequences, store
from .sequences import (  # noqa: F401 — the routine-facing names for the shared parts
    MAX_STEPS,
    SequenceError,
    consequential_steps,
    describe,
    fingerprint,
    needs_approval,
    run,
    summarise,
    validate,
)

log = logging.getLogger(__name__)

KIND = store.KIND_ROUTINE

# The name this exception had before shortcuts existed, kept so the routine
# skills read as being about routines where that is what they are about.
RoutineError = SequenceError


def create(
    *,
    label: str,
    fire_at: datetime,
    steps: list[dict[str, Any]],
    recurrence: dict[str, Any] | None = None,
    approved: bool = False,
    phrase: str = "",
) -> store.ScheduledItem:
    cleaned = sequences.validate(steps)
    return store.add(
        kind=KIND,
        label=label,
        fire_at=fire_at,
        recurrence=recurrence,
        payload=sequences.payload(cleaned, approved=approved, phrase=phrase),
    )


def all_routines() -> list[store.ScheduledItem]:
    return sequences.of_kind(KIND)


def get(routine_id: str) -> store.ScheduledItem | None:
    return sequences.get(routine_id, KIND)


def approve(routine_id: str) -> store.ScheduledItem | None:
    return sequences.approve(routine_id, KIND)


def set_steps(routine_id: str, steps: list[dict[str, Any]]) -> store.ScheduledItem | None:
    return sequences.set_steps(routine_id, KIND, steps)


def cancel(routine_id: str) -> bool:
    return sequences.cancel(routine_id, KIND)

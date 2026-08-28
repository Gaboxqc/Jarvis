"""Named shortcuts — REQ-22.

    THE SYSTEM SHALL let the user define named shortcuts for multi-step
    sequences ("work setup" → open these three apps).

A shortcut is a routine with a name where its trigger time would be. That is not
a simplification made for this file — it is the whole difference, and everything
else comes from sequences.py: the same validation, the same approval bound to a
fingerprint of the steps, the same revocation when the list changes, and the
same execution through the Action Gate.

Nothing new is stored. `scheduled_items` already holds a nullable
`next_fire_at`, and `due_items()` already refuses rows where it is null, so a
shortcut sits in the same table as the reminders and can never fire on its own.
The reminder list filters on `REMINDER_KINDS` rather than excluding kinds one at
a time, so it did not need touching for this and will not for the next kind.

One difference worth naming
---------------------------

A routine fires at 9am with nobody watching, so an unapproved step is skipped
and reported. A shortcut is run by someone who is right there, which makes
asking possible — but it still skips, for two reasons. The gate's contract is
one pending confirmation at a time, and a five-step shortcut that stopped to ask
twice would be a worse experience than the sequence it replaced. And the
approval was already taken, in full, from a preview naming every step: the only
way a shortcut has unapproved steps is that it was edited, and then the honest
thing is to say so once rather than re-ask per step.
"""

from __future__ import annotations

import logging
from typing import Any

from . import sequences, store

log = logging.getLogger(__name__)

KIND = store.KIND_SHORTCUT


def create(
    *,
    label: str,
    steps: list[dict[str, Any]],
    approved: bool = False,
) -> store.ScheduledItem:
    cleaned = sequences.validate(steps)
    if not label.strip():
        raise sequences.SequenceError("The shortcut needs a name.")
    if _by_name(label) is not None:
        raise sequences.SequenceError(
            f'There is already a shortcut called "{label.strip()}".'
        )
    return store.add(
        kind=KIND,
        label=label.strip(),
        # No trigger time. This is what makes it a shortcut rather than a
        # routine, and what keeps it out of due_items() forever.
        fire_at=None,
        payload=sequences.payload(cleaned, approved=approved),
    )


def _by_name(label: str) -> store.ScheduledItem | None:
    needle = label.strip().lower()
    for item in all_shortcuts():
        if item.label.lower() == needle:
            return item
    return None


def all_shortcuts() -> list[store.ScheduledItem]:
    return sequences.of_kind(KIND)


def get(shortcut_id: str) -> store.ScheduledItem | None:
    return sequences.get(shortcut_id, KIND)


def find(name: str) -> store.ScheduledItem:
    """The shortcut the user means, or a SequenceError saying why not.

    Names are how a shortcut is invoked, so an ambiguous one has to be an
    answerable question rather than a guess: running the wrong multi-step
    sequence is exactly the mistake the Action Gate exists to prevent, and
    picking one for them would be the assistant making it on their behalf.
    """
    return sequences.find(name, KIND)


def approve(shortcut_id: str) -> store.ScheduledItem | None:
    return sequences.approve(shortcut_id, KIND)


def set_steps(shortcut_id: str, steps: list[dict[str, Any]]) -> store.ScheduledItem | None:
    return sequences.set_steps(shortcut_id, KIND, steps)


def cancel(shortcut_id: str) -> bool:
    return sequences.cancel(shortcut_id, KIND)


def run(item: store.ScheduledItem) -> dict[str, Any]:
    return sequences.run(item)


def needs_approval(item: store.ScheduledItem) -> bool:
    return sequences.needs_approval(item)

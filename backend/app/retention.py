"""How long the record is kept — REQ-26.

Every turn writes two rows to `conversation_turns`, and every gated call writes
an `action_records` row carrying its parameters and its result. Nothing ever
removed either. An assistant used daily for a year holds thousands of rows of
what was said and what was done, and those rows are not abstract: the parameters
of a mail action are a subject line and an address, the result of a file action
is a list of the user's own paths.

The only control over any of it was `wipe_all_local_data()` -- all of it, or
none. REQ-26 asks for local data *control*, and a switch is not a dial.

What is swept, and what is not
------------------------------

Two tables, and the choice of which is the whole design:

  conversation_turns    swept. The rolling window is 20 turns and the idle gap
                        ends a conversation, so anything past the window is
                        already invisible to the assistant -- it was being kept
                        for nobody.

  action_records        swept, but only rows that are *finished*. A pending
                        confirmation is a question the user has not answered
                        yet, and expiring one by age is the gate's job, with its
                        own TTL and its own reasons.

Deliberately left alone: memory facts, tasks, notes and transcripts. Every one
of those is something the user explicitly asked to keep, is listed in the UI,
and is individually deletable. Deleting them on a timer would be the assistant
throwing away work it was told to hold on to -- which is the opposite of what a
retention setting is for.

Zero means keep forever, and that is the honest way to spell "off": a user who
wants the whole record should not have to pick a number large enough to never
arrive.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from . import db
from .actions import journal
from .settings import load_config

log = logging.getLogger(__name__)

# Statuses a row will never leave. Anything else is still in play.
_FINISHED = (
    journal.STATUS_EXECUTED,
    journal.STATUS_FAILED,
    journal.STATUS_DECLINED,
    journal.STATUS_EXPIRED,
    journal.STATUS_UNDONE,
)


def _cutoff(days: int) -> str | None:
    if days <= 0:
        return None
    moment = datetime.now(timezone.utc) - timedelta(days=days)
    return moment.replace(microsecond=0).isoformat()


def sweep() -> dict[str, int]:
    """Remove what is past its window. Returns what went, per table."""
    settings = load_config().retention
    removed = {"conversation_turns": 0, "action_records": 0}

    conversations = _cutoff(settings.conversation_days)
    if conversations is not None:
        cursor = db.execute(
            "DELETE FROM conversation_turns WHERE ts < ?", (conversations,)
        )
        removed["conversation_turns"] = cursor.rowcount or 0

    history = _cutoff(settings.history_days)
    if history is not None:
        placeholders = ", ".join("?" for _ in _FINISHED)
        cursor = db.execute(
            f"DELETE FROM action_records "
            f"WHERE created_at < ? AND status IN ({placeholders})",
            (history, *_FINISHED),
        )
        removed["action_records"] = cursor.rowcount or 0

    total = sum(removed.values())
    if total:
        log.info(
            "retention swept %d conversation turn(s) and %d action record(s)",
            removed["conversation_turns"], removed["action_records"],
        )
    return removed


def describe() -> dict[str, object]:
    """What the privacy screen shows: the window, and what is inside it now."""
    settings = load_config().retention
    turns = db.query_one("SELECT COUNT(*) AS c FROM conversation_turns")
    actions = db.query_one("SELECT COUNT(*) AS c FROM action_records")
    return {
        "conversation_days": settings.conversation_days,
        "history_days": settings.history_days,
        "conversation_turns": int(turns["c"]) if turns else 0,
        "action_records": int(actions["c"]) if actions else 0,
    }

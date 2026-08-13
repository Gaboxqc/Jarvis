"""Outbound notifications — REQ-9, REQ-27.

The scheduler dispatches a due reminder to whatever has subscribed. The CLI
subscribes; the API process did not. So in the installed desktop app a reminder
came due, was marked delivered, and nobody was ever told — the item was
consumed and silently lost, which is worse than never having fired.

This gives the API a subscriber and a small queue the UI drains. Draining is
destructive by design: a reminder is announced once, and the queue is memory
only, because a notification that survives a restart to shout about something
three days late is not a feature.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

MAX_QUEUED = 50


@dataclass
class Notification:
    id: str
    kind: str
    title: str
    body: str
    at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "at": self.at,
        }


_lock = threading.Lock()
# Bounded: a machine left off for a week should not return to hundreds of
# stacked toasts.
_queue: deque[Notification] = deque(maxlen=MAX_QUEUED)


def publish(kind: str, title: str, body: str, identifier: str = "") -> Notification:
    note = Notification(
        id=identifier or f"{kind}-{datetime.now(timezone.utc).timestamp()}",
        kind=kind,
        title=title,
        body=body,
        at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    )
    with _lock:
        _queue.append(note)
    log.info("notification queued: %s", title)
    return note


def drain() -> list[Notification]:
    """Take everything queued. Destructive — each notification is given once."""
    with _lock:
        taken = list(_queue)
        _queue.clear()
    return taken


def peek() -> list[Notification]:
    with _lock:
        return list(_queue)


def clear() -> None:
    with _lock:
        _queue.clear()


def on_scheduler_delivery(delivery: Any) -> None:
    """Subscriber for the scheduler.

    Kept tolerant of the delivery's shape: a raising subscriber would be caught
    and logged by the scheduler, which is precisely the silent loss this module
    exists to stop.
    """
    try:
        body = delivery.message()
        label = getattr(delivery, "label", "") or body
        publish(
            kind=getattr(delivery, "kind", "reminder"),
            title="Reminder",
            body=body,
            identifier=getattr(delivery, "item_id", ""),
        )
        log.info("reminder surfaced to the desktop: %s", label)
    except Exception:  # noqa: BLE001
        log.exception("could not queue a scheduler delivery")

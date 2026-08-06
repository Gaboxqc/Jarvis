"""The scheduler loop — REQ-9, REQ-12.

A single background thread polling the store every few seconds. Polling rather
than in-memory timers is a deliberate choice: it costs almost nothing at this
cadence, it survives the process being killed, and a reminder that came due
while the machine was asleep still fires on wake with its real due time attached.

Delivery is a list of subscriber callbacks so the CLI, the API and (later) the
desktop notifier can all receive the same event without the scheduler knowing
any of them exist.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from ..skills.planning.timeparse import next_occurrence
from . import store

log = logging.getLogger(__name__)

POLL_SECONDS = 5.0


@dataclass
class Delivery:
    item_id: str
    kind: str
    label: str
    due_at: datetime | None
    late_by_seconds: int
    payload: dict[str, Any]

    @property
    def was_missed(self) -> bool:
        # A minute of scheduler latency is not "missed"; an hour is.
        return self.late_by_seconds > 60

    def message(self) -> str:
        if not self.was_missed:
            return f"Reminder: {self.label}"
        when = self.due_at.astimezone().strftime("%a %d %b at %H:%M") if self.due_at else "earlier"
        # REQ-9: a late reminder states the time it was actually for.
        return f"Reminder (was due {when}): {self.label}"


Subscriber = Callable[[Delivery], None]

_subscribers: list[Subscriber] = []
_thread: threading.Thread | None = None
_stop = threading.Event()
_lock = threading.Lock()


def subscribe(callback: Subscriber) -> None:
    with _lock:
        if callback not in _subscribers:
            _subscribers.append(callback)


def unsubscribe(callback: Subscriber) -> None:
    with _lock:
        if callback in _subscribers:
            _subscribers.remove(callback)


def collect_due() -> list[Delivery]:
    """Find due items, advance them, and return what should be announced.

    Advancing before delivering means a subscriber that raises cannot cause the
    same reminder to fire forever.
    """
    from .. import focus

    # REQ-23: hold proactive output during a focus session rather than dropping
    # it. Nothing is advanced here, so everything due stays due and comes
    # through when the session ends, still carrying its original time.
    if focus.is_active():
        return []

    deliveries: list[Delivery] = []
    now = datetime.now(timezone.utc)

    for item in store.due_items():
        due_at = item.next_fire_at
        late = int((now - due_at).total_seconds()) if due_at else 0

        following = None
        if item.recurrence:
            base = due_at or now
            following = next_occurrence(item.recurrence, base.astimezone())
            # If the machine was off for days, don't replay every missed
            # occurrence — roll forward to the next one that is still ahead.
            guard = 0
            while following is not None and following <= now.astimezone() and guard < 500:
                following = next_occurrence(item.recurrence, following)
                guard += 1

        store.mark_fired(item.id, following)
        deliveries.append(
            Delivery(
                item_id=item.id,
                kind=item.kind,
                label=item.label,
                due_at=due_at,
                late_by_seconds=max(0, late),
                payload=item.payload,
            )
        )

    return deliveries


def _dispatch(deliveries: list[Delivery]) -> None:
    with _lock:
        targets = list(_subscribers)
    for delivery in deliveries:
        for callback in targets:
            try:
                callback(delivery)
            except Exception:  # noqa: BLE001 — one bad subscriber must not stop the rest
                log.exception("scheduler subscriber failed")


def tick() -> list[Delivery]:
    deliveries = collect_due()
    if deliveries:
        _dispatch(deliveries)

    # The scheduler thread is already the "something happens periodically"
    # thread, so the indexer rides along rather than starting a second one.
    # maybe_scan() is cheap when there is nothing due and returns immediately
    # when the machine should be left alone (REQ-31).
    try:
        from ..index import scanner

        scanner.maybe_scan()
    except Exception:  # noqa: BLE001 — indexing must never disturb reminders
        log.exception("background index scan failed to start")

    # Release speech models that have gone unused, so an assistant idling in
    # the tray isn't holding hundreds of MB it last needed hours ago (REQ-31).
    try:
        from ..voice import stt, tts

        stt.unload_if_idle()
        tts.unload_if_idle()

        from ..screen import capture as screen_capture

        screen_capture.unload_if_idle()
    except Exception:  # noqa: BLE001 — voice being absent is not an error here
        log.debug("voice idle check skipped", exc_info=True)

    return deliveries


def _run() -> None:
    log.info("scheduler started")
    while not _stop.is_set():
        try:
            tick()
        except Exception:  # noqa: BLE001 — the loop outlives any single failure
            log.exception("scheduler tick failed")
        _stop.wait(POLL_SECONDS)
    log.info("scheduler stopped")


def start() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_run, name="kai-scheduler", daemon=True)
    _thread.start()


def stop() -> None:
    global _thread
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=POLL_SECONDS + 1)
    _thread = None

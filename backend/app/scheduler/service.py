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
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from ..skills.planning.timeparse import next_occurrence
from . import store

log = logging.getLogger(__name__)

POLL_SECONDS = 5.0

# How long `stop()` waits for a routine that is mid-execution.
#
# Generous, because what it is usually waiting for is a connector: a routine
# step that opens IMAP has a 20-second timeout of its own, and killing it
# halfway would leave the Action Gate's journal recording an action whose
# outcome nobody knows.
ROUTINE_SHUTDOWN_SECONDS = 30.0


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


# -- routine execution, off the poll loop ---------------------------------
#
# Routines used to run inline in `tick()`, which is the same 5-second loop that
# delivers every reminder, advances the document index and unloads idle speech
# models. A routine is the one thing on that list with no bound on how long it
# takes: its steps go through the Action Gate into real skills, and a step that
# opens IMAP has a 20-second timeout of its own. For as long as one ran, every
# reminder was late and the indexer did not advance -- and `_run_routines`
# caught exceptions, so slowness was the failure mode nothing noticed.
#
# One worker, with a queue, rather than a thread per tick. Routines must not
# run concurrently with each other: each is a batch in the action journal and
# is undoable as one, and two interleaved batches would be undoable as neither.
# Serialised through a single consumer, that stays true without a second lock.
_routine_queue: queue.Queue[Delivery | None] = queue.Queue()
_routine_thread: threading.Thread | None = None
_routine_lock = threading.Lock()

# Outstanding routines, for the callers that need to know when the queue has
# drained -- `POST /reminders/tick`, which exists to make something happen now,
# and the tests. Counted here rather than read off the Queue's own
# `unfinished_tasks`, which is an implementation detail and has no way to wait
# with a timeout.
_routines_idle = threading.Condition()
_routines_pending = 0


def _routine_worker() -> None:
    try:
        _consume_routines()
    finally:
        # Connections are per thread, so this one is this worker's and nobody
        # else can use it. Closed on the way out rather than left to the
        # interpreter: a thread that exits holding a SQLite handle keeps the
        # file open, and on Windows an open handle is what stops the next
        # installer replacing it.
        from .. import db

        db.close_connection()


def _consume_routines() -> None:
    while True:
        delivery = _routine_queue.get()
        if delivery is None:
            return
        try:
            _execute_routine(delivery)
        except Exception:  # noqa: BLE001 — one bad routine must not end the worker
            log.exception("routine %s failed", delivery.label)
        finally:
            global _routines_pending
            with _routines_idle:
                _routines_pending -= 1
                if _routines_pending <= 0:
                    _routines_idle.notify_all()


def _execute_routine(delivery: Delivery) -> None:
    """Run one routine and say what happened.

    Imported here rather than at module scope: routines reach into the Action
    Gate, the gate reaches into the skill registry, and the registry is loaded
    by the app that starts this thread. A scheduler that could not be imported
    without the whole skill layer would be a scheduler nothing could test.
    """
    from . import routines as routines_module

    item = store.get(delivery.item_id)
    if item is None:
        return
    outcome = routines_module.run(item)

    from .. import notifications

    notifications.publish(
        kind="routine",
        title=delivery.label,
        body=routines_module.summarise(outcome),
        identifier=delivery.item_id,
    )


def _queue_routines(deliveries: list[Delivery]) -> None:
    """Hand due routines to the worker and return.

    The store has already advanced them (`collect_due`), so a routine cannot be
    queued twice by the next tick arriving before this one has run.
    """
    global _routine_thread, _routines_pending

    with _routine_lock:
        if _routine_thread is None or not _routine_thread.is_alive():
            _routine_thread = threading.Thread(
                target=_routine_worker, name="kai-routines", daemon=True
            )
            _routine_thread.start()

    with _routines_idle:
        _routines_pending += len(deliveries)
    for delivery in deliveries:
        _routine_queue.put(delivery)


def wait_for_routines(timeout: float = 10.0) -> bool:
    """Block until every queued routine has finished. False on timeout.

    For the callers that mean "now": `POST /reminders/tick`, whose whole
    purpose is to make the due thing happen while someone is looking at it,
    and the tests, which would otherwise assert against work that has been
    handed to a thread and not yet done.
    """
    deadline = time.monotonic() + timeout
    with _routines_idle:
        while _routines_pending > 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _routines_idle.wait(remaining)
    return True


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
    # A routine is not a reminder. Announcing "Reminder: morning start" and then
    # doing nothing would be exactly backwards: the point of a routine is that
    # it acts, and what the user wants told is what it did.
    routines_due = [d for d in deliveries if d.kind == store.KIND_ROUTINE]
    if routines_due:
        _queue_routines(routines_due)
    rest = [d for d in deliveries if d.kind != store.KIND_ROUTINE]
    if rest:
        _dispatch(rest)

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
    global _thread, _routine_thread
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=POLL_SECONDS + 1)
    _thread = None

    # The routine worker goes down after the poll loop, not with it: the loop
    # is what queues work, so stopping it first means nothing new arrives while
    # this drains. A routine mid-execution is given time to finish rather than
    # abandoned -- its steps are already in the action journal, and a journal
    # entry whose outcome nobody recorded is worse than a slow shutdown.
    with _routine_lock:
        worker, _routine_thread = _routine_thread, None
    if worker is not None and worker.is_alive():
        _routine_queue.put(None)
        worker.join(timeout=ROUTINE_SHUTDOWN_SECONDS)
        if worker.is_alive():
            log.warning("a routine was still running at shutdown; leaving it")

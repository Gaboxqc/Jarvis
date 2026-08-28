"""One stream instead of three pollers — REQ-31, REQ-32.

Three timers ran for the life of the window: presence every 2 seconds,
notifications every 5, the prerequisite check every 10. About 48 HTTP requests a
minute with the app sitting idle, and each one is not free — Starlette, a
threadpool thread, a SQLite read, and for the health check an outbound call to
Ollama. All of it to discover, forty-seven times out of forty-eight, that nothing
had changed.

This samples the same things in-process and sends only the differences. Idle
costs one open connection and a heartbeat every twenty seconds.

What is sampled, and how often
------------------------------

    presence       1s    cheap: two module flags and a boolean
    notifications  1s    a bounded in-memory deque, drained
    brain health   10s   an HTTP call to Ollama, so it keeps its own cadence

Draining is destructive, and that has one consequence worth naming: two open
windows would split the notifications between them rather than both showing all
of them. That was equally true of the polling this replaces -- `/notifications`
has always been a drain -- and the app is a single-window tray application, so
it is a property rather than a regression. It would need fixing before a second
window is ever a supported thing.

The connection is also the liveness signal. A dead backend used to be discovered
by a failing poll; now it is the stream ending, which is faster and needs no
request of its own.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import anyio

from . import focus, notifications
from .brain import llm

log = logging.getLogger(__name__)

PRESENCE_INTERVAL = 1.0
HEALTH_INTERVAL = 10.0
# Long enough to be quiet, short enough that a proxy or a sleeping NIC does not
# decide the connection is dead before the client does.
HEARTBEAT_INTERVAL = 20.0


def presence() -> dict[str, Any]:
    """What the indicator shows. Same contract as GET /state."""
    from .capture import session as capture
    from .voice import stt, tts

    if capture.is_recording():
        state = "recording"
    elif stt.is_loaded() or tts.is_loaded():
        state = "listening"
    else:
        state = "idle"

    return {
        "state": state,
        "emotion": None,
        "recording": capture.is_recording(),
        "focus": focus.is_active(),
    }


def brain_health() -> dict[str, Any]:
    health = llm.health()
    return {"ok": bool(health.get("ok")), "error": health.get("error")}


def _frame(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def stream() -> AsyncIterator[str]:
    """Server-sent events, emitted on change.

    Sampling runs in a worker thread rather than on the event loop. Each sample
    touches SQLite and, once every ten seconds, the network; doing that inline
    would stall every other request for as long as it took, which for a health
    check against an Ollama that is not running is the whole connect timeout.
    """
    last_presence: dict[str, Any] | None = None
    last_health: dict[str, Any] | None = None
    since_health = HEALTH_INTERVAL      # sample immediately on connect
    since_anything = 0.0

    # An opening frame, so a client knows the stream is live without waiting for
    # something to change. Without it a healthy idle app is indistinguishable
    # from a connection that never opened.
    yield _frame({"type": "hello"})

    while True:
        sent = False

        current = await anyio.to_thread.run_sync(presence)
        if current != last_presence:
            last_presence = current
            yield _frame({"type": "state", **current})
            sent = True

        queued = await anyio.to_thread.run_sync(notifications.drain)
        if queued:
            yield _frame({"type": "notifications", "items": [n.to_dict() for n in queued]})
            sent = True

        since_health += PRESENCE_INTERVAL
        if since_health >= HEALTH_INTERVAL:
            since_health = 0.0
            health = await anyio.to_thread.run_sync(brain_health)
            if health != last_health:
                last_health = health
                yield _frame({"type": "health", **health})
                sent = True

        since_anything = 0.0 if sent else since_anything + PRESENCE_INTERVAL
        if since_anything >= HEARTBEAT_INTERVAL:
            since_anything = 0.0
            yield _frame({"type": "heartbeat"})

        await anyio.sleep(PRESENCE_INTERVAL)

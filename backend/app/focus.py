"""Focus session state — REQ-23, REQ-31.

A focus session is the one time the assistant is asked to be less present. While
one is active:

* proactive output is held, not dropped — a reminder that comes due during a
  session fires when the session ends, with its original time (REQ-12, REQ-23);
* document indexing backs off, so the machine stays quiet (REQ-31);
* direct requests are still answered normally. Focus mode silences the
  assistant's interruptions, not the assistant.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

_lock = threading.Lock()
_until: datetime | None = None
_started: datetime | None = None
_closed_apps: tuple[str, ...] = ()


@dataclass
class FocusState:
    active: bool
    until: datetime | None
    minutes_left: int
    closed_apps: tuple[str, ...]

    def describe(self) -> str:
        if not self.active:
            return "No focus session is running."
        return f"Focus session active for another {self.minutes_left} minutes."


def start(minutes: int, closed_apps: tuple[str, ...] = ()) -> FocusState:
    global _until, _started, _closed_apps
    with _lock:
        _started = datetime.now(timezone.utc)
        _until = _started + timedelta(minutes=max(1, minutes))
        _closed_apps = closed_apps

    from .index import scanner

    scanner.pause(True)
    return state()


def end() -> FocusState:
    global _until, _started, _closed_apps
    with _lock:
        previous = _until
        _until = None
        _started = None
        _closed_apps = ()

    from .index import scanner

    scanner.pause(False)
    return FocusState(active=False, until=previous, minutes_left=0, closed_apps=())


def is_active() -> bool:
    """True while a session is running.

    Expiry is evaluated here rather than by a timer, so a session cannot outlive
    its duration just because a background thread stalled or the machine slept.
    """
    with _lock:
        if _until is None:
            return False
        if datetime.now(timezone.utc) >= _until:
            expired = True
        else:
            return True

    if expired:
        end()
    return False


def state() -> FocusState:
    active = is_active()
    with _lock:
        until = _until
        closed = _closed_apps
    remaining = 0
    if active and until is not None:
        remaining = max(0, int((until - datetime.now(timezone.utc)).total_seconds() // 60))
    return FocusState(active=active, until=until, minutes_left=remaining, closed_apps=closed)


def reset() -> None:
    """Test hook."""
    global _until, _started, _closed_apps
    with _lock:
        _until = None
        _started = None
        _closed_apps = ()

"""Talking to the cloning engine — REQ-4, REQ-31.

The engine is a separate program (see sidecar/xtts_main.py), because it needs
torch and the backend must not. This is the half that speaks to it: one JSON
object per line over a pipe, one reply per request.

The process is kept alive between requests. Loading XTTS costs tens of seconds
and about 2GB of memory, so a new process per sentence would make the feature
unusable. It is stopped when it has been idle long enough, by the same
housekeeping that unloads the speech models.

Reading every reply is not optional. The sidecar writes only protocol lines to
stdout and sends its own noise to a log file, so nothing accumulates -- but the
lesson that produced that design was a backend frozen solid by an undrained
pipe, and the same rule applies on this side: never leave output unread.
"""

from __future__ import annotations

import json
import logging
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from ..settings import data_dir, load_config

log = logging.getLogger(__name__)

# Loading the model on the first synthesis is the slow one; a ping is instant.
READY_TIMEOUT = 30.0
PING_TIMEOUT = 15.0
SYNTHESIS_TIMEOUT = 300.0
# The first load downloads the model. On a slow connection that is a long wait
# for something that is working, so it gets its own generous ceiling rather than
# failing a synthesis that had not started.
LOAD_TIMEOUT = 3600.0

_lock = threading.RLock()
_process: subprocess.Popen | None = None
_replies: queue.Queue | None = None
_last_used: float = 0.0


def available() -> bool:
    from . import engines

    return engines.xtts_installed()


def is_running() -> bool:
    return _process is not None and _process.poll() is None


def _pump(process: subprocess.Popen, sink: queue.Queue) -> None:
    """Read replies forever, so the pipe can never back up."""
    try:
        for line in process.stdout:  # type: ignore[union-attr]
            line = line.strip()
            if line:
                sink.put(line)
    except Exception:  # noqa: BLE001 - the process died; the waiter will see it
        pass
    finally:
        sink.put(None)


def _start() -> None:
    global _process, _replies

    from . import engines

    executable = engines.xtts_executable()
    if not executable.exists():
        raise RuntimeError("The voice engine is not installed.")

    # The model licence, not the cloning-consent switch. The engine refuses
    # without it, and this app has no business answering Coqui's terms on
    # behalf of whoever is using it.
    accepted = load_config().voice.xtts_licence_accepted
    environment = {
        "KAI_XTTS_LOG_DIR": str(data_dir() / "logs"),
        # The weights are ~1.8GB. They belong with the rest of the app's data,
        # not in a cache directory the user was never told about.
        "KAI_XTTS_MODEL_DIR": str(data_dir() / "engines" / "xtts-models"),
        # Passed through rather than assumed. The engine refuses to load
        # without it, and this app has no business accepting Coqui's licence
        # on behalf of whoever is using it.
        "KAI_XTTS_LICENCE_ACCEPTED": "1" if accepted else "0",
    }

    import os

    log.info("starting the voice cloning engine")
    _process = subprocess.Popen(
        [str(executable)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        # The engine logs to a file of its own; anything here would be noise
        # nobody reads, which is how pipes fill up.
        stderr=subprocess.DEVNULL,
        cwd=str(executable.parent),
        env={**os.environ, **environment},
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    _replies = queue.Queue()
    threading.Thread(
        target=_pump, args=(_process, _replies), name="xtts-replies", daemon=True
    ).start()

    hello = _await_reply(READY_TIMEOUT)
    if not hello.get("ok"):
        stop()
        raise RuntimeError(f"The voice engine would not start: {hello.get('error')}")


def _await_reply(timeout: float) -> dict[str, Any]:
    assert _replies is not None
    try:
        line = _replies.get(timeout=timeout)
    except queue.Empty:
        raise TimeoutError("The voice engine stopped responding.") from None
    if line is None:
        raise RuntimeError("The voice engine exited.")
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"The voice engine sent something unreadable: {exc}") from None


def request(payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Send one request and wait for its reply."""
    global _last_used

    with _lock:
        if not is_running():
            _start()
        assert _process is not None and _process.stdin is not None

        _process.stdin.write(json.dumps(payload) + "\n")
        _process.stdin.flush()
        reply = _await_reply(timeout)
        _last_used = time.monotonic()

    if not reply.get("ok"):
        raise RuntimeError(reply.get("error") or "The voice engine failed.")
    return reply


def ping() -> dict[str, Any]:
    return request({"op": "ping"}, PING_TIMEOUT)


def warm_up() -> dict[str, Any]:
    """Load the model, downloading it the first time.

    Called before synthesising rather than as part of it, so a first use on a
    slow connection is a long wait rather than a timeout.
    """
    return request({"op": "load"}, LOAD_TIMEOUT)


def synthesize_to_file(text: str, reference: Path, language: str, out: Path) -> dict[str, Any]:
    return request(
        {
            "op": "synthesize",
            "text": text,
            "reference": str(reference),
            "language": language,
            "out": str(out),
        },
        SYNTHESIS_TIMEOUT,
    )


def stop() -> None:
    """Shut the engine down and give back its memory."""
    global _process, _replies

    with _lock:
        process, _process = _process, None
        _replies = None

    if process is None or process.poll() is not None:
        return

    try:
        if process.stdin:
            # Closing stdin is the documented way out; the engine watches for it.
            process.stdin.close()
        process.wait(timeout=10)
    except Exception:  # noqa: BLE001
        process.kill()
    log.info("voice cloning engine stopped")


def idle_seconds() -> float:
    return time.monotonic() - _last_used if _last_used else 0.0

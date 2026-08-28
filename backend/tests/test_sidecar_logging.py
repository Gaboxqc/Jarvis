"""The backend must not be able to deadlock on its own logging — REQ-29.

Reported as "when I make a second message, she never answers". The backend was
alive, listening, and reported as Responding by Windows, and answered nothing on
any endpoint.

uvicorn logs one line per request at info level, and the desktop app spawns the
backend with its pipes connected. A pipe holds about 64KB. Once it is full the
next write blocks, and that write happens on the thread serving the request, so
the entire server stops -- permanently, with no error anywhere, because the
thing that failed was a log line.

Reproduced by spawning with the pipes undrained: wedged after 70 requests.

The parent does drain today. That is not a defence: a consumer that pauses,
crashes, or applies backpressure would kill the backend just as silently, and
nothing about the symptom points at logging. So the sidecar writes to a rotating
file instead and stops depending on anyone reading its output.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SERVER = BACKEND / "server.py"
PORT = 8796

# Comfortably past the 70 that wedged the old build, and few enough to stay
# quick: each is a bare /state, which touches no model and no network.
REQUESTS = 200


def _probe(timeout: float) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/state", timeout=timeout) as r:
            r.read()
        return True
    except Exception:
        return False


@pytest.mark.slow
def test_the_sidecar_survives_a_parent_that_never_reads_its_output(tmp_path):
    env = {
        **os.environ,
        "KAI_NO_BACKGROUND_SCAN": "1",
        "KAI_DATA_DIR": str(tmp_path),
        # What the desktop app sets. It selects file logging, which is the fix.
        "KAI_PARENT_WATCH": "1",
    }
    # stdout and stderr piped and deliberately never read, which is the state a
    # stalled or crashed parent leaves them in.
    child = subprocess.Popen(
        [sys.executable, str(SERVER), "--port", str(PORT), "--log-level", "info"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(BACKEND),
        env=env,
    )
    try:
        for _ in range(40):
            if _probe(3):
                break
            time.sleep(2)
        else:
            pytest.fail("the backend never started")

        for i in range(1, REQUESTS + 1):
            assert _probe(10), (
                f"stopped answering after {i} requests with its pipes undrained - "
                "the log output has deadlocked the server again"
            )

        # Logging went somewhere, and that somewhere is not the pipe.
        assert (tmp_path / "logs" / "backend.log").exists()
    finally:
        child.kill()
        child.wait(timeout=10)


@pytest.mark.slow
def test_running_it_by_hand_still_logs_to_the_console(tmp_path):
    """The file logging is for the sidecar, not for everyone.

    Someone running the executable from a terminal to see what it is doing must
    still see it, or the fix has traded one invisible failure for another.
    """
    env = {**os.environ, "KAI_NO_BACKGROUND_SCAN": "1", "KAI_DATA_DIR": str(tmp_path)}
    child = subprocess.Popen(
        [sys.executable, str(SERVER), "--port", str(PORT + 1), "--log-level", "info"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(BACKEND),
        env=env,
        text=True,
    )
    try:
        deadline = time.monotonic() + 45
        seen = ""
        while time.monotonic() < deadline:
            line = child.stdout.readline()
            if not line:
                break
            seen += line
            if "Uvicorn running" in seen or "Application startup" in seen:
                break
        assert "Uvicorn running" in seen or "Application startup" in seen, (
            f"no startup output on the console; got: {seen[:300]!r}"
        )
    finally:
        child.kill()
        child.wait(timeout=10)

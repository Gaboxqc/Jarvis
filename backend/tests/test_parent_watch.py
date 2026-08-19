"""The backend outliving the app that started it — REQ-29.

This was found the hard way. The desktop app kills its sidecar on exit and
always has; the code and its comment were both correct. It still left an
orphaned backend, because the app died in a way that never reached that code,
and the orphan then held port 8756 and kept its own DLLs open, so the next
installer stopped with

    Error opening file for writing: ...\\_internal\\MSVCP140_1.dll

So the child no longer depends on the parent behaving well on the way out. It
watches the stdin it inherited: empty while the parent lives, EOF the moment it
dies, whatever killed it.

These run the real entry point as a real subprocess. A unit test with a fake
stream would exercise the thread and prove nothing about whether the pipe
actually reaches EOF when a parent is killed, which is the only claim that
matters.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SERVER = BACKEND / "server.py"

# Long enough for a slow machine to notice a closed pipe, short enough that a
# broken watchdog fails the suite rather than hanging it.
GRACE_SECONDS = 15


def _spawn(env_extra: dict[str, str]) -> subprocess.Popen:
    import os

    env = {**os.environ, "KAI_NO_BACKGROUND_SCAN": "1", **env_extra}
    return subprocess.Popen(
        [sys.executable, str(SERVER), "--port", "8791", "--log-level", "warning"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(BACKEND),
        env=env,
    )


def _wait_for_exit(process: subprocess.Popen, seconds: float) -> int | None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            return code
        time.sleep(0.2)
    return None


@pytest.mark.slow
def test_the_backend_exits_when_its_parent_goes_away(tmp_path):
    """Closing the inherited stdin is the parent dying, as far as the child knows."""
    child = _spawn({"KAI_PARENT_WATCH": "1", "KAI_DATA_DIR": str(tmp_path)})
    try:
        # Give it a moment to arm the watchdog and start serving.
        time.sleep(3)
        assert child.poll() is None, "the backend exited before the parent did"

        # Exactly what the OS does when the parent process dies.
        child.stdin.close()

        assert _wait_for_exit(child, GRACE_SECONDS) is not None, (
            "the backend outlived its parent - this is the orphan that holds "
            "port 8756 and blocks the installer"
        )
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


@pytest.mark.slow
def test_the_backend_stays_up_when_nobody_asked_it_to_watch(tmp_path):
    """Run by hand, a closed stdin means nothing and must not stop the server.

    Without this the watchdog would be a quiet way to make the executable
    unusable from a terminal.
    """
    child = _spawn({"KAI_DATA_DIR": str(tmp_path)})
    try:
        time.sleep(3)
        assert child.poll() is None, "the backend exited before the parent did"

        child.stdin.close()

        assert _wait_for_exit(child, 5) is None, (
            "the backend exited on a closed stdin without being asked to watch"
        )
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)

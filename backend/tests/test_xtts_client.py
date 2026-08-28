"""Talking to the cloning engine — REQ-4.

The engine itself needs torch, which the backend's environment deliberately
does not have. So these run a stub that speaks the same protocol: one JSON
object per line, one reply per request. What is under test is the client's half
of the conversation, which is the part that ships in the backend.

The stub also lets the failure cases be exercised, and those are the ones worth
pinning: an engine that dies mid-sentence, one that never answers, and one that
returns an error rather than audio. All three are things a 2GB model process
does in the field, and none are reachable by asking the real thing nicely.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from app.voice import xtts_client


def _stub(body: str, tmp_path: Path) -> Path:
    """Write a small program that speaks the engine's protocol."""
    script = tmp_path / "stub_engine.py"
    script.write_text(
        textwrap.dedent(
            '''
            import json, sys

            def reply(payload):
                sys.stdout.write(json.dumps(payload) + "\\n")
                sys.stdout.flush()

            reply({"ok": True, "ready": True, "engine": "stub"})

            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                request = json.loads(line)
            '''
        ).strip()
        + "\n"
        + textwrap.indent(textwrap.dedent(body).strip(), "    ")
        + "\n",
        encoding="utf-8",
    )
    return script


@pytest.fixture(autouse=True)
def _point_at_stub(monkeypatch, tmp_path):
    """Run the stub through this interpreter instead of the frozen engine."""
    holder = {"script": None}

    def executable():
        return holder["script"]

    monkeypatch.setattr("app.voice.engines.xtts_executable", executable)
    monkeypatch.setattr("app.voice.engines.xtts_installed", lambda: True)

    # The client runs the path directly; a .py needs its interpreter in front.
    real_popen = xtts_client.subprocess.Popen

    def popen(args, **kwargs):
        return real_popen([sys.executable, str(args[0])], **kwargs)

    monkeypatch.setattr(xtts_client.subprocess, "Popen", popen)
    yield holder
    xtts_client.stop()


def test_a_request_gets_its_reply(_point_at_stub, tmp_path):
    _point_at_stub["script"] = _stub(
        '''
        reply({"ok": True, "engine": "stub", "device": "cpu"})
        ''',
        tmp_path,
    )

    assert xtts_client.ping()["device"] == "cpu"


def test_an_engine_error_is_raised_not_swallowed(_point_at_stub, tmp_path):
    """A failure that reads as silence is how the last three bugs hid."""
    _point_at_stub["script"] = _stub(
        '''
        reply({"ok": False, "error": "no reference recording"})
        ''',
        tmp_path,
    )

    with pytest.raises(RuntimeError, match="no reference recording"):
        xtts_client.ping()


def test_an_engine_that_dies_is_reported(_point_at_stub, tmp_path):
    """Not a hang. A 2GB model process is killed by the OS more often than most."""
    _point_at_stub["script"] = _stub(
        '''
        raise SystemExit(1)
        ''',
        tmp_path,
    )

    with pytest.raises(RuntimeError, match="exited"):
        xtts_client.ping()


def test_an_engine_that_never_answers_times_out(_point_at_stub, tmp_path, monkeypatch):
    """Waiting forever on a subprocess is the deadlock this project keeps meeting."""
    _point_at_stub["script"] = _stub(
        '''
        import time
        time.sleep(120)
        ''',
        tmp_path,
    )
    monkeypatch.setattr(xtts_client, "PING_TIMEOUT", 3.0)

    with pytest.raises(TimeoutError):
        xtts_client.ping()


def test_the_process_is_reused_between_requests(_point_at_stub, tmp_path):
    """Loading XTTS costs tens of seconds; one process per sentence is unusable."""
    _point_at_stub["script"] = _stub(
        '''
        reply({"ok": True, "engine": "stub", "device": "cpu"})
        ''',
        tmp_path,
    )

    xtts_client.ping()
    first = xtts_client._process
    xtts_client.ping()

    assert xtts_client._process is first
    assert xtts_client.is_running()


def test_stopping_closes_the_engine(_point_at_stub, tmp_path):
    _point_at_stub["script"] = _stub(
        '''
        reply({"ok": True, "engine": "stub", "device": "cpu"})
        ''',
        tmp_path,
    )

    xtts_client.ping()
    assert xtts_client.is_running()

    xtts_client.stop()

    assert not xtts_client.is_running()

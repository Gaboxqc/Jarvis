"""The event stream — REQ-31, REQ-32.

Three timers ran for the life of the window and produced about 48 requests a
minute with the app idle, to establish forty-seven times out of forty-eight that
nothing had changed. This is the replacement, and the property that makes it
worth having is not that it delivers — it is that it stays quiet.

Tested against the generator rather than through a TestClient. The stream never
ends by design, so a client would sit on it until something killed the test; the
generator can be stepped one frame at a time, which is also the only way to say
anything precise about what it does and does not emit.
"""

from __future__ import annotations

import json

import anyio
import pytest

from app import events, notifications


def parse(frame: str) -> dict:
    assert frame.startswith("data: "), frame
    assert frame.endswith("\n\n"), frame
    return json.loads(frame[len("data: "):])


async def take(stream, count: int, *, timeout: float = 10.0) -> list[dict]:
    """The next `count` frames, or a failure rather than a hung test."""
    collected: list[dict] = []
    with anyio.fail_after(timeout):
        async for frame in stream:
            collected.append(parse(frame))
            if len(collected) >= count:
                break
    return collected


@pytest.fixture
def fast(monkeypatch):
    """Real cadence compressed. The intervals are the thing being tested, so
    they are scaled rather than stubbed away."""
    monkeypatch.setattr(events, "PRESENCE_INTERVAL", 0.01)
    monkeypatch.setattr(events, "HEALTH_INTERVAL", 0.05)
    monkeypatch.setattr(events, "HEARTBEAT_INTERVAL", 0.1)
    monkeypatch.setattr(events, "brain_health", lambda: {"ok": True, "error": None})


@pytest.fixture(autouse=True)
def quiet_queue():
    notifications.drain()
    yield
    notifications.drain()


def test_it_says_hello_before_anything_has_changed(workspace, fast):
    """A healthy idle app would otherwise be indistinguishable from a connection
    that never opened."""

    async def run():
        return await take(events.stream(), 1)

    assert anyio.run(run)[0] == {"type": "hello"}


def test_the_first_sample_is_always_sent(workspace, fast):
    """A client that has just connected knows nothing yet, so the first reading
    is a change even when it matches the default."""

    async def run():
        return await take(events.stream(), 2)

    first, second = anyio.run(run)
    assert first["type"] == "hello"
    assert second["type"] == "state"
    assert second["state"] == "idle"
    assert second["recording"] is False


def test_nothing_is_sent_while_nothing_changes(workspace, fast):
    """The whole point. After the opening frames an idle app should produce a
    heartbeat and nothing else."""

    async def run():
        return await take(events.stream(), 4)

    types = [frame["type"] for frame in anyio.run(run)]

    assert types[0] == "hello"
    assert "state" in types
    # Whatever else arrives, no second `state` — the reading has not changed.
    assert types.count("state") == 1
    assert "heartbeat" in types


def test_a_change_in_presence_is_sent(workspace, fast, monkeypatch):
    readings = iter([
        {"state": "idle", "emotion": None, "recording": False, "focus": False},
        {"state": "recording", "emotion": None, "recording": True, "focus": False},
    ])
    last = {"state": "recording", "emotion": None, "recording": True, "focus": False}
    monkeypatch.setattr(events, "presence", lambda: next(readings, last))

    # Four, not two: the opening `hello` and the first `health` sample both land
    # before the second presence reading is taken.
    async def run():
        return await take(events.stream(), 4)

    frames = anyio.run(run)
    states = [f for f in frames if f["type"] == "state"]
    assert [s["state"] for s in states] == ["idle", "recording"]


def test_a_notification_is_delivered(workspace, fast):
    notifications.publish("reminder", "Stand up", "You have been sitting an hour")

    async def run():
        return await take(events.stream(), 3)

    delivered = [f for f in anyio.run(run) if f["type"] == "notifications"]
    assert delivered
    assert delivered[0]["items"][0]["title"] == "Stand up"


def test_a_notification_is_handed_out_once(workspace, fast):
    """Draining is destructive, and it was destructive before this existed too
    -- /notifications has always worked this way."""
    notifications.publish("reminder", "Stand up", "")

    async def run():
        return await take(events.stream(), 5)

    delivered = [f for f in anyio.run(run) if f["type"] == "notifications"]
    assert len(delivered) == 1


def test_brain_health_is_sent_when_it_changes(workspace, fast, monkeypatch):
    readings = iter([
        {"ok": True, "error": None},
        {"ok": False, "error": "Ollama isn't running"},
    ])
    last = {"ok": False, "error": "Ollama isn't running"}
    monkeypatch.setattr(events, "brain_health", lambda: next(readings, last))

    async def run():
        return await take(events.stream(), 8)

    health = [f for f in anyio.run(run) if f["type"] == "health"]
    assert [h["ok"] for h in health] == [True, False]
    assert health[1]["error"] == "Ollama isn't running"


def test_health_is_sampled_less_often_than_presence(workspace):
    """It is an outbound HTTP call to Ollama. Sampling it at the presence
    cadence would mean one network round trip per second, forever."""
    assert events.HEALTH_INTERVAL >= 10 * events.PRESENCE_INTERVAL


def test_the_stream_matches_what_state_reports(workspace):
    """Two ways to ask the same question, and the CLI still uses the other one."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        body = client.get("/state").json()

    assert events.presence() == body

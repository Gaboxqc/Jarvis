"""XTTS-v2's model licence — REQ-4, REQ-26.

Separate from `clone_consent`, and that separation is the point. One switch is
about the ethics of copying somebody's voice; this one is agreeing to a
non-commercial software licence. Answering both with a single tick would mean
accepting terms nobody was shown, on the strength of a sentence about something
else entirely.

The engine enforces it too -- it refuses to load without the environment
variable this sets -- so these cover the record, and sidecar/xtts_main.py covers
the refusal.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.settings import load_config, reset_config_cache


@pytest.fixture
def client(workspace, config_file):
    config_file.write_text("persona:\n  name: Kai\n", encoding="utf-8")
    reset_config_cache()
    with TestClient(app) as test_client:
        yield test_client


def test_the_licence_starts_unaccepted(client):
    body = client.get("/voice/clone").json()

    assert body["licence_accepted"] is False
    assert body["licence_accepted_at"] == ""


def test_accepting_is_recorded_with_a_date(client):
    body = client.post("/voice/clone/licence", json={"accepted": True}).json()

    assert body["licence_accepted"] is True
    assert body["licence_accepted_at"].startswith("20")
    assert load_config().voice.xtts_licence_accepted is True


def test_it_is_not_the_same_switch_as_cloning_consent(client):
    """The bug this prevents: one tick answering two different questions."""
    client.post("/voice/clone/licence", json={"accepted": True})

    body = client.get("/voice/clone").json()

    assert body["licence_accepted"] is True
    # Consent to clone a voice was never asked for and must not be assumed.
    assert body["consented"] is False


def test_consenting_to_cloning_does_not_accept_the_licence(client):
    """And the same in reverse."""
    client.post("/voice/clone/consent", json={"consent": True})

    body = client.get("/voice/clone").json()

    assert body["consented"] is True
    assert body["licence_accepted"] is False


def test_the_licence_can_be_withdrawn_and_clears_its_date(client):
    client.post("/voice/clone/licence", json={"accepted": True})

    body = client.post("/voice/clone/licence", json={"accepted": False}).json()

    assert body["licence_accepted"] is False
    assert body["licence_accepted_at"] == ""

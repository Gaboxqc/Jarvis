"""The Live2D runtime licence gate — REQ-32.

Cubism Core is bundled with the app but licensed separately by Live2D Inc, and
nobody can accept a licence on someone else's behalf. So the runtime stays
inert until the person using the app says yes, and says yes to terms they were
actually shown.

What these assert is the shape of consent rather than the wording: that it
starts off, that it can be withdrawn as easily as granted, that withdrawing
leaves no contradictory leftovers, and that the terms travel with the question.
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


def test_the_avatar_is_off_until_the_licence_is_accepted(client):
    """Off by default is the whole point: an opt-out gate is not consent."""
    body = client.get("/avatar").json()

    assert body["licence_accepted"] is False
    assert body["licence_accepted_at"] == ""


def test_the_terms_travel_with_the_question(client):
    """Agreeing to terms you were not shown is not agreement."""
    body = client.get("/avatar").json()

    assert body["licence_summary"].strip()
    assert "Live2D" in body["licence_summary"]
    assert body["licence_url"].startswith("https://")


def test_accepting_is_recorded_with_a_date(client):
    body = client.post("/avatar/licence", json={"accepted": True}).json()

    assert body["licence_accepted"] is True
    # Shown back to the user later, so it has to be a real timestamp rather
    # than a flag wearing a date's name.
    assert body["licence_accepted_at"].startswith("20")
    assert load_config().avatar.licence_accepted is True


def test_acceptance_can_be_withdrawn(client):
    """Consent that cannot be taken back is not consent."""
    client.post("/avatar/licence", json={"accepted": True})

    body = client.post("/avatar/licence", json={"accepted": False}).json()

    assert body["licence_accepted"] is False
    assert load_config().avatar.licence_accepted is False


def test_withdrawing_clears_the_date_too(client):
    """A config saying "not accepted, accepted on the 3rd" contradicts itself."""
    client.post("/avatar/licence", json={"accepted": True})

    body = client.post("/avatar/licence", json={"accepted": False}).json()

    assert body["licence_accepted_at"] == ""


def test_the_decision_survives_a_restart(client, config_file):
    """It is written to the config, not held in memory for the session."""
    client.post("/avatar/licence", json={"accepted": True})

    assert "licence_accepted" in config_file.read_text(encoding="utf-8")
    reset_config_cache()
    assert load_config().avatar.licence_accepted is True

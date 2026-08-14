"""Speaking in a cloned voice — REQ-4, REQ-26, REQ-31.

XTTS is not installed in this environment and should not be: it is a ~2GB
optional dependency, and a test suite that required it would make the whole
project unbuildable for anyone who only wants the assistant. So the engine is
stubbed and what gets tested is the machinery around it — which is where the
decisions are anyway.

Most of this file is about refusing. Cloning a voice is not a neutral act: a
convincing copy of somebody's speech can be used to say things they never said,
and the person whose voice it is may not be the person operating this app. The
gate is deliberately low — one acknowledgement — but it has to be real, and it
has to stay live rather than being a door that stays open once passed.
"""

from __future__ import annotations

import wave

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.settings import load_config, reset_config_cache
from app.voice import cloning, tts


@pytest.fixture
def client(workspace):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def reference(workspace):
    """A silent but valid 6-second reference recording."""
    path = cloning.reference_path()
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(b"\x00\x00" * 16_000 * 6)
    yield path
    if path.exists():
        path.unlink()


def consent(client, *, granted=True, enable=True):
    return client.post("/voice/clone/consent", json={"consent": granted, "enable": enable})


# -- the gate --------------------------------------------------------------


def test_cloning_is_off_until_it_is_acknowledged(client):
    status = client.get("/voice/clone").json()
    assert status["consented"] is False
    assert status["enabled"] is False


def test_recording_a_reference_is_refused_without_acknowledgement(client):
    response = client.post("/voice/clone/record")
    assert response.status_code == 403
    assert "acknowledged" in response.json()["detail"]


def test_acknowledging_enables_it(client):
    body = consent(client).json()
    assert body["consented"] is True
    assert body["enabled"] is True
    assert load_config().voice.tts_engine == "xtts"


def test_withdrawing_switches_the_engine_back(client):
    consent(client)
    body = consent(client, granted=False).json()

    assert body["consented"] is False
    assert body["enabled"] is False
    # Leaving tts_engine on xtts would make the config say one thing while the
    # behaviour did another.
    assert load_config().voice.tts_engine == "piper"


def test_consent_is_checked_at_every_use_not_only_when_granted(
    workspace, config_file, reference, monkeypatch
):
    """Clearing the acknowledgement in the file must stop cloning immediately.

    A gate that only ran at grant time would leave cloning on for an install
    whose config now says it is not permitted.
    """
    monkeypatch.setattr(cloning, "is_installed", lambda: True)

    config_file.write_text(
        "voice:\n  tts_engine: xtts\n  clone_consent: true\n", encoding="utf-8"
    )
    reset_config_cache()
    assert tts.use_cloned_voice() is True

    config_file.write_text(
        "voice:\n  tts_engine: xtts\n  clone_consent: false\n", encoding="utf-8"
    )
    reset_config_cache()
    assert tts.use_cloned_voice() is False


def test_cloning_needs_every_condition(workspace, config_file, monkeypatch):
    """Engine, consent, install and a reference. Any one missing means Piper."""
    monkeypatch.setattr(cloning, "is_installed", lambda: True)
    config_file.write_text(
        "voice:\n  tts_engine: xtts\n  clone_consent: true\n", encoding="utf-8"
    )
    reset_config_cache()

    # No reference recorded yet.
    assert tts.use_cloned_voice() is False

    monkeypatch.setattr(cloning, "has_reference", lambda: True)
    assert tts.use_cloned_voice() is True

    # Engine not installed.
    monkeypatch.setattr(cloning, "is_installed", lambda: False)
    assert tts.use_cloned_voice() is False


# -- the reference recording ----------------------------------------------


def test_a_short_sample_is_refused_with_the_reason(workspace):
    """Two seconds produces a poor clone, discovered only when it speaks."""
    with pytest.raises(cloning.CloningUnavailable, match="seconds"):
        cloning.save_reference(b"\x00\x00" * 16_000 * 2, 16_000)


def test_a_long_sample_is_trimmed_rather_than_refused(workspace):
    result = cloning.save_reference(b"\x00\x00" * 16_000 * 90, 16_000)
    assert result["seconds"] <= cloning.MAX_REFERENCE_SECONDS


def test_the_reference_can_be_deleted(client, reference):
    """It is a recording of somebody's voice (REQ-26)."""
    assert client.get("/voice/clone").json()["has_reference"] is True

    body = client.delete("/voice/clone/reference").json()
    assert body["removed"] is True
    assert body["has_reference"] is False
    assert not cloning.reference_path().exists()


def test_deleting_a_missing_reference_is_not_an_error(client):
    assert client.delete("/voice/clone/reference").json()["removed"] is False


def test_silence_is_not_saved_as_a_reference(client, monkeypatch):
    """A clone built from silence sounds like nothing, and the failure surfaces
    much later as a reply that comes out wrong."""
    from app.voice import audio

    class Silent:
        samples = None
        seconds = 12.0
        speech_detected = False
        peak_level = 0.0

    consent(client)
    monkeypatch.setattr(audio, "record_fixed", lambda seconds: Silent())

    response = client.post("/voice/clone/record")
    assert response.status_code == 400
    assert "didn't hear" in response.json()["detail"]


# -- falling back ----------------------------------------------------------


def test_a_broken_clone_falls_back_to_piper_rather_than_going_silent(
    workspace, config_file, reference, monkeypatch
):
    """Losing the timbre is cosmetic; losing the reply is not (REQ-4)."""
    monkeypatch.setattr(cloning, "is_installed", lambda: True)
    config_file.write_text(
        "voice:\n  tts_engine: xtts\n  clone_consent: true\n", encoding="utf-8"
    )
    reset_config_cache()

    def explode(text, language="en"):
        raise cloning.CloningUnavailable("the model fell over")

    monkeypatch.setattr(cloning, "synthesize", explode)

    # Stand in for Piper so the fallback has somewhere real to land.
    class FakePiper:
        def synthesize(self, text):
            chunk = type("Chunk", (), {"audio_int16_bytes": b"\x01\x00" * 100,
                                       "sample_rate": 22_050})()
            return [chunk]

    monkeypatch.setattr(tts, "load", lambda: FakePiper())

    speech = tts.synthesize("hello")

    assert speech.audio, "the reply must still be spoken, just not in the cloned voice"
    assert speech.sample_rate == 22_050  # Piper's rate, not XTTS's 24kHz


# -- what the screen is told ----------------------------------------------


def test_status_says_what_is_missing(client):
    status = client.get("/voice/clone").json()

    for key in ("installed", "enabled", "consented", "has_reference",
                "reference_seconds", "min_seconds", "licence"):
        assert key in status

    # The licence rules out selling this, which nobody should discover later.
    assert "non-commercial" in status["licence"]

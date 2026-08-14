"""Voice — REQ-1, REQ-2, REQ-3, REQ-4, REQ-26, REQ-31.

No test here opens the microphone. Capture is stubbed, and the one test that
exercises real models does it by feeding synthesized speech straight back into
the recogniser — which covers both halves and needs no hardware.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
import yaml

from app.settings import load_config, reset_config_cache
from app.voice import audio, models, session, stt, tts, wake


def enable_voice(config_file: Path, **overrides) -> None:
    raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    raw.setdefault("voice", {})
    raw["voice"].update({"enabled": True, "input_enabled": True, "output_enabled": True})
    raw["voice"].update(overrides)
    config_file.write_text(yaml.safe_dump(raw), encoding="utf-8")
    reset_config_cache()


def transcription(text: str, confidence: float = 0.9, no_speech: bool = False):
    return stt.Transcription(text=text, confidence=confidence, no_speech=no_speech)


# -- the privacy guarantee (REQ-2, REQ-26) --------------------------------


def _wake_ast() -> ast.Module:
    return ast.parse(inspect.getsource(wake))


def _imported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(alias.name for alias in node.names)
    return names


def _called_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


def test_the_wake_listener_cannot_reach_speech_to_text():
    """REQ-2: nothing is transcribed before the wake phrase fires.

    Enforced by structure rather than discipline — the module has no route to
    the transcriber at all. Parsed rather than grepped, so the requirement can
    be quoted in a docstring without the test either passing or failing for the
    wrong reason.
    """
    tree = _wake_ast()

    assert not any("stt" in name.split(".") for name in _imported_names(tree))
    assert "transcribe" not in _called_names(tree)


def test_the_wake_module_does_not_write_audio_or_send_it_anywhere():
    tree = _wake_ast()

    called = _called_names(tree)
    for forbidden in ("open", "write_bytes", "write_text", "post", "put", "send"):
        assert forbidden not in called, f"wake.py should not call {forbidden}()"

    imported = _imported_names(tree)
    for forbidden in ("httpx", "requests", "urllib", "socket", "wave"):
        assert not any(forbidden in name for name in imported), (
            f"wake.py should not import {forbidden}"
        )


# -- confidence handling (REQ-3) ------------------------------------------


def test_low_confidence_asks_the_user_to_repeat(workspace, config_file, monkeypatch):
    """A plausible sentence built from a cough must not reach the router."""
    enable_voice(config_file, min_confidence=0.6)

    spoken: list[str] = []
    monkeypatch.setattr(tts, "speak", lambda text, **kw: spoken.append(text))

    def must_not_run(*args, **kwargs):
        raise AssertionError("a low-confidence transcription reached the brain")

    from app.brain import orchestrator

    monkeypatch.setattr(orchestrator, "handle_turn", must_not_run)

    turn = session.VoiceSession().handle_transcription(
        transcription("delete all my files", confidence=0.2)
    )

    assert turn.error == "low_confidence"
    assert "repeat" in spoken[0].lower()
    assert not turn.skill_calls


def test_confident_speech_is_acted_on(workspace, config_file, monkeypatch):
    enable_voice(config_file, min_confidence=0.4)
    monkeypatch.setattr(tts, "speak", lambda text, **kw: None)

    seen = {}
    from app.brain import orchestrator

    def fake_turn(text, session_id, pending_action_id=None):
        seen["text"] = text
        return orchestrator.TurnResult(reply="Done.")

    monkeypatch.setattr(orchestrator, "handle_turn", fake_turn)

    turn = session.VoiceSession().handle_transcription(
        transcription("what is on my list", confidence=0.85)
    )

    assert seen["text"] == "what is on my list"
    assert turn.reply == "Done."
    assert turn.error is None


def test_silence_is_not_treated_as_a_request(workspace, config_file, monkeypatch):
    enable_voice(config_file)
    monkeypatch.setattr(tts, "speak", lambda text, **kw: None)

    turn = session.VoiceSession().handle_transcription(
        transcription("you", confidence=0.9, no_speech=True)
    )

    assert turn.error
    assert not turn.skill_calls


# -- confirmations over voice (REQ-24) ------------------------------------


def test_a_spoken_yes_still_has_to_carry_the_action_id(workspace, config_file, monkeypatch):
    """Voice is a transport. It gets no shortcut through the Action Gate."""
    enable_voice(config_file)
    monkeypatch.setattr(tts, "speak", lambda text, **kw: None)

    from app.brain import orchestrator

    calls: list[str | None] = []

    def fake_turn(text, session_id, pending_action_id=None):
        calls.append(pending_action_id)
        if text == "tidy my downloads":
            return orchestrator.TurnResult(
                reply="Move 12 files. Go ahead?",
                pending=orchestrator.PendingAction("act-123", "system.organize_folder",
                                                   "Move 12 files", True),
            )
        return orchestrator.TurnResult(reply="Moved 12 files.")

    monkeypatch.setattr(orchestrator, "handle_turn", fake_turn)

    voice = session.VoiceSession()
    first = voice.handle_transcription(transcription("tidy my downloads"))
    assert first.pending_action_id == "act-123"

    voice.handle_transcription(transcription("yes"))

    # The second turn carried the id, rather than the word "yes" meaning anything.
    assert calls == [None, "act-123"]


def test_a_mis_heard_yes_below_confidence_confirms_nothing(workspace, config_file, monkeypatch):
    enable_voice(config_file, min_confidence=0.6)
    monkeypatch.setattr(tts, "speak", lambda text, **kw: None)

    from app.brain import orchestrator

    calls: list[str | None] = []

    def fake_turn(text, session_id, pending_action_id=None):
        calls.append(pending_action_id)
        return orchestrator.TurnResult(
            reply="Delete 40 files. Go ahead?",
            pending=orchestrator.PendingAction("act-9", "system.organize_folder", "x", True),
        )

    monkeypatch.setattr(orchestrator, "handle_turn", fake_turn)

    voice = session.VoiceSession()
    voice.handle_transcription(transcription("tidy up"))
    voice.handle_transcription(transcription("yes", confidence=0.15))

    # Only the first turn reached the brain; the unclear "yes" never did.
    assert calls == [None]


# -- muting (REQ-4) -------------------------------------------------------


def test_muting_output_does_not_disable_anything_else(workspace, config_file, monkeypatch):
    enable_voice(config_file, output_enabled=False)

    from app.brain import orchestrator

    monkeypatch.setattr(
        orchestrator, "handle_turn",
        lambda text, sid, pending_action_id=None: orchestrator.TurnResult(reply="Task added."),
    )

    turn = session.VoiceSession().handle_transcription(transcription("add a task"))

    assert turn.reply == "Task added."   # the work still happened
    assert turn.spoke is False           # it just wasn't spoken


def test_speak_is_a_noop_when_voice_is_off(workspace, config_file):
    enable_voice(config_file, enabled=False)
    assert tts.speak("anything") is None


def test_voice_input_off_is_reported_not_silently_ignored(workspace, config_file):
    enable_voice(config_file, input_enabled=False)

    turn = session.VoiceSession().listen_once()

    assert "turned off" in (turn.error or "")


# -- degradation (REQ-27) -------------------------------------------------


def test_a_missing_speech_model_is_reported_clearly(workspace, monkeypatch):
    monkeypatch.setattr(models, "whisper_present", lambda size: False)
    stt.unload()

    with pytest.raises(stt.STTUnavailable, match="isn't downloaded"):
        stt.load()


def test_a_missing_voice_is_reported_clearly(workspace, monkeypatch, tmp_path):
    monkeypatch.setattr(models, "piper_voice_paths",
                        lambda vid: (tmp_path / "nope.onnx", tmp_path / "nope.json"))
    tts.unload()

    with pytest.raises(tts.TTSUnavailable, match="isn't downloaded"):
        tts.load()


def test_losing_speech_output_does_not_lose_the_turn(workspace, config_file, monkeypatch):
    """The work already happened; failing to say it aloud is a degradation."""
    enable_voice(config_file)

    def broken(text, **kwargs):
        raise tts.TTSUnavailable("speaker on fire")

    monkeypatch.setattr(tts, "speak", broken)

    from app.brain import orchestrator

    monkeypatch.setattr(
        orchestrator, "handle_turn",
        lambda text, sid, pending_action_id=None: orchestrator.TurnResult(reply="Reminder set."),
    )

    turn = session.VoiceSession().handle_transcription(transcription("remind me"))

    assert turn.reply == "Reminder set."
    assert turn.spoke is False


# -- model management (REQ-29, REQ-31) ------------------------------------


def test_models_are_reported_before_being_downloaded(workspace):
    entries = models.status()

    assert {e.kind for e in entries} == {"speech-to-text", "text-to-speech", "wake word"}
    assert all(e.approx_mb > 0 for e in entries)


def test_wake_models_are_not_required_when_wake_is_off(workspace, config_file):
    enable_voice(config_file, wake_enabled=False)

    kinds = {entry.kind for entry in models.missing()}

    assert "wake word" not in kinds


def test_idle_unload_releases_the_model(workspace, config_file, monkeypatch):
    """REQ-31 — an assistant idling in the tray shouldn't hold the weights."""
    enable_voice(config_file, unload_after_minutes=1)
    monkeypatch.setattr(stt, "_model", object())
    monkeypatch.setattr(stt, "_last_used", 0.0)  # long ago on the monotonic clock

    assert stt.unload_if_idle() is True
    assert stt.is_loaded() is False


def test_a_recently_used_model_is_kept(workspace, config_file, monkeypatch):
    import time

    enable_voice(config_file, unload_after_minutes=10)
    monkeypatch.setattr(stt, "_model", object())
    monkeypatch.setattr(stt, "_last_used", time.monotonic())

    assert stt.unload_if_idle() is False


# -- audio helpers --------------------------------------------------------


def test_pcm_conversion_round_trips():
    import numpy as np

    original = np.array([0, 16384, -16384, 32767], dtype=np.int16)
    converted = audio.pcm16_to_float32(original.tobytes())

    assert converted.dtype == np.float32
    assert converted[0] == pytest.approx(0.0)
    assert converted[1] == pytest.approx(0.5, abs=0.001)


def test_resampling_changes_length_proportionally():
    import numpy as np

    samples = np.zeros(22_050, dtype=np.float32)  # one second
    resampled = audio.resample(samples, 22_050, 16_000)

    assert len(resampled) == pytest.approx(16_000, abs=2)


def test_resampling_is_a_noop_at_the_same_rate():
    import numpy as np

    samples = np.zeros(100, dtype=np.float32)
    assert audio.resample(samples, 16_000, 16_000) is samples


# -- the real thing -------------------------------------------------------


needs_models = pytest.mark.skipif(
    not (models.whisper_present("base") and models.piper_voice_paths("en_US-amy-medium")[0].exists()),
    reason="voice models not downloaded",
)


@needs_models
@pytest.mark.slow
def test_synthesized_speech_is_recognised_again():
    """End-to-end through both real models, with no microphone involved."""
    phrase = "Remind me to call the dentist in twenty minutes."

    speech = tts.synthesize(phrase)
    assert speech.seconds > 1.0

    samples = audio.resample(
        audio.pcm16_to_float32(speech.audio), speech.sample_rate, 16_000
    )
    heard = stt.transcribe(samples)

    assert "dentist" in heard.text.lower()
    assert heard.is_confident(0.4)


@needs_models
@pytest.mark.slow
def test_speech_can_be_written_as_a_wav_file(tmp_path):
    speech = tts.synthesize("Testing one two three.")
    path = speech.write_wav(tmp_path / "out.wav")

    assert path.exists()
    assert path.read_bytes()[:4] == b"RIFF"


# -- speaking, and the shape the avatar reads (REQ-4, REQ-32) -------------


def test_speak_actually_plays(workspace, config_file, monkeypatch):
    """The endpoint used to synthesize and throw the audio away.

    The CLI and the voice loop both call tts.speak(), which plays; this endpoint
    called synthesize(), which does not. So the desktop app's "speak replies
    aloud" switch produced silence while every other path worked, and nothing
    caught it because nothing asserted sound came out.
    """
    from fastapi.testclient import TestClient

    from app.main import app
    from app.settings import reset_config_cache
    from app.voice import audio, tts

    config_file.write_text(
        "voice:\n  enabled: true\n  output_enabled: true\n", encoding="utf-8"
    )
    reset_config_cache()

    played: list[tuple[int, int]] = []
    monkeypatch.setattr(
        tts, "synthesize",
        lambda text: tts.Speech(audio=b"\x10\x00" * 16_000, sample_rate=16_000),
    )
    monkeypatch.setattr(
        audio, "play",
        lambda pcm, rate, blocking=True: played.append((len(pcm), rate)),
    )

    with TestClient(app) as client:
        body = client.post("/voice/speak", json={"text": "hello"}).json()

    assert played, "no audio reached the output device"
    assert body["spoke"] is True


def test_speak_is_silent_when_muted_without_being_an_error(workspace, config_file):
    """Muting output disables output and nothing else (REQ-4)."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.settings import reset_config_cache

    config_file.write_text(
        "voice:\n  enabled: true\n  output_enabled: false\n", encoding="utf-8"
    )
    reset_config_cache()

    with TestClient(app) as client:
        response = client.post("/voice/speak", json={"text": "hello"})

    assert response.status_code == 200
    assert response.json()["spoke"] is False


def test_the_envelope_tracks_loudness(workspace):
    """It drives the avatar's mouth, so quiet must read as closed."""
    import math
    import struct

    from app.voice import tts

    rate = 16_000
    # A second of silence, then a second of tone.
    quiet = b"\x00\x00" * rate
    loud = b"".join(struct.pack("<h", int(20000 * math.sin(i * 0.2))) for i in range(rate))
    shape = tts.envelope(tts.Speech(audio=quiet + loud, sample_rate=rate), fps=30)

    assert len(shape) == 60, "two seconds at 30fps"
    assert max(shape[:25]) < 0.05, "silence should keep the mouth shut"
    assert max(shape[35:]) > 0.8, "the tone should open it"
    assert all(0.0 <= value <= 1.0 for value in shape)


def test_an_empty_utterance_has_no_envelope(workspace):
    from app.voice import tts

    assert tts.envelope(tts.Speech(audio=b"", sample_rate=22_050)) == []

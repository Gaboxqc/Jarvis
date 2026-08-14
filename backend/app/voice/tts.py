"""Text to speech — REQ-4, REQ-26, REQ-31.

Piper, running locally. The voice model stays on this machine.

Muting is a property of output only: REQ-4 requires that turning speech off
disables nothing else, so `speak()` becomes a no-op while every other capability
carries on. The reply is still produced, still shown, still acted on.
"""

from __future__ import annotations

import io
import logging
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..settings import load_config
from . import models

log = logging.getLogger(__name__)


class TTSUnavailable(Exception):
    """The voice isn't installed or couldn't be loaded."""


@dataclass
class Speech:
    audio: bytes  # 16-bit PCM, mono
    sample_rate: int

    @property
    def seconds(self) -> float:
        return len(self.audio) / 2 / self.sample_rate if self.sample_rate else 0.0

    def to_wav_bytes(self) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(self.sample_rate)
            handle.writeframes(self.audio)
        return buffer.getvalue()

    def write_wav(self, path: Path) -> Path:
        path.write_bytes(self.to_wav_bytes())
        return path


_lock = threading.Lock()
_voice: Any = None
_voice_id: str = ""
_last_used: float = 0.0


def is_loaded() -> bool:
    return _voice is not None


def load() -> Any:
    global _voice, _voice_id, _last_used

    config = load_config().voice
    with _lock:
        if _voice is not None and _voice_id == config.voice_id:
            _last_used = time.monotonic()
            return _voice

        onnx, config_json = models.piper_voice_paths(config.voice_id)
        if not onnx.exists():
            raise TTSUnavailable(
                f"The voice '{config.voice_id}' isn't downloaded yet. Run voice setup first."
            )

        try:
            from piper import PiperVoice
        except ImportError as exc:
            raise TTSUnavailable("piper-tts isn't installed.") from exc

        log.info("loading piper voice %s", config.voice_id)
        try:
            _voice = PiperVoice.load(
                onnx, config_path=config_json if config_json.exists() else None
            )
        except Exception as exc:  # noqa: BLE001
            raise TTSUnavailable(f"The voice wouldn't load: {exc}") from exc

        _voice_id = config.voice_id
        _last_used = time.monotonic()
        return _voice


def unload() -> bool:
    global _voice, _voice_id
    with _lock:
        if _voice is None:
            return False
        _voice = None
        _voice_id = ""
    log.info("released piper voice")
    return True


def unload_if_idle() -> bool:
    if _voice is None:
        return False
    minutes = load_config().voice.unload_after_minutes
    if minutes <= 0:
        return False
    if time.monotonic() - _last_used < minutes * 60:
        return False
    return unload()


def use_cloned_voice() -> bool:
    """Whether this install should speak in the cloned voice.

    Every condition has to hold. Consent is checked here rather than only where
    it is granted, so that clearing it in the config file switches cloning off
    on the next sentence — the acknowledgement is a live setting, not a
    one-time gate that stays open once passed.
    """
    from . import cloning

    config = load_config().voice
    return (
        config.tts_engine == "xtts"
        and config.clone_consent
        and cloning.is_installed()
        and cloning.has_reference()
    )


def synthesize(text: str) -> Speech:
    """Render text to PCM. Raises TTSUnavailable rather than returning silence."""
    global _last_used

    text = (text or "").strip()
    if not text:
        return Speech(audio=b"", sample_rate=22_050)

    if use_cloned_voice():
        from . import cloning

        try:
            pcm, rate = cloning.synthesize(text, language=load_config().voice.language)
            return Speech(audio=pcm, sample_rate=rate)
        except cloning.CloningUnavailable as exc:
            # Fall through to Piper rather than going silent. Losing the cloned
            # timbre is a cosmetic downgrade; losing the reply is not, and REQ-4
            # says speech is a way out, never a capability of its own.
            log.warning("cloned voice unavailable, falling back to piper: %s", exc)

    voice = load()
    chunks: list[bytes] = []
    sample_rate = 22_050

    try:
        for chunk in voice.synthesize(text):
            audio = getattr(chunk, "audio_int16_bytes", None)
            if audio is None:  # older piper returned raw bytes
                audio = bytes(chunk)
            chunks.append(audio)
            sample_rate = int(getattr(chunk, "sample_rate", sample_rate))
    except Exception as exc:  # noqa: BLE001
        raise TTSUnavailable(f"Speech synthesis failed: {exc}") from exc
    finally:
        _last_used = time.monotonic()

    return Speech(audio=b"".join(chunks), sample_rate=sample_rate)


def speak(text: str, *, blocking: bool = True) -> Speech | None:
    """Say something out loud, unless speech output is muted.

    Returns None when muted — the caller carries on regardless, because muting
    speech must not change what the assistant actually does (REQ-4).
    """
    config = load_config().voice
    if not config.enabled or not config.output_enabled:
        return None

    speech = synthesize(text)
    if not speech.audio:
        return speech

    from .audio import play

    play(speech.audio, speech.sample_rate, blocking=blocking)
    return speech


def available_voices() -> list[str]:
    """Voices already downloaded, for the settings screen (REQ-4)."""
    return sorted(path.stem for path in models.piper_dir().glob("*.onnx"))


def status() -> dict[str, Any]:
    config = load_config().voice
    onnx, _ = models.piper_voice_paths(config.voice_id)
    return {
        "voice": config.voice_id,
        "installed": onnx.exists(),
        "loaded": is_loaded(),
        "muted": not (config.enabled and config.output_enabled),
        "available": available_voices(),
    }

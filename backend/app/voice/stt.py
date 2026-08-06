"""Speech to text — REQ-3, REQ-26, REQ-31.

faster-whisper, running locally. Raw audio never leaves the machine.

The model is loaded on first use and released after an idle period. A desktop
assistant that sits in the tray all day holding 150MB of weights it last used
at breakfast is exactly what REQ-31 is about.

Low-confidence results are surfaced rather than acted on. Whisper will happily
transcribe a cough into a plausible sentence, and a plausible sentence is what
makes it dangerous — it reaches the router looking like an instruction.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..settings import load_config
from . import models

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000


class STTUnavailable(Exception):
    """The model isn't installed or couldn't be loaded."""


@dataclass
class Transcription:
    text: str
    confidence: float
    language: str = ""
    duration: float = 0.0
    no_speech: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()

    def is_confident(self, threshold: float | None = None) -> bool:
        if threshold is None:
            threshold = load_config().voice.min_confidence
        return not self.is_empty and not self.no_speech and self.confidence >= threshold

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "confidence": round(self.confidence, 3),
            "language": self.language,
            "duration": round(self.duration, 2),
            "no_speech": self.no_speech,
        }


_lock = threading.Lock()
_model: Any = None
_model_size: str = ""
_last_used: float = 0.0


def is_loaded() -> bool:
    return _model is not None


def load() -> Any:
    """Load the model, reusing it across calls."""
    global _model, _model_size, _last_used

    config = load_config().voice
    with _lock:
        if _model is not None and _model_size == config.stt_model:
            _last_used = time.monotonic()
            return _model

        if not models.whisper_present(config.stt_model):
            raise STTUnavailable(
                f"The speech model (whisper-{config.stt_model}) isn't downloaded yet. "
                "Run voice setup first."
            )

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise STTUnavailable("faster-whisper isn't installed.") from exc

        log.info("loading whisper model %s", config.stt_model)
        try:
            _model = WhisperModel(
                config.stt_model,
                device="cpu",
                compute_type="int8",
                download_root=str(models.whisper_dir()),
                local_files_only=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise STTUnavailable(f"The speech model wouldn't load: {exc}") from exc

        _model_size = config.stt_model
        _last_used = time.monotonic()
        return _model


def unload() -> bool:
    global _model, _model_size
    with _lock:
        if _model is None:
            return False
        _model = None
        _model_size = ""
    log.info("released whisper model")
    return True


def unload_if_idle() -> bool:
    """Called from the scheduler tick."""
    if _model is None:
        return False
    minutes = load_config().voice.unload_after_minutes
    if minutes <= 0:
        return False
    if time.monotonic() - _last_used < minutes * 60:
        return False
    return unload()


def transcribe(audio: Any, *, language: str | None = None) -> Transcription:
    """Transcribe mono 16 kHz float32 audio, or a path to an audio file."""
    global _last_used

    model = load()
    config = load_config().voice
    source = str(audio) if isinstance(audio, (str, Path)) else audio

    try:
        segments, info = model.transcribe(
            source,
            language=language or config.language or None,
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        collected = list(segments)
    except Exception as exc:  # noqa: BLE001
        raise STTUnavailable(f"Transcription failed: {exc}") from exc
    finally:
        _last_used = time.monotonic()

    text = " ".join(segment.text.strip() for segment in collected).strip()
    return Transcription(
        text=text,
        confidence=_confidence(collected),
        language=getattr(info, "language", "") or "",
        duration=float(getattr(info, "duration", 0.0) or 0.0),
        no_speech=_is_silence(collected),
    )


def _confidence(segments: list[Any]) -> float:
    """Collapse per-segment log probabilities into one 0-1 score.

    Weighted by segment duration so a long clear sentence is not dragged down by
    a short uncertain fragment at the end.
    """
    if not segments:
        return 0.0

    total_weight = 0.0
    total = 0.0
    for segment in segments:
        weight = max(0.1, float(getattr(segment, "end", 0)) - float(getattr(segment, "start", 0)))
        avg_logprob = float(getattr(segment, "avg_logprob", -1.0))
        # avg_logprob is a mean log probability per token; exponentiating puts
        # it back on a probability scale that a threshold can be reasoned about.
        total += math.exp(max(-10.0, min(0.0, avg_logprob))) * weight
        total_weight += weight

    score = total / total_weight if total_weight else 0.0

    worst_no_speech = max(
        (float(getattr(s, "no_speech_prob", 0.0)) for s in segments), default=0.0
    )
    return max(0.0, min(1.0, score * (1.0 - worst_no_speech)))


def _is_silence(segments: list[Any]) -> bool:
    if not segments:
        return True
    return all(float(getattr(s, "no_speech_prob", 0.0)) > 0.7 for s in segments)


def status() -> dict[str, Any]:
    config = load_config().voice
    return {
        "model": config.stt_model,
        "installed": models.whisper_present(config.stt_model),
        "loaded": is_loaded(),
        "idle_seconds": round(time.monotonic() - _last_used, 1) if _model else None,
    }

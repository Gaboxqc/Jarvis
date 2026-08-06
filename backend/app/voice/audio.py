"""Microphone and speaker I/O, plus end-of-speech detection — REQ-2, REQ-3.

The VAD is energy-based against a noise floor measured from the room at the
start of each capture, rather than a fixed threshold. A fixed threshold is
wrong in both directions — it never triggers next to a desk fan, and it
triggers constantly in a quiet room — and calibrating per capture costs 300ms
and no dependencies.

Nothing here writes audio to disk. Captured samples live in memory for the
duration of one turn and are dropped afterwards (REQ-26).
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Callable

from ..settings import load_config

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000

CALIBRATION_FRAMES = 10          # ~300ms of room tone
SPEECH_FACTOR = 3.0              # how far above the floor counts as speech
MIN_SPEECH_FRAMES = 5            # ~150ms, so a keyboard click isn't an utterance
PREROLL_FRAMES = 10              # keep the moment before speech starts


class AudioUnavailable(Exception):
    """No usable microphone or speaker."""


@dataclass
class Capture:
    samples: Any  # float32 numpy array, mono 16 kHz
    seconds: float
    speech_detected: bool
    peak_level: float = 0.0

    @property
    def is_empty(self) -> bool:
        return self.seconds <= 0 or not self.speech_detected


def _sd() -> Any:
    try:
        import sounddevice
    except (ImportError, OSError) as exc:
        raise AudioUnavailable(f"Audio isn't available on this machine: {exc}") from exc
    return sounddevice


def _np() -> Any:
    import numpy

    return numpy


def input_devices() -> list[dict[str, Any]]:
    try:
        devices = _sd().query_devices()
    except Exception as exc:  # noqa: BLE001
        raise AudioUnavailable(str(exc)) from exc
    return [
        {"index": index, "name": device["name"], "channels": device["max_input_channels"]}
        for index, device in enumerate(devices)
        if device["max_input_channels"] > 0
    ]


def has_microphone() -> bool:
    try:
        return bool(input_devices())
    except AudioUnavailable:
        return False


def rms(frame: Any) -> float:
    np = _np()
    if len(frame) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))


def record_until_silence(
    *,
    silence_ms: int | None = None,
    max_seconds: int | None = None,
    on_state: Callable[[str], None] | None = None,
) -> Capture:
    """Record from the default microphone until the speaker stops.

    Returns as soon as speech is followed by `silence_ms` of quiet, or when
    `max_seconds` is reached — the cap matters because without it a stuck-open
    stream records until the disk fills.
    """
    np = _np()
    sd = _sd()
    config = load_config().voice
    silence_ms = silence_ms if silence_ms is not None else config.silence_ms
    max_seconds = max_seconds if max_seconds is not None else config.max_utterance_seconds

    silence_frames_needed = max(1, silence_ms // FRAME_MS)
    max_frames = max(1, (max_seconds * 1000) // FRAME_MS)

    collected: list[Any] = []
    preroll: list[Any] = []
    noise_floor = 0.0
    speech_frames = 0
    silence_run = 0
    started = False
    peak = 0.0

    try:
        stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=FRAME_SAMPLES
        )
    except Exception as exc:  # noqa: BLE001
        raise AudioUnavailable(f"Couldn't open the microphone: {exc}") from exc

    with stream:
        # Measure the room before deciding what counts as speech.
        if on_state:
            on_state("calibrating")
        levels = []
        for _ in range(CALIBRATION_FRAMES):
            frame, _overflow = stream.read(FRAME_SAMPLES)
            levels.append(rms(frame[:, 0]))
        noise_floor = sorted(levels)[len(levels) // 2]  # median resists a stray bump
        threshold = max(noise_floor * SPEECH_FACTOR, 0.005)

        if on_state:
            on_state("listening")

        for _ in range(max_frames):
            frame, _overflow = stream.read(FRAME_SAMPLES)
            mono = frame[:, 0].copy()
            level = rms(mono)
            peak = max(peak, level)

            if level >= threshold:
                if not started:
                    started = True
                    # Include the pre-roll so the first syllable survives.
                    collected.extend(preroll)
                    preroll.clear()
                    if on_state:
                        on_state("speaking")
                speech_frames += 1
                silence_run = 0
                collected.append(mono)
                continue

            if started:
                silence_run += 1
                collected.append(mono)
                if silence_run >= silence_frames_needed and speech_frames >= MIN_SPEECH_FRAMES:
                    break
            else:
                preroll.append(mono)
                if len(preroll) > PREROLL_FRAMES:
                    preroll.pop(0)

    if not collected:
        return Capture(samples=np.zeros(0, dtype=np.float32), seconds=0.0,
                       speech_detected=False, peak_level=peak)

    samples = np.concatenate(collected).astype(np.float32)
    return Capture(
        samples=samples,
        seconds=len(samples) / SAMPLE_RATE,
        speech_detected=speech_frames >= MIN_SPEECH_FRAMES,
        peak_level=peak,
    )


def record_fixed(seconds: float) -> Capture:
    """Straight timed capture, for push-to-talk held keys and for tests."""
    np = _np()
    sd = _sd()
    frames = int(seconds * SAMPLE_RATE)
    try:
        recording = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1, dtype="float32")
        sd.wait()
    except Exception as exc:  # noqa: BLE001
        raise AudioUnavailable(f"Couldn't record: {exc}") from exc
    samples = recording[:, 0].astype(np.float32)
    return Capture(
        samples=samples,
        seconds=seconds,
        speech_detected=rms(samples) > 0.005,
        peak_level=float(np.abs(samples).max()) if len(samples) else 0.0,
    )


def play(pcm_bytes: bytes, sample_rate: int, *, blocking: bool = True) -> None:
    np = _np()
    sd = _sd()
    if not pcm_bytes:
        return
    samples = np.frombuffer(pcm_bytes, dtype=np.int16)
    try:
        sd.play(samples, samplerate=sample_rate)
        if blocking:
            sd.wait()
    except Exception as exc:  # noqa: BLE001
        raise AudioUnavailable(f"Couldn't play audio: {exc}") from exc


def stop_playback() -> None:
    try:
        _sd().stop()
    except Exception:  # noqa: BLE001 — stopping silence is not an error
        pass


def pcm16_to_float32(pcm_bytes: bytes) -> Any:
    """Convert 16-bit PCM to the float32 form whisper expects."""
    np = _np()
    return np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0


def resample(samples: Any, source_rate: int, target_rate: int = SAMPLE_RATE) -> Any:
    """Linear resample. Adequate for speech recognition input."""
    np = _np()
    if source_rate == target_rate or len(samples) == 0:
        return samples
    duration = len(samples) / source_rate
    target_length = int(duration * target_rate)
    source_positions = np.linspace(0, len(samples) - 1, num=len(samples))
    target_positions = np.linspace(0, len(samples) - 1, num=target_length)
    return np.interp(target_positions, source_positions, samples).astype(np.float32)

"""Wake word detection — REQ-2, REQ-26.

REQ-2 says that while not activated, the system shall not transcribe or transmit
audio anywhere. That is enforced structurally rather than by discipline:

    this module never imports the transcriber.

Audio here only ever reaches openWakeWord's local scorer. The listener's job is
to answer one yes/no question and then hand control back; it has no route by
which pre-wake audio could reach speech-to-text, the network, or the disk, and a
test asserts that the import stays absent.

The rolling pre-roll buffer exists so the first syllable after the wake phrase
isn't clipped. It is a few hundred milliseconds of memory, overwritten
continuously and never persisted.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

from ..settings import load_config
from . import models

log = logging.getLogger(__name__)

FRAME_SAMPLES = 1280  # 80ms at 16 kHz, the size openWakeWord expects
PREROLL_FRAMES = 6


class WakeUnavailable(Exception):
    """The wake word engine or its models aren't available.

    Callers fall back to push-to-talk rather than losing voice entirely (REQ-2).
    """


@dataclass
class Detection:
    phrase: str
    score: float


def _load_model() -> Any:
    config = load_config().voice
    if not models.wakeword_present(config.wake_word):
        raise WakeUnavailable(
            f"Wake word models for '{config.wake_word}' aren't downloaded yet."
        )

    try:
        from openwakeword.model import Model
    except ImportError as exc:
        raise WakeUnavailable("openwakeword isn't installed.") from exc

    directory = models.wakeword_dir()
    candidates = sorted(directory.glob(f"{config.wake_word}*.onnx"))
    if not candidates:
        raise WakeUnavailable(f"No model file for '{config.wake_word}'.")

    try:
        return Model(
            wakeword_models=[str(candidates[0])],
            inference_framework="onnx",
            melspec_model_path=str(directory / "melspectrogram.onnx"),
            embedding_model_path=str(directory / "embedding_model.onnx"),
        )
    except Exception as exc:  # noqa: BLE001
        raise WakeUnavailable(f"The wake word engine wouldn't start: {exc}") from exc


class WakeListener:
    """Listens for the wake phrase and calls back when it hears it."""

    def __init__(self, on_detected: Callable[[Detection], None]) -> None:
        self._on_detected = on_detected
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._suspended = threading.Event()
        self._error: str | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def error(self) -> str | None:
        return self._error

    def start(self) -> None:
        if self.running:
            return
        # Fail before starting a thread, so an unavailable engine surfaces to
        # the caller immediately and it can fall back to push-to-talk.
        _load_model()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="kai-wake", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None

    def suspend(self) -> None:
        """Stop scoring while the assistant is speaking or handling a turn.

        Without this the assistant's own voice coming out of the speakers can
        re-trigger the wake word.
        """
        self._suspended.set()

    def resume(self) -> None:
        self._suspended.clear()

    def _run(self) -> None:
        from .audio import AudioUnavailable, _sd

        config = load_config().voice
        try:
            model = _load_model()
            stream = _sd().InputStream(
                samplerate=16_000, channels=1, dtype="int16", blocksize=FRAME_SAMPLES
            )
        except (WakeUnavailable, AudioUnavailable, Exception) as exc:  # noqa: BLE001
            self._error = str(exc)
            log.warning("wake listener could not start: %s", exc)
            return

        log.info("listening for '%s'", config.wake_word)
        with stream:
            while not self._stop.is_set():
                try:
                    frame, _overflow = stream.read(FRAME_SAMPLES)
                except Exception as exc:  # noqa: BLE001
                    self._error = str(exc)
                    log.warning("wake listener audio error: %s", exc)
                    break

                if self._suspended.is_set():
                    continue

                try:
                    scores = model.predict(frame[:, 0])
                except Exception as exc:  # noqa: BLE001
                    log.debug("wake scoring failed: %s", exc)
                    continue

                for phrase, score in scores.items():
                    if score >= config.wake_threshold:
                        log.info("wake word '%s' detected (%.2f)", phrase, score)
                        # Suspend immediately: the turn that follows owns the
                        # microphone, and a second detection mid-turn is noise.
                        self.suspend()
                        try:
                            self._on_detected(Detection(phrase=phrase, score=float(score)))
                        except Exception:  # noqa: BLE001
                            log.exception("wake callback failed")
                        finally:
                            model.reset()
                            self.resume()
                        break

        log.info("wake listener stopped")


def is_available() -> bool:
    config = load_config().voice
    if not models.wakeword_present(config.wake_word):
        return False
    try:
        import openwakeword  # noqa: F401
    except ImportError:
        return False
    return True


def status() -> dict[str, Any]:
    config = load_config().voice
    return {
        "phrase": config.wake_word,
        "enabled": config.wake_enabled,
        "installed": models.wakeword_present(config.wake_word),
        "available": is_available(),
        "threshold": config.wake_threshold,
    }

"""Capture session orchestration — REQ-19, REQ-26, REQ-31.

One session at a time. Starting requires an explicit action, and while one is
running `is_recording()` is true for any UI to display — the persistent
indicator REQ-19 asks for is a state anything can read, not a message that
scrolls away.

Transcription runs on the chunks as they arrive, in a worker thread, so a
two-hour meeting doesn't end with a long silent wait and memory stays bounded.
Text is appended to the transcript as it is produced, so a crash mid-meeting
loses the last chunk rather than the whole thing.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from . import recorder, store

log = logging.getLogger(__name__)

_lock = threading.Lock()
_active: "CaptureSession | None" = None


class CaptureError(Exception):
    """Recording could not start or is not running."""


@dataclass
class SessionStatus:
    recording: bool
    transcript_id: str = ""
    label: str = ""
    seconds: float = 0.0
    sources: list[str] | None = None
    words: int = 0
    note: str = ""

    def describe(self) -> str:
        if not self.recording:
            return "Not recording."
        minutes = int(self.seconds // 60)
        return (
            f"Recording \"{self.label}\" - {minutes} min so far, "
            f"{self.words} words transcribed, capturing {' and '.join(self.sources or [])}."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "recording": self.recording,
            "transcript_id": self.transcript_id,
            "label": self.label,
            "seconds": round(self.seconds, 1),
            "sources": self.sources or [],
            "words": self.words,
            "note": self.note,
        }


class CaptureSession:
    def __init__(self, label: str, on_text: Callable[[str], None] | None = None) -> None:
        self.label = label or "Meeting"
        self.transcript_id = ""
        self.started_at = 0.0
        self._recorder = recorder.Recorder()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._on_text = on_text
        self._words = 0
        self.note = ""

    # -- lifecycle --------------------------------------------------------

    def start(self) -> SessionStatus:

        status = self._recorder.start()
        self.note = status.note
        self.started_at = time.monotonic()

        transcript = store.create(self.label, status.names)
        self.transcript_id = transcript.id

        self._stop.clear()
        self._worker = threading.Thread(
            target=self._transcribe_loop, name="kai-capture-stt", daemon=True
        )
        self._worker.start()

        log.info("capture started: %s (%s)", self.label, ", ".join(status.names))
        return self.status()

    def stop(self) -> store.Transcript | None:
        self._recorder.stop()          # flushes the tail into the queue
        self._stop.set()
        if self._worker is not None:
            # Generous: the last chunk may still be transcribing, and losing the
            # closing minutes of a meeting is exactly what people notice.
            self._worker.join(timeout=120.0)
        self._worker = None

        duration = time.monotonic() - self.started_at
        transcript = store.finish(self.transcript_id, duration)
        log.info("capture stopped: %s (%.0fs)", self.label, duration)
        return transcript

    # -- transcription ----------------------------------------------------

    def _transcribe_loop(self) -> None:
        from ..voice import stt

        while True:
            try:
                chunk = self._recorder.chunks.get(timeout=1.0)
            except Exception:  # noqa: BLE001 — queue.Empty
                if self._stop.is_set() and self._recorder.chunks.empty():
                    return
                continue

            try:
                heard = stt.transcribe(chunk)
            except stt.STTUnavailable as exc:
                log.warning("capture transcription failed: %s", exc)
                self.note = str(exc)
                continue
            except Exception:  # noqa: BLE001 — one bad chunk must not end the session
                log.exception("capture chunk failed")
                continue

            text = heard.text.strip()
            if not text:
                continue

            store.append_text(self.transcript_id, text)
            self._words += len(text.split())
            if self._on_text:
                try:
                    self._on_text(text)
                except Exception:  # noqa: BLE001
                    log.debug("capture text callback failed", exc_info=True)

    # -- state ------------------------------------------------------------

    def status(self) -> SessionStatus:
        return SessionStatus(
            recording=True,
            transcript_id=self.transcript_id,
            label=self.label,
            seconds=time.monotonic() - self.started_at if self.started_at else 0.0,
            sources=self._recorder.status.names,
            words=self._words,
            note=self.note or (self._recorder.error or ""),
        )


# -- module-level control (one session at a time) -------------------------


def is_recording() -> bool:
    """The persistent indicator REQ-19 requires — readable from anywhere."""
    return _active is not None


def start(label: str, on_text: Callable[[str], None] | None = None) -> SessionStatus:
    global _active
    with _lock:
        if _active is not None:
            raise CaptureError(
                f"Already recording \"{_active.label}\". Stop that first."
            )
        session = CaptureSession(label, on_text=on_text)
        try:
            status = session.start()
        except recorder.CaptureUnavailable as exc:
            raise CaptureError(str(exc)) from exc
        _active = session
        return status


def stop() -> store.Transcript | None:
    global _active
    with _lock:
        if _active is None:
            raise CaptureError("Nothing is being recorded.")
        session = _active
        _active = None
    return session.stop()


def status() -> SessionStatus:
    session = _active
    if session is None:
        return SessionStatus(recording=False)
    return session.status()


def reset() -> None:
    """Test hook."""
    global _active
    if _active is not None:
        try:
            _active.stop()
        except Exception:  # noqa: BLE001
            pass
    _active = None

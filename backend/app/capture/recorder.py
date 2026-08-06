"""Audio capture for meetings — REQ-19, REQ-26.

Records the microphone in fixed-length chunks for transcription.

WHY MICROPHONE ONLY
-------------------
Capturing system audio too — the other participants on a video call — was built
and then removed, because it could not be made reliable on Windows. The findings
are recorded here so it isn't re-attempted blind:

* `sounddevice` cannot do WASAPI loopback at all: its `WasapiSettings` exposes
  no `loopback` option and no loopback devices appear in its device list.
* `soundcard` can, and does it correctly in isolation.
* But `soundcard` binds its COM apartment to the thread that first imports it,
  and any earlier use of `sounddevice` in the same process — which the voice
  stack does, so a single `/listen` beforehand is enough — makes its loopback
  recorder terminate the interpreter outright. No exception, no traceback,
  exit code 127.
* Two simultaneous `soundcard` recorders crash the same way, whether on one
  thread or two, so going through one library isn't a way around it.
* Isolating `soundcard` in a subprocess and piping audio back produced no
  samples either.

So this records one source reliably rather than two unreliably. For an in-person
meeting or dictation that is complete. For a remote call it captures your side
only, and `capture.start` says so before recording rather than leaving it to be
discovered afterwards in the transcript.

Nothing is written to disk here. Audio is transcribed in chunks and the samples
are dropped; only text is persisted (REQ-26).
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
CHUNK_SECONDS = 20
BLOCK = 1024


class CaptureUnavailable(Exception):
    """No usable audio input."""


def _np() -> Any:
    import numpy

    return numpy


def microphone_available() -> bool:
    from ..voice.audio import has_microphone

    return has_microphone()


def system_audio_available() -> tuple[bool, str]:
    """Always unavailable in this build — see the module docstring for why."""
    return False, (
        "capturing the other participants isn't supported on Windows in this "
        "build, so only your side is recorded"
    )


@dataclass
class SourceStatus:
    microphone: bool = False
    system_audio: bool = False
    note: str = ""

    @property
    def names(self) -> list[str]:
        found = []
        if self.microphone:
            found.append("microphone")
        if self.system_audio:
            found.append("system audio")
        return found

    def describe(self) -> str:
        if not self.names:
            return "no audio sources available"
        return " and ".join(self.names)


def probe() -> SourceStatus:
    system_ok, reason = system_audio_available()
    return SourceStatus(
        microphone=microphone_available(),
        system_audio=system_ok,
        # Stated up front, so an incomplete recording announces itself before it
        # starts rather than after (REQ-19).
        note="" if system_ok else reason,
    )


class Recorder:
    """Captures the microphone into fixed-length chunks."""

    def __init__(self, chunk_seconds: int = CHUNK_SECONDS) -> None:
        self.chunk_seconds = max(5, chunk_seconds)
        self.chunks: queue.Queue = queue.Queue()
        self.status = probe()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._buffer: list[Any] = []
        self._lock = threading.Lock()
        self._error: str | None = None

    @property
    def error(self) -> str | None:
        return self._error

    def start(self) -> SourceStatus:
        if not self.status.microphone:
            raise CaptureUnavailable("There's no microphone available to record.")
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="kai-capture", daemon=True)
        self._thread.start()
        return self.status

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._thread = None
        self._flush()

    # -- capture ----------------------------------------------------------

    def _run(self) -> None:
        from ..voice.audio import _sd

        try:
            stream = _sd().InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=BLOCK
            )
        except Exception as exc:  # noqa: BLE001
            self._error = f"microphone unavailable: {exc}"
            log.warning(self._error)
            return

        target = self.chunk_seconds * SAMPLE_RATE
        with stream:
            while not self._stop.is_set():
                try:
                    frame, _overflow = stream.read(BLOCK)
                except Exception as exc:  # noqa: BLE001
                    self._error = f"microphone stopped: {exc}"
                    log.warning(self._error)
                    break
                with self._lock:
                    self._buffer.append(frame[:, 0].copy())
                    buffered = sum(len(b) for b in self._buffer)
                # Chunk during the meeting, not after it: memory stays bounded
                # and a two-hour call isn't followed by a long silent wait.
                if buffered >= target:
                    self._emit(target)

    def _emit(self, count: int | None = None) -> None:
        np = _np()
        with self._lock:
            if not self._buffer:
                return
            joined = np.concatenate(self._buffer)
            self._buffer.clear()

            take = count or len(joined)
            chunk, leftover = joined[:take], joined[take:]
            if len(leftover):
                # Audio straddling a chunk boundary is carried over, not dropped.
                self._buffer.append(leftover)

        if len(chunk):
            self.chunks.put(chunk.astype("float32"))

    def _flush(self) -> None:
        with self._lock:
            remaining = sum(len(b) for b in self._buffer)
        if remaining > SAMPLE_RATE // 2:  # ignore sub-half-second tails
            self._emit()

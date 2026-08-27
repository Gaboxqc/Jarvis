"""The voice cloning engine, as a separate program — REQ-4.

XTTS cannot live in the backend. It needs torch, which is around 2GB and brings
its own numpy, transformers and numba; putting it in the backend's environment
would upgrade numpy under onnxruntime and piper, and would add 2GB to an
installer for a feature most people never turn on. So it is built separately,
with its own virtual environment, and spoken to over a pipe.

**The protocol is one JSON object per line, in and out.** Requests arrive on
stdin, one reply goes out per request, and the process exits when stdin closes
-- which is how it learns the backend has gone, the same trick the backend uses
on the app.

    -> {"op": "ping"}
    <- {"ok": true, "engine": "xtts-v2", "device": "cpu"}

    -> {"op": "synthesize", "text": "...", "reference": "ref.wav",
        "language": "en", "out": "reply.wav"}
    <- {"ok": true, "path": "reply.wav", "seconds": 3.2, "sample_rate": 24000}

    <- {"ok": false, "error": "what went wrong, in a sentence"}

Audio goes to a file the caller names rather than through the pipe. A few
seconds of 24kHz speech is megabytes; base64 through stdout would be slower,
larger, and would put the whole reply into a buffer that has to be drained
perfectly.

**Nothing but protocol goes to stdout.** torch and TTS print freely -- download
bars, warnings, model summaries -- and any of it on stdout would corrupt the
protocol. Worse, a parent that stops reading would block the writer and freeze
this process, which is exactly the deadlock that took the backend down. So file
descriptor 1 is pointed at a log file before anything is imported, and the real
stdout is kept aside for replies alone. That covers prints from C extensions
too, which redirecting `sys.stdout` alone would not.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ENGINE = "xtts-v2"
MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"

# Set by the backend, and only when the person using the app has accepted that
# XTTS-v2 is non-commercial (Coqui CPML). Coqui's own loader looks for
# COQUI_TOS_AGREED; this refuses to set it on anyone's behalf.
LICENCE_ENV = "KAI_XTTS_LICENCE_ACCEPTED"


def _split_stdout(log_path: Path):
    """Keep stdout for the protocol; send everything else to a file.

    Returns the protocol stream. Must run before torch or TTS are imported,
    because the point is to catch what they print on the way in.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        protocol = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n")
    except OSError:
        # A windowed build with nobody on the other end has no usable fd 1.
        # There is no way to answer anyone, so record why and stop rather
        # than run a service that cannot reply.
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write("no stdout to speak on; spawn this with pipes\n")
        raise SystemExit(2)

    log = open(log_path, "a", encoding="utf-8")
    os.dup2(log.fileno(), 1)   # anything writing to fd 1 now lands in the file
    os.dup2(log.fileno(), 2)
    sys.stdout = log
    sys.stderr = log
    return protocol


def _reply(stream, payload: dict) -> None:
    stream.write(json.dumps(payload) + "\n")
    stream.flush()


class Engine:
    """Loads XTTS once, on the first request that needs it."""

    def __init__(self) -> None:
        self._tts = None

    @property
    def device(self) -> str:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"

    def load(self):
        if self._tts is not None:
            return self._tts

        if os.environ.get(LICENCE_ENV) != "1":
            raise RuntimeError(
                "XTTS-v2 is licensed for non-commercial use under the Coqui "
                "Public Model License, and that has not been accepted."
            )
        # Coqui reads this itself when fetching the model.
        os.environ["COQUI_TOS_AGREED"] = "1"

        from TTS.api import TTS

        self._tts = TTS(MODEL_NAME).to(self.device)
        return self._tts

    def synthesize(self, text: str, reference: str, language: str, out: str) -> dict:
        if not text.strip():
            raise ValueError("nothing to say")
        if not Path(reference).exists():
            raise FileNotFoundError(f"no reference recording at {reference}")

        tts = self.load()
        started = time.monotonic()
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        tts.tts_to_file(
            text=text,
            speaker_wav=reference,
            language=language or "en",
            file_path=out,
        )

        import wave

        with wave.open(out, "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()

        return {
            "ok": True,
            "path": out,
            "seconds": round(frames / float(rate or 1), 3),
            "sample_rate": rate,
            "took_seconds": round(time.monotonic() - started, 2),
        }


def handle(engine: Engine, request: dict) -> dict:
    op = request.get("op")

    if op == "ping":
        # Deliberately does not load the model: the caller uses this to check
        # the process is alive, and loading takes tens of seconds.
        return {"ok": True, "engine": ENGINE, "device": engine.device}

    if op == "synthesize":
        return engine.synthesize(
            text=request.get("text", ""),
            reference=request.get("reference", ""),
            language=request.get("language", "en"),
            out=request.get("out", ""),
        )

    if op == "shutdown":
        return {"ok": True, "bye": True}

    raise ValueError(f"unknown op {op!r}")


def main() -> int:
    log_dir = Path(os.environ.get("KAI_XTTS_LOG_DIR") or Path.home() / ".kai-xtts")
    protocol = _split_stdout(log_dir / "xtts.log")

    engine = Engine()
    _reply(protocol, {"ok": True, "ready": True, "engine": ENGINE})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _reply(protocol, {"ok": False, "error": f"bad request: {exc}"})
            continue

        try:
            response = handle(engine, request)
        except Exception as exc:  # noqa: BLE001 - every failure is a reply
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        _reply(protocol, response)
        if request.get("op") == "shutdown":
            break

    # stdin closed: the backend is gone, and so is any reason to stay.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

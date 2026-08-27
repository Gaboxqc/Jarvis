"""Speaking in a cloned voice — REQ-4, REQ-26, REQ-31.

Piper cannot do this. It plays fixed pre-trained voices; `en_US-amy-medium` is a
model file, not a timbre you can steer. Cloning means a second engine, and this
is it: XTTS-v2, which reproduces a voice from a few seconds of reference audio.

Three things shape the design.

**It is optional and heavy.** The model is around 2GB and wants the GPU that is
already holding the language model. So the import is deferred, absence is
reported as a plain sentence rather than an exception nobody can act on, and
Piper stays the default. An install without XTTS is a working install.

**The reference recording is personal data.** It is a sample of somebody's
voice, kept on disk so it can be reused. It lives in the data directory, is
listed by /voice/clone, and is deleted by one call — the same treatment every
other piece of recorded audio gets (REQ-26).

**Cloning a voice is not a neutral act.** A convincing copy of someone's speech
can be used to say things they never said, and the person whose voice it is may
not be the person operating this app. So enabling it requires an explicit
acknowledgement stored in config, and refusing to record that acknowledgement
leaves the feature off. That is a low bar and deliberately so: this is a local
assistant reading your own replies aloud, not a publishing tool. The bar exists
so the choice is made once, on purpose, rather than arrived at by clicking.

Licensing is worth stating in the source rather than only in a commit message:
XTTS-v2 ships under the Coqui Public Model License, which is non-commercial.
Fine for a personal assistant; not fine to sell. Nothing here enforces that --
it is the operator's call -- but nobody should discover it by accident.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any

from ..settings import data_dir, load_config

log = logging.getLogger(__name__)

# Reference audio shorter than this produces a poor clone; the model needs a
# few seconds of continuous speech to characterise a voice.
MIN_REFERENCE_SECONDS = 5.0
MAX_REFERENCE_SECONDS = 30.0

REFERENCE_NAME = "voice-reference.wav"


class CloningUnavailable(Exception):
    """Cloning is not set up, and the message says what is missing."""


_lock = threading.Lock()
_model: Any = None
_last_used: float = 0.0


def reference_path() -> Path:
    """Where the reference recording lives. One per install, by design.

    Multiple stored voice samples would make this a voice library, which is a
    different and much more sensitive product than "read my replies in my own
    voice".
    """
    return data_dir() / REFERENCE_NAME


def has_reference() -> bool:
    return reference_path().exists()


def _engine_installed() -> bool:
    """Whether the downloadable sidecar is present on disk."""
    from . import engines

    return engines.xtts_installed()


def _package_importable() -> bool:
    """Whether XTTS is importable in this process.

    Only true in a checkout that installed it deliberately. A frozen build never
    has it -- that is the whole reason the sidecar exists.
    """
    from importlib.util import find_spec

    try:
        return find_spec("TTS") is not None
    except (ImportError, ValueError):  # pragma: no cover - malformed install
        return False


def is_installed() -> bool:
    """Whether cloning can actually run, by either route.

    Two of them: the package imported into this process, which only a checkout
    has, and the downloaded sidecar, which is how a packaged build gets there.
    Answered without importing anything -- find_spec and a stat, because the
    settings screen asks this on every render and importing XTTS costs seconds.
    """
    return _package_importable() or _engine_installed()


def is_loaded() -> bool:
    return _model is not None


def load() -> Any:
    """Load XTTS, or explain precisely what is missing."""
    global _model, _last_used

    if not is_installed():
        raise CloningUnavailable(
            "Voice cloning needs the XTTS engine, which isn't installed. "
            "Install it with: pip install TTS"
        )
    if not has_reference():
        raise CloningUnavailable(
            "There's no reference recording yet. Record a short sample first."
        )

    with _lock:
        if _model is not None:
            _last_used = time.monotonic()
            return _model

        try:
            from TTS.api import TTS as CoquiTTS
        except Exception as exc:  # noqa: BLE001
            raise CloningUnavailable(f"The XTTS engine wouldn't import: {exc}") from exc

        log.info("loading XTTS-v2 (this takes a moment and about 2GB)")
        try:
            # GPU when there is one. The language model is already resident, so
            # this is the second tenant of the card and may fall back to CPU on
            # a smaller one -- slower, but not broken.
            _model = CoquiTTS("tts_models/multilingual/multi-dataset/xtts_v2").to(_device())
        except Exception as exc:  # noqa: BLE001
            raise CloningUnavailable(f"XTTS wouldn't load: {exc}") from exc

        _last_used = time.monotonic()
        return _model


def _device() -> str:
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"


def unload() -> bool:
    """Release the model. Two gigabytes is worth reclaiming when idle (REQ-31)."""
    global _model
    with _lock:
        if _model is None:
            return False
        _model = None

    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass

    log.info("released XTTS")
    return True


def unload_if_idle() -> bool:
    if _model is None:
        return False
    minutes = load_config().voice.unload_after_minutes
    if minutes <= 0 or time.monotonic() - _last_used < minutes * 60:
        return False
    return unload()


def synthesize(text: str, language: str = "en") -> tuple[bytes, int]:
    """Render text in the cloned voice. Returns (PCM16 mono, sample rate)."""
    global _last_used

    # The sidecar first: in a packaged build it is the only route, and where
    # both exist it is the one that was deliberately installed.
    if not _package_importable() and _engine_installed():
        return _synthesize_via_sidecar(text, language)

    model = load()
    try:
        import numpy as np

        samples = model.tts(
            text=text,
            speaker_wav=str(reference_path()),
            language=language,
        )
        array = np.asarray(samples, dtype="float32")
        # XTTS returns float32 in [-1, 1]; everything downstream is PCM16.
        clipped = np.clip(array, -1.0, 1.0)
        pcm = (clipped * 32767).astype("<i2").tobytes()
    except Exception as exc:  # noqa: BLE001
        raise CloningUnavailable(f"Cloned speech failed: {exc}") from exc
    finally:
        _last_used = time.monotonic()

    return pcm, 24_000  # XTTS-v2 outputs at 24kHz


def _synthesize_via_sidecar(text: str, language: str) -> tuple[bytes, int]:
    """Ask the separate engine, and read back what it wrote.

    It renders to a file rather than returning audio down the pipe: seconds of
    24kHz speech are megabytes, and a reply that large has to be drained
    perfectly or it blocks the writer.
    """
    import tempfile

    from . import xtts_client

    if not has_reference():
        raise CloningUnavailable(
            "There's no reference recording yet. Upload a short sample first."
        )

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "cloned.wav"
        try:
            xtts_client.synthesize_to_file(text, reference_path(), language, out)
            return pcm_from_wav(out.read_bytes())
        except CloningUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CloningUnavailable(f"Cloned speech failed: {exc}") from exc


def pcm_from_wav(raw: bytes) -> tuple[bytes, int]:
    """Decode an uploaded WAV to mono PCM16.

    Stereo is mixed down rather than refused -- most phone recordings are two
    identical channels, and rejecting them would send people to an audio editor
    for no reason. Anything that is not 16-bit is refused by name instead of
    being reinterpreted, because misreading the sample width produces a file
    that loads happily and sounds like static.
    """
    import io
    import wave

    # audioop would have done the downmix, but it was removed in Python 3.13.
    # numpy is already a dependency and lets the sample width and byte order be
    # stated explicitly rather than assumed from the platform.
    import numpy as np

    try:
        with wave.open(io.BytesIO(raw), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.readframes(handle.getnframes())
    except Exception as exc:  # noqa: BLE001
        raise CloningUnavailable(
            "That doesn't look like a readable WAV file."
        ) from exc

    if width != 2:
        raise CloningUnavailable(
            f"That WAV is {width * 8}-bit. Please export it as 16-bit PCM."
        )
    if not frames:
        raise CloningUnavailable("That file has no audio in it.")

    if channels > 1:
        samples = np.frombuffer(frames, dtype="<i2")
        # Trailing bytes from a truncated file would make the reshape fail.
        usable = (len(samples) // channels) * channels
        # int32 for the sum: two samples near full scale overflow int16 before
        # the division, which shows up as loud crackling in the clone.
        mixed = samples[:usable].reshape(-1, channels).astype("<i4").mean(axis=1)
        frames = mixed.astype("<i2").tobytes()

    return frames, rate


def save_reference(pcm: bytes, sample_rate: int) -> dict[str, Any]:
    """Store the reference recording, replacing any previous one."""
    seconds = len(pcm) / 2 / sample_rate if sample_rate else 0.0
    if seconds < MIN_REFERENCE_SECONDS:
        raise CloningUnavailable(
            f"That's only {seconds:.1f} seconds. The model needs at least "
            f"{MIN_REFERENCE_SECONDS:.0f} seconds of continuous speech to copy a voice."
        )

    import wave

    path = reference_path()
    # Written whole to a temporary file and moved, so an interrupted save cannot
    # leave a half-written reference that fails later with a confusing error.
    temporary = path.with_suffix(".tmp.wav")
    with wave.open(str(temporary), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm[: int(MAX_REFERENCE_SECONDS * sample_rate) * 2])
    temporary.replace(path)

    # A new voice means the loaded model's cached speaker latents are stale.
    unload()
    log.info("saved a %0.1fs voice reference", min(seconds, MAX_REFERENCE_SECONDS))
    return {"seconds": round(min(seconds, MAX_REFERENCE_SECONDS), 1), "path": str(path)}


def forget_reference() -> bool:
    """Delete the recording. It is a sample of someone's voice (REQ-26)."""
    path = reference_path()
    if not path.exists():
        return False
    path.unlink()
    unload()
    log.info("deleted the voice reference")
    return True


def status() -> dict[str, Any]:
    config = load_config().voice
    path = reference_path()
    return {
        "installed": is_installed(),
        # Present but separate from `installed`: the engine being on disk is
        # not the same as being able to synthesise with it, and conflating the
        # two would let the card offer a working feature before it works.
        "engine_installed": _engine_installed(),
        # Whether this is a frozen build. It decides what "not installed" means:
        # in a checkout it is a missing package somebody can install, and in a
        # packaged app it is a component that was never shipped, where advice to
        # run pip is not just useless but misleading -- there is no environment
        # to install into.
        "packaged": bool(getattr(sys, "frozen", False)),
        "enabled": config.tts_engine == "xtts",
        "consented": config.clone_consent,
        "has_reference": path.exists(),
        "reference_seconds": _reference_seconds(path),
        "loaded": is_loaded(),
        "min_seconds": MIN_REFERENCE_SECONDS,
        # Said plainly rather than buried: the licence rules out selling this.
        "licence": "XTTS-v2 is licensed for non-commercial use (Coqui CPML).",
        # Its own acceptance, separate from clone_consent above.
        "licence_accepted": config.xtts_licence_accepted,
        "licence_accepted_at": config.xtts_licence_accepted_at,
    }


def _reference_seconds(path: Path) -> float:
    if not path.exists():
        return 0.0
    try:
        import wave

        with wave.open(str(path), "rb") as handle:
            return round(handle.getnframes() / float(handle.getframerate() or 1), 1)
    except Exception:  # noqa: BLE001
        return 0.0

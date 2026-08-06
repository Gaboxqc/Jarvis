"""Voice model management — REQ-4, REQ-26, REQ-29, REQ-30.

Speech models are hundreds of megabytes. They are downloaded by an explicit
action, never silently on first import, for three reasons: the user should know
when their machine is fetching that much data, a download that starts inside a
"say hello" turn looks like a hang, and a metered connection is the user's
business rather than ours.

Everything lands under the Kai data directory, so the models are removed with
the rest of the local data rather than being scattered through per-library
caches the user would never find.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..settings import data_dir, load_config

log = logging.getLogger(__name__)

# Approximate download sizes, so the user is told what they are agreeing to.
WHISPER_SIZES_MB = {"tiny": 75, "base": 145, "small": 480, "medium": 1500}
PIPER_QUALITY_MB = {"low": 25, "medium": 65, "high": 115}


def models_root() -> Path:
    root = data_dir() / "models"
    root.mkdir(parents=True, exist_ok=True)
    return root


def whisper_dir() -> Path:
    path = models_root() / "whisper"
    path.mkdir(parents=True, exist_ok=True)
    return path


def piper_dir() -> Path:
    path = models_root() / "piper"
    path.mkdir(parents=True, exist_ok=True)
    return path


def wakeword_dir() -> Path:
    path = models_root() / "wakeword"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class ModelStatus:
    name: str
    kind: str
    present: bool
    detail: str = ""
    approx_mb: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "present": self.present,
            "detail": self.detail,
            "approx_mb": self.approx_mb,
        }


def piper_voice_paths(voice_id: str) -> tuple[Path, Path]:
    directory = piper_dir()
    return directory / f"{voice_id}.onnx", directory / f"{voice_id}.onnx.json"


def _piper_quality(voice_id: str) -> str:
    tail = voice_id.rsplit("-", 1)[-1]
    return tail if tail in PIPER_QUALITY_MB else "medium"


def whisper_present(model_size: str) -> bool:
    """Whether faster-whisper has this model cached locally.

    Checked by looking for the CTranslate2 weights file rather than by asking
    the library, because asking would trigger a download.
    """
    root = whisper_dir()
    if not root.is_dir():
        return False
    for candidate in root.rglob("model.bin"):
        if model_size in str(candidate.parent).lower() or model_size in candidate.parent.name:
            return True
    # Some snapshots use the size only in the repo directory name.
    return any(model_size in p.name.lower() for p in root.iterdir() if p.is_dir()) and any(
        root.rglob("model.bin")
    )


def wakeword_present(wake_word: str) -> bool:
    directory = wakeword_dir()
    if not directory.is_dir():
        return False
    has_feature_models = any(directory.glob("melspectrogram.*")) and any(
        directory.glob("embedding_model.*")
    )
    has_wakeword = any(directory.glob(f"{wake_word}*.onnx")) or any(
        directory.glob(f"{wake_word}*.tflite")
    )
    return has_feature_models and has_wakeword


def status() -> list[ModelStatus]:
    config = load_config().voice
    onnx, _ = piper_voice_paths(config.voice_id)
    return [
        ModelStatus(
            name=f"whisper-{config.stt_model}",
            kind="speech-to-text",
            present=whisper_present(config.stt_model),
            detail=str(whisper_dir()),
            approx_mb=WHISPER_SIZES_MB.get(config.stt_model, 150),
        ),
        ModelStatus(
            name=config.voice_id,
            kind="text-to-speech",
            present=onnx.exists(),
            detail=str(onnx),
            approx_mb=PIPER_QUALITY_MB.get(_piper_quality(config.voice_id), 65),
        ),
        ModelStatus(
            name=config.wake_word,
            kind="wake word",
            present=wakeword_present(config.wake_word),
            detail=str(wakeword_dir()),
            approx_mb=8,
        ),
    ]


def missing(include_wake: bool | None = None) -> list[ModelStatus]:
    config = load_config().voice
    want_wake = config.wake_enabled if include_wake is None else include_wake
    return [
        entry
        for entry in status()
        if not entry.present and (want_wake or entry.kind != "wake word")
    ]


def total_download_mb(include_wake: bool | None = None) -> int:
    return sum(entry.approx_mb for entry in missing(include_wake))


# -- downloads -------------------------------------------------------------


def download_whisper(model_size: str) -> str:
    from faster_whisper import WhisperModel

    log.info("downloading whisper model %s", model_size)
    # Constructing the model is what fetches it. It is discarded immediately —
    # this function's job is the download, not a warm model.
    WhisperModel(
        model_size, device="cpu", compute_type="int8", download_root=str(whisper_dir())
    )
    return f"whisper-{model_size}"


def download_piper_voice(voice_id: str) -> str:
    from piper.download_voices import download_voice

    log.info("downloading piper voice %s", voice_id)
    download_voice(voice_id, piper_dir())
    return voice_id


def download_wakeword(wake_word: str) -> str:
    from openwakeword.utils import download_models

    log.info("downloading wake word models for %s", wake_word)
    target = str(wakeword_dir())
    # The feature extractors are separate from the wake phrase itself and are
    # required for any phrase, so both are fetched together.
    download_models(model_names=[wake_word], target_directory=target)
    download_models(model_names=[], target_directory=target)
    return wake_word


def ensure_all(include_wake: bool | None = None) -> dict[str, Any]:
    """Download whatever is missing. Returns a report, never raises for one failure."""
    config = load_config().voice
    want_wake = config.wake_enabled if include_wake is None else include_wake

    fetched: list[str] = []
    failed: list[dict[str, str]] = []

    if not whisper_present(config.stt_model):
        try:
            fetched.append(download_whisper(config.stt_model))
        except Exception as exc:  # noqa: BLE001
            log.exception("whisper download failed")
            failed.append({"model": f"whisper-{config.stt_model}", "error": str(exc)})

    onnx, _ = piper_voice_paths(config.voice_id)
    if not onnx.exists():
        try:
            fetched.append(download_piper_voice(config.voice_id))
        except Exception as exc:  # noqa: BLE001
            log.exception("piper download failed")
            failed.append({"model": config.voice_id, "error": str(exc)})

    if want_wake and not wakeword_present(config.wake_word):
        try:
            fetched.append(download_wakeword(config.wake_word))
        except Exception as exc:  # noqa: BLE001
            log.exception("wake word download failed")
            failed.append({"model": config.wake_word, "error": str(exc)})

    return {
        "downloaded": fetched,
        "failed": failed,
        "ready": not missing(want_wake),
        "location": str(models_root()),
    }


def remove_all() -> int:
    """Delete every downloaded model. Part of reclaiming disk, and of REQ-26."""
    root = models_root()
    size = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
    shutil.rmtree(root, ignore_errors=True)
    models_root()
    return size // (1024 * 1024)

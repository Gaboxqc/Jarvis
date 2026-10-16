"""Speech in and out, and the cloning engine — REQ-1 to REQ-4, REQ-26."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from .. import preferences
from ..settings import load_config
from . import schemas

log = logging.getLogger("kai")

router = APIRouter()


# A voice reference is seconds of speech. 50MB is already far more than one
# needs -- a minute of 24kHz 16-bit mono is under 3MB -- and the number is kept
# generous on purpose so a long or needlessly high-quality recording is not
# refused for being tidy.
MAX_REFERENCE_BYTES = 50 * 1024 * 1024

# Read in pieces rather than whole, because a size limit enforced after the
# body has been accepted is not a limit.
UPLOAD_CHUNK_BYTES = 1024 * 1024


async def _read_capped(file: UploadFile, cap: int, what: str) -> bytes:
    """Read an upload, refusing one that is over `cap`.

    This used to be `await file.read()` followed by a check on `len()`, which
    reads the entire body first and then decides whether it was allowed to.
    Starlette spools past a threshold, so the cost was disk rather than memory
    -- but it was unbounded disk, written before anything had agreed to accept
    it, and the one caller is an endpoint whose whole input is a file.

    `Content-Length` is checked first because it is free and it lets an honest
    client be turned away without sending anything. It is a claim rather than a
    fact, so the chunked read below enforces the same cap on the bytes that
    actually arrive, and stops at the first chunk that crosses it.
    """
    declared = file.size
    if declared is not None and declared > cap:
        raise HTTPException(
            status_code=413,
            detail=f"That file is too large to be {what} ({declared // (1024 * 1024)}MB).",
        )

    pieces: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > cap:
            raise HTTPException(
                status_code=413,
                detail=f"That file is too large to be {what}.",
            )
        pieces.append(chunk)
    return b"".join(pieces)


class SpeakRequest(BaseModel):
    text: str


class CloneConsentRequest(BaseModel):
    consent: bool
    enable: bool = False


XTTS_LICENCE_URL = "https://coqui.ai/cpml"
XTTS_LICENCE_SUMMARY = (
    "The cloning engine uses XTTS-v2, a model from Coqui released under the "
    "Coqui Public Model License. It permits personal and other non-commercial "
    "use, and not selling what it produces or building a paid service on it. "
    "Accepting records that you agree to those terms; declining leaves cloning "
    "off and changes nothing else."
)


class XttsLicenceRequest(BaseModel):
    accepted: bool


@router.get("/voice/status", response_model=schemas.VoiceStatus)
def voice_status() -> dict[str, Any]:
    from ..voice import session as voice_session

    return voice_session.status()


@router.get("/voice/models", response_model=schemas.VoiceModels)
def voice_models() -> dict[str, Any]:
    from ..voice import models as voice_models_module

    return {
        "models": [entry.to_dict() for entry in voice_models_module.status()],
        "missing_mb": voice_models_module.total_download_mb(),
        "location": str(voice_models_module.models_root()),
    }


@router.post("/voice/models", response_model=schemas.VoiceModelsDownloaded)
def download_voice_models(include_wake: bool = False) -> dict[str, Any]:
    """Explicit download. Hundreds of MB never move without being asked for."""
    from ..voice import models as voice_models_module

    return voice_models_module.ensure_all(include_wake=include_wake)


@router.delete("/voice/models", response_model=schemas.VoiceModelsRemoved)
def delete_voice_models() -> dict[str, Any]:
    from ..voice import models as voice_models_module

    return {"freed_mb": voice_models_module.remove_all()}


@router.post("/voice/speak", response_model=schemas.SpeechShape)
def speak(request: SpeakRequest) -> dict[str, Any]:
    """Say something out loud, and describe its shape for the avatar.

    This used to call synthesize() and return the duration -- rendering the
    audio and discarding it. The CLI and the voice loop both call tts.speak(),
    which plays; this endpoint did not, so the desktop app's "speak replies
    aloud" switch produced silence while every other path worked.

    Playback is non-blocking so the response comes back at the moment sound
    starts rather than when it finishes. That matters for the avatar: the
    envelope below is walked from the moment this returns, so a blocking call
    would put the mouth a whole sentence behind the voice.
    """
    from ..voice import tts
    from ..voice.audio import play

    config = load_config().voice
    if not config.enabled or not config.output_enabled:
        # Muting silences output and nothing else (REQ-4). Not an error.
        return {"spoke": False, "seconds": 0.0, "sample_rate": 0, "envelope": []}

    try:
        speech = tts.synthesize(request.text)
    except tts.TTSUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    shape = tts.envelope(speech)
    if speech.audio:
        try:
            play(speech.audio, speech.sample_rate, blocking=False)
        except Exception as exc:  # noqa: BLE001
            # No output device, or one that is busy. The reply is already on
            # screen, so this degrades rather than fails the turn.
            log.warning("could not play speech: %s", exc)
            return {"spoke": False, "seconds": round(speech.seconds, 2),
                    "sample_rate": speech.sample_rate, "envelope": []}

    return {
        "spoke": True,
        "seconds": round(speech.seconds, 2),
        "sample_rate": speech.sample_rate,
        # 30 values per second of speech, 0-1. Drives the avatar's mouth.
        "envelope": shape,
    }


@router.get("/voice/clone", response_model=schemas.CloneStatus)
def clone_status() -> dict[str, Any]:
    from ..voice import cloning

    return cloning.status()


@router.post("/voice/clone/consent", response_model=schemas.CloneStatus)
def clone_consent(request: CloneConsentRequest) -> dict[str, Any]:
    """Record — or withdraw — the acknowledgement that gates cloning.

    Withdrawing switches the engine back to Piper in the same call. Leaving
    `tts_engine: xtts` set while consent is false would mean the setting said
    one thing and the behaviour did another; tts.use_cloned_voice() would refuse
    anyway, but a config that lies about what it is doing is its own problem.
    """
    from ..voice import cloning

    changes: dict[str, Any] = {"clone_consent": bool(request.consent)}
    if request.consent and request.enable:
        changes["tts_engine"] = "xtts"
    elif not request.consent:
        changes["tts_engine"] = "piper"

    try:
        preferences.update({"voice": changes})
    except preferences.NotWritable as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return cloning.status()


@router.get("/voice/clone/engine", response_model=schemas.EngineProgress)
def clone_engine_progress() -> dict[str, Any]:
    from ..voice import engines

    return {"installed": engines.xtts_installed(), **engines.progress()}


@router.post("/voice/clone/engine", response_model=schemas.EngineProgress)
def install_clone_engine() -> dict[str, Any]:
    """Start fetching the cloning engine. Explicit, and never on startup.

    Returns immediately rather than holding the request open for a download
    measured in hundreds of megabytes: the settings screen polls the GET above.
    A second call while one is running is refused by the installer's own lock
    rather than starting a competing download.
    """
    import threading

    from ..voice import engines

    if engines.xtts_installed():
        return {"installed": True, **engines.progress()}

    def run() -> None:
        try:
            engines.install_xtts()
        except engines.EngineError:
            # Recorded in the progress snapshot the UI is already polling, and
            # logged by the installer. Nothing here to raise into.
            log.warning("voice engine install failed", exc_info=True)

    threading.Thread(target=run, name="xtts-install", daemon=True).start()
    return {"installed": False, **engines.progress()}


@router.delete("/voice/clone/engine", response_model=schemas.EngineRemoved)
def remove_clone_engine() -> dict[str, Any]:
    """Give the disk space back. As easy as spending it."""
    from ..voice import engines

    removed = engines.remove_xtts()
    return {"removed": removed, "installed": engines.xtts_installed()}


@router.post("/voice/clone/licence", response_model=schemas.CloneStatus)
def xtts_licence(request: XttsLicenceRequest) -> dict[str, Any]:
    """Record — or withdraw — acceptance of XTTS-v2's licence.

    Kept separate from `clone_consent` on purpose. That switch is about the
    ethics of copying somebody's voice; this is agreeing to a non-commercial
    software licence. They are different questions with different answers, and
    one switch meaning both would be an acceptance nobody was shown.

    Withdrawing clears the date, for the same reason the Live2D gate does: a
    config reading "not accepted, accepted on the 3rd" contradicts itself.
    """
    from ..voice import cloning

    changes: dict[str, Any] = {
        "xtts_licence_accepted": bool(request.accepted),
        "xtts_licence_accepted_at": (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            if request.accepted
            else ""
        ),
    }
    try:
        preferences.update({"voice": changes})
    except preferences.NotWritable as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    log.warning(
        "XTTS-v2 model licence %s",
        "accepted" if request.accepted else "withdrawn",
    )
    return cloning.status()


@router.post("/voice/clone/reference", response_model=schemas.ReferenceSaved)
async def upload_clone_reference(file: UploadFile = File(...)) -> dict[str, Any]:
    """Take the reference sample as an uploaded audio file.

    This replaced recording from the microphone. The argument for recording was
    that it put whoever is being cloned in the room -- but it is not enforcement
    (a phone held to a laptop defeats it) and it made the feature unusable for
    the ordinary case of already having a clean recording of your own voice.
    Since it bought manners rather than safety, the consent acknowledgement is
    doing the real work, and this is the more useful shape.

    Anything ffmpeg-free is accepted directly: WAV in, PCM out. Other formats
    are refused by name rather than half-decoded into noise that only reveals
    itself when the clone speaks.
    """
    from ..voice import cloning

    if not load_config().voice.clone_consent:
        raise HTTPException(
            status_code=403,
            detail="Voice cloning hasn't been acknowledged yet.",
        )

    name = (file.filename or "").lower()
    if not name.endswith(".wav"):
        raise HTTPException(
            status_code=400,
            detail=(
                "Please upload a .wav file. Most voice recorders can export one, "
                "and converting here would mean guessing at the format."
            ),
        )

    raw = await _read_capped(file, MAX_REFERENCE_BYTES, "a voice sample")

    try:
        pcm, rate = cloning.pcm_from_wav(raw)
    except cloning.CloningUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        return {**cloning.save_reference(pcm, rate), **cloning.status()}
    except cloning.CloningUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/voice/clone/reference", response_model=schemas.ReferenceForgotten)
def forget_clone_reference() -> dict[str, Any]:
    """Delete the recording. It is a sample of somebody's voice (REQ-26)."""
    from ..voice import cloning

    return {"removed": cloning.forget_reference(), **cloning.status()}


@router.post("/voice/listen", response_model=schemas.VoiceTurn)
def listen_once() -> dict[str, Any]:
    """Capture one utterance and run it as a normal turn."""
    from ..voice.session import VoiceSession

    return VoiceSession().listen_once().to_dict()

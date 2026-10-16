"""Meeting capture — REQ-19. Recorded locally, listed, each one deletable."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from . import schemas


router = APIRouter()


@router.get("/capture/status", response_model=schemas.CaptureStatus)
def capture_status() -> dict[str, Any]:
    from ..capture import session as capture

    return capture.status().to_dict()


@router.post("/capture/start", response_model=schemas.CaptureStatus)
def capture_start(label: str = "Meeting") -> dict[str, Any]:
    from ..capture import session as capture

    try:
        return capture.start(label).to_dict()
    except capture.CaptureError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/capture/stop", response_model=schemas.StoppedCapture)
def capture_stop() -> dict[str, Any]:
    from ..capture import session as capture
    from ..capture import store as capture_store
    from ..capture import summarize

    try:
        transcript = capture.stop()
    except capture.CaptureError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if transcript is None:
        raise HTTPException(status_code=500, detail="nothing was saved")

    result = summarize.summarise(transcript.text)
    capture_store.set_summary(transcript.id, result.to_dict())
    return {**transcript.to_dict(), "summary": result.to_dict()}


@router.get("/capture/transcripts", response_model=schemas.Transcripts)
def list_transcripts() -> dict[str, Any]:
    from ..capture import store as capture_store

    return {"transcripts": [t.to_dict() for t in capture_store.recent()]}


@router.get("/capture/transcripts/{transcript_id}", response_model=schemas.TranscriptDetail)
def get_transcript(transcript_id: str) -> dict[str, Any]:
    from ..capture import store as capture_store

    found = capture_store.get(transcript_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No such transcript")
    return {**found.to_dict(), "text": found.text}


@router.delete("/capture/transcripts/{transcript_id}", response_model=schemas.DeletedTranscript)
def delete_transcript(transcript_id: str) -> dict[str, Any]:
    from ..capture import store as capture_store

    removed = capture_store.delete(transcript_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="No such transcript")
    return {"deleted": removed.to_dict()}

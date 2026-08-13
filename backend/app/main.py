"""FastAPI surface — the Brain service.

Kept thin on purpose: every endpoint is a direct call into the orchestrator, the
gate, or a store. No business logic lives here, so the Tauri UI (Phase 10) and
the CLI exercise exactly the same code paths.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import focus, notifications, preferences
from .actions import gate, journal, undo
from .brain import llm, orchestrator
from .connectors import setup as connector_setup
from .index import scanner as index_scanner
from .index import store as index_store
from .memory import long_term, short_term
from .scheduler import service as scheduler
from .scheduler import store as sched_store
from .settings import load_config
from .skills.registry import catalog, load_skills
from . import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("kai")


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_skills()
    log.info("loaded %d skills", len(catalog()))
    # Without this the API process has no subscriber and a due reminder is
    # consumed with nobody told -- the reminder is lost, not merely late.
    scheduler.subscribe(notifications.on_scheduler_delivery)
    scheduler.start()
    # Reconcile the document index with disk in the background. Startup must not
    # block on it: a first scan of a large Documents folder takes minutes, and
    # the assistant is fully usable without it (REQ-16, REQ-27).
    if load_config().documents.indexed_folders:
        index_scanner.scan_in_background()
    try:
        yield
    finally:
        scheduler.stop()
        scheduler.unsubscribe(notifications.on_scheduler_delivery)
        db.close_connection()


app = FastAPI(title="Kai", version="0.1.0", lifespan=lifespan)

# The desktop UI runs from a local dev server, and inside Tauri from a
# platform-specific origin. Localhost only -- this API reaches the user's files,
# mail and calendar, so it must never accept a page served from anywhere else
# (REQ-26).
#
# `tauri.localhost` is not optional. Windows WebView2 serves the packaged app
# from http://tauri.localhost, while tauri:// is the macOS and Linux scheme.
# Allowing only the latter meant the installed Windows app was CORS-blocked from
# its own backend on every request -- it reported "backend unreachable" against a
# backend that was running and healthy.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=(
        r"^(https?://(localhost|127\.0\.0\.1|tauri\.localhost)(:\d+)?"
        r"|tauri://localhost)$"
    ),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -- models ---------------------------------------------------------------


class TurnRequest(BaseModel):
    text: str
    session_id: str = "default"
    # Present only when the client is answering a confirmation it was just given.
    # An approval without an id is not an approval (REQ-24).
    pending_action_id: str | None = None


class SkillCallRequest(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    session_id: str = "default"


# -- conversation ---------------------------------------------------------


@app.post("/turn")
def turn(request: TurnRequest) -> dict[str, Any]:
    result = orchestrator.handle_turn(
        request.text,
        request.session_id,
        pending_action_id=request.pending_action_id,
    )
    return result.to_dict()


@app.post("/turn/stream")
def turn_stream(request: TurnRequest) -> StreamingResponse:
    """The same turn, delivered as it happens (REQ-27, REQ-32).

    Server-sent events rather than a WebSocket: this is one-way, short-lived,
    and survives a dropped connection by simply ending. A socket would add a
    lifecycle to manage for no capability the turn needs.

    Every event is one `data:` line of JSON with a `type`:

        stage  - which slow phase is running, so the UI can say so
        delta  - a piece of the reply
        done   - the finished TurnResult, identical to what POST /turn returns

    `done` always arrives, including on failure, and its reply is the
    authoritative text: receipts and the ungrounded-answer guard can both revise
    what the deltas already showed, so a client renders deltas as they come and
    then settles on `done`.
    """

    def events() -> Iterator[str]:
        try:
            for event in orchestrator.run_turn(
                request.text,
                request.session_id,
                pending_action_id=request.pending_action_id,
                stream=True,
            ):
                if event["type"] == "done":
                    payload = {"type": "done", **event["result"].to_dict()}
                else:
                    payload = event
                yield f"data: {json.dumps(payload)}\n\n"
        except Exception as exc:  # noqa: BLE001
            # The stream has already returned 200, so an exception here cannot
            # become an HTTP error -- it would just stop mid-reply and look like
            # the model went quiet. Send a `done` the client can render.
            log.exception("streaming turn failed")
            failed = {
                "type": "done",
                "reply": "Something went wrong while answering. Nothing was left half-done.",
                "needs_confirmation": False,
                "pending": None,
                "skill_calls": [],
                "error": str(exc),
            }
            yield f"data: {json.dumps(failed)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        # Without this a proxy or the WebView can hold the whole response back
        # to buffer it, which produces exactly the all-at-once delivery
        # streaming exists to avoid.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class AccountRequest(BaseModel):
    kind: str                       # "mail" | "calendar"
    provider: str                   # "imap" | "caldav"
    fields: dict[str, Any] = Field(default_factory=dict)


@app.post("/connectors/accounts")
def add_account(request: AccountRequest) -> dict[str, Any]:
    """Add an account without hand-editing YAML (REQ-13, REQ-26).

    Details only. No password crosses this boundary — `fields` is rejected
    outright if it carries anything that looks like one, and the response says
    to run `/connect`, which prompts at the terminal and writes to the OS
    credential store.
    """
    try:
        return connector_setup.add_account(request.kind, request.provider, request.fields)
    except connector_setup.SetupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/connectors/accounts/{kind}/{label}")
def remove_account(kind: str, label: str) -> dict[str, Any]:
    try:
        return connector_setup.remove_account(kind, label)
    except connector_setup.SetupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/session/{session_id}")
def clear_session(session_id: str) -> dict[str, Any]:
    return {"cleared_turns": short_term.clear(session_id)}


# -- actions (REQ-24, REQ-25) ---------------------------------------------


@app.get("/actions/pending")
def list_pending() -> dict[str, Any]:
    return {
        "pending": [
            {
                "action_id": r.id,
                "skill": r.skill_name,
                "preview": r.preview,
                "reversible": r.reversible,
                "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            }
            for r in journal.pending()
        ]
    }


@app.post("/actions/{action_id}/confirm")
def confirm_action(action_id: str, session_id: str = "default") -> dict[str, Any]:
    return orchestrator.confirm_pending(action_id, session_id).to_dict()


@app.post("/actions/{action_id}/decline")
def decline_action(action_id: str, session_id: str = "default") -> dict[str, Any]:
    return orchestrator.decline_pending(action_id, session_id).to_dict()


@app.get("/actions/history")
def action_history(limit: int = 25) -> dict[str, Any]:
    return {
        "history": [
            {
                "id": r.id,
                "batch_id": r.batch_id,
                "skill": r.skill_name,
                "preview": r.preview,
                "severity": r.severity,
                "status": r.status,
                "can_undo": r.can_undo,
                "executed_at": r.executed_at.isoformat() if r.executed_at else None,
                "error": r.error,
            }
            for r in journal.history(limit=limit)
        ]
    }


@app.post("/actions/undo")
def undo_last() -> dict[str, Any]:
    return undo.undo_last().to_dict()


@app.post("/actions/{action_id}/undo")
def undo_action(action_id: str) -> dict[str, Any]:
    return undo.undo_action(action_id).to_dict()


@app.get("/actions/pre-approvals")
def list_pre_approvals() -> dict[str, Any]:
    return {"pre_approvals": gate.list_pre_approvals()}


@app.post("/actions/pre-approvals/{skill_name}")
def grant_pre_approval(skill_name: str) -> dict[str, Any]:
    gate.grant_pre_approval(skill_name)
    return {"granted": skill_name, "pre_approvals": gate.list_pre_approvals()}


@app.delete("/actions/pre-approvals/{skill_name}")
def revoke_pre_approval(skill_name: str) -> dict[str, Any]:
    gate.revoke_pre_approval(skill_name)
    return {"revoked": skill_name, "pre_approvals": gate.list_pre_approvals()}


# -- skills ---------------------------------------------------------------


@app.get("/skills")
def list_skills() -> dict[str, Any]:
    return {"skills": catalog()}


@app.post("/skills/run")
def run_skill(request: SkillCallRequest) -> dict[str, Any]:
    """Direct invocation, still through the gate — there is no bypass."""
    from .skills.base import SkillContext

    outcome = gate.submit(
        request.name,
        request.args,
        SkillContext(session_id=request.session_id, config=load_config()),
    )
    return outcome.to_dict()


# -- memory (REQ-7) -------------------------------------------------------


@app.get("/memory")
def list_memory() -> dict[str, Any]:
    return {"facts": [f.to_dict() for f in long_term.all_facts()]}


@app.delete("/memory/{fact_id}")
def delete_memory(fact_id: str) -> dict[str, Any]:
    fact = long_term.delete(fact_id)
    if fact is None:
        raise HTTPException(status_code=404, detail="No such memory")
    return {"deleted": fact.to_dict()}


@app.delete("/memory")
def delete_all_memory() -> dict[str, Any]:
    return {"deleted": long_term.delete_all()}


# -- scheduling (REQ-9) ---------------------------------------------------


@app.get("/reminders")
def list_reminders() -> dict[str, Any]:
    return {"reminders": [i.to_dict() for i in sched_store.active_items()]}


@app.post("/reminders/tick")
def force_tick() -> dict[str, Any]:
    """Deliver anything due right now. Used by tests and by the UI on wake."""
    return {"delivered": [d.message() for d in scheduler.tick()]}


# -- privacy & health (REQ-26, REQ-27) ------------------------------------


# -- documents (REQ-16) ---------------------------------------------------


@app.get("/documents/status")
def document_index_status() -> dict[str, Any]:
    return {**index_scanner.status(), "failures": index_store.failures()}


@app.get("/documents")
def list_documents() -> dict[str, Any]:
    return {"documents": index_store.documents()}


@app.post("/documents/reindex")
def reindex_documents(force: bool = True) -> dict[str, Any]:
    return index_scanner.scan(force=force).to_dict()


@app.get("/documents/search")
def search_documents(q: str, limit: int = 5) -> dict[str, Any]:
    return {"results": [hit.to_dict() for hit in index_store.search(q, limit=limit)]}


@app.delete("/documents/index")
def clear_document_index() -> dict[str, Any]:
    """REQ-16/REQ-26 — the user can see what is indexed and remove all of it."""
    return {"cleared_documents": index_store.clear()}


# -- connectors (REQ-8, REQ-13, REQ-26) -----------------------------------


@app.get("/connectors")
def connector_status() -> dict[str, Any]:
    """Account details only. Never returns a secret, by construction."""
    from .connectors import base as connectors

    return connectors.status()


class CredentialRequest(BaseModel):
    # Never logged, never echoed back, never written to the config file. It goes
    # from this field into the OS credential store and nowhere else.
    secret: str


@app.put("/connectors/{kind}/{label}/credential")
def set_credential(kind: str, label: str, request: CredentialRequest) -> dict[str, Any]:
    """Store the password (or an iCal URL) for an account — REQ-26.

    This used to be terminal-only, on the reasoning that a secret typed into a
    form travels through a request body and a validation layer before it reaches
    anywhere safe, whereas one typed at a getpass prompt goes straight from the
    keyboard to the OS store. That reasoning is sound and was still the wrong
    call: an assistant whose accounts can only be set up by running commands in
    a terminal is not configurable by the people it is for, and "secure but
    unused" is not secure.

    So the secret crosses one loopback hop, and everything else is held tight
    around it. The API binds 127.0.0.1 only and CORS is an allow-list, so no
    page on the internet can reach this. The value is not logged here or in
    credentials.store, is never returned by any endpoint, and never touches
    kai.config.yaml. What lands on disk is a reference.
    """
    from .connectors import base as connectors
    from .connectors import credentials

    try:
        account = connectors.find(kind, label)
    except connectors.ConnectorError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    secret = (request.secret or "").strip()
    if not secret:
        raise HTTPException(status_code=400, detail="An empty password can't be stored.")

    try:
        credentials.store(account.credential_ref, secret)
    except credentials.CredentialError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # Report the reference, never the value.
    return {"stored": True, "kind": kind, "label": label, "credential_ref": account.credential_ref}


@app.post("/connectors/{kind}/{label}/check")
def check_connector(kind: str, label: str) -> dict[str, Any]:
    """Try the account and say whether it works.

    Without this, a wrong password is discovered the next time the user asks
    about their mail, as an error about something they were not doing.
    """
    from .connectors import base as connectors

    try:
        return connectors.check(kind, label)
    except connectors.ConnectorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/connectors/{kind}/{label}/credential")
def forget_credential(kind: str, label: str) -> dict[str, Any]:
    from .connectors import credentials

    return {"removed": credentials.delete(credentials.reference(kind, label))}


@app.get("/briefing")
def briefing() -> dict[str, Any]:
    from .skills.planning.briefing import build

    return {
        "sections": [
            {"name": s.name, "lines": s.lines, "error": s.error} for s in build()
        ]
    }


# -- notifications (REQ-9) ------------------------------------------------


@app.get("/notifications")
def take_notifications() -> dict[str, Any]:
    """Drain queued notifications. Destructive: each is handed out once."""
    return {"notifications": [n.to_dict() for n in notifications.drain()]}


# -- settings (REQ-5, REQ-26) ---------------------------------------------


class SettingsPatch(BaseModel):
    changes: dict[str, dict[str, Any]] = Field(default_factory=dict)


@app.get("/settings")
def read_settings() -> dict[str, Any]:
    return {"current": preferences.current(), "writable": preferences.writable_keys()}


@app.patch("/settings")
def write_settings(patch: SettingsPatch) -> dict[str, Any]:
    """Change a local preference.

    Deliberately narrow. Anything governing what leaves this machine or what the
    assistant may touch -- privacy switches, connectors, allowed file roots,
    indexed folders -- is refused here and stays editable only by opening
    kai.config.yaml (REQ-26).
    """
    try:
        return preferences.update(patch.changes)
    except preferences.NotWritable as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


# -- voice (REQ-1 to REQ-4) -----------------------------------------------


@app.get("/voice/status")
def voice_status() -> dict[str, Any]:
    from .voice import session as voice_session

    return voice_session.status()


@app.get("/voice/models")
def voice_models() -> dict[str, Any]:
    from .voice import models as voice_models_module

    return {
        "models": [entry.to_dict() for entry in voice_models_module.status()],
        "missing_mb": voice_models_module.total_download_mb(),
        "location": str(voice_models_module.models_root()),
    }


@app.post("/voice/models")
def download_voice_models(include_wake: bool = False) -> dict[str, Any]:
    """Explicit download. Hundreds of MB never move without being asked for."""
    from .voice import models as voice_models_module

    return voice_models_module.ensure_all(include_wake=include_wake)


@app.delete("/voice/models")
def delete_voice_models() -> dict[str, Any]:
    from .voice import models as voice_models_module

    return {"freed_mb": voice_models_module.remove_all()}


class SpeakRequest(BaseModel):
    text: str


@app.post("/voice/speak")
def speak(request: SpeakRequest) -> dict[str, Any]:
    from .voice import tts

    try:
        speech = tts.synthesize(request.text)
    except tts.TTSUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"seconds": round(speech.seconds, 2), "sample_rate": speech.sample_rate}


@app.post("/voice/listen")
def listen_once() -> dict[str, Any]:
    """Capture one utterance and run it as a normal turn."""
    from .voice.session import VoiceSession

    return VoiceSession().listen_once().to_dict()


# -- meeting capture (REQ-19) ---------------------------------------------


@app.get("/capture/status")
def capture_status() -> dict[str, Any]:
    from .capture import session as capture

    return capture.status().to_dict()


@app.post("/capture/start")
def capture_start(label: str = "Meeting") -> dict[str, Any]:
    from .capture import session as capture

    try:
        return capture.start(label).to_dict()
    except capture.CaptureError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/capture/stop")
def capture_stop() -> dict[str, Any]:
    from .capture import session as capture
    from .capture import store as capture_store
    from .capture import summarize

    try:
        transcript = capture.stop()
    except capture.CaptureError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if transcript is None:
        raise HTTPException(status_code=500, detail="nothing was saved")

    result = summarize.summarise(transcript.text)
    capture_store.set_summary(transcript.id, result.to_dict())
    return {**transcript.to_dict(), "summary": result.to_dict()}


@app.get("/capture/transcripts")
def list_transcripts() -> dict[str, Any]:
    from .capture import store as capture_store

    return {"transcripts": [t.to_dict() for t in capture_store.recent()]}


@app.get("/capture/transcripts/{transcript_id}")
def get_transcript(transcript_id: str) -> dict[str, Any]:
    from .capture import store as capture_store

    found = capture_store.get(transcript_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No such transcript")
    return {**found.to_dict(), "text": found.text}


@app.delete("/capture/transcripts/{transcript_id}")
def delete_transcript(transcript_id: str) -> dict[str, Any]:
    from .capture import store as capture_store

    removed = capture_store.delete(transcript_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="No such transcript")
    return {"deleted": removed.to_dict()}


# -- focus (REQ-23) -------------------------------------------------------


@app.get("/focus")
def focus_status() -> dict[str, Any]:
    state = focus.state()
    return {"active": state.active, "minutes_left": state.minutes_left,
            "closed_apps": list(state.closed_apps)}


@app.post("/focus/end")
def end_focus() -> dict[str, Any]:
    state = focus.end()
    return {"active": state.active}


@app.get("/state")
def presence_state() -> dict[str, Any]:
    """What the presence indicator shows — REQ-32.

    Deliberately the whole contract: a state name, plus an optional emotion tag.
    The UI is given nothing about the brain, the skills or the actions, which is
    what keeps the presentation layer replaceable.
    """
    from .capture import session as capture
    from .voice import stt, tts

    if capture.is_recording():
        state = "recording"
    elif stt.is_loaded() or tts.is_loaded():
        state = "listening"
    else:
        state = "idle"

    return {
        "state": state,
        "emotion": None,
        "recording": capture.is_recording(),
        "focus": focus.is_active(),
    }


@app.get("/health")
def health() -> dict[str, Any]:
    config = load_config()
    skill_count = len(catalog())
    # A packaged build that lost its dynamically-discovered skills still serves
    # every endpoint and answers questions, so nothing else would reveal it.
    # Reporting ok=True there would make the one signal anyone checks a lie.
    return {
        "ok": skill_count > 0,
        "problem": None if skill_count else "No skills loaded - this build is broken.",
        "brain": llm.health(),
        "skills": skill_count,
        "persona": config.persona.name,
        "config_file": str(config.source_path) if config.source_path else None,
        "data_dir": str(db.db_path().parent),
        "privacy": {
            "web_search": config.privacy.allow_web_search,
            "live_data": config.privacy.allow_live_data,
            "cloud_llm": config.privacy.allow_cloud_llm,
        },
    }


@app.post("/privacy/wipe")
def wipe() -> dict[str, Any]:
    """REQ-26 — the single delete-everything action."""
    return {"removed": db.wipe_all_local_data()}

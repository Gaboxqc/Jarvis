"""FastAPI surface — the Brain service.

Kept thin on purpose: every endpoint is a direct call into the orchestrator, the
gate, or a store. No business logic lives here, so the Tauri UI (Phase 10) and
the CLI exercise exactly the same code paths.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .actions import gate, journal, undo
from .brain import llm, orchestrator
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
    scheduler.start()
    try:
        yield
    finally:
        scheduler.stop()
        db.close_connection()


app = FastAPI(title="Kai", version="0.1.0", lifespan=lifespan)


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


@app.get("/health")
def health() -> dict[str, Any]:
    config = load_config()
    return {
        "ok": True,
        "brain": llm.health(),
        "skills": len(catalog()),
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

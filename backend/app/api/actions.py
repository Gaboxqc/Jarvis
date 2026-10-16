"""The Action Gate's HTTP surface — REQ-24, REQ-25.

Skills live here too. `POST /skills/run` is a direct invocation that still
goes through the gate, which makes it a statement about the gate rather than
about the registry.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..actions import gate, journal, undo
from ..brain import orchestrator
from ..settings import load_config
from ..skills.registry import catalog
from . import schemas

from .params import Limit

router = APIRouter()


class SkillCallRequest(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    session_id: str = "default"


@router.get("/actions/pending", response_model=schemas.PendingActions)
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


@router.post("/actions/{action_id}/confirm", response_model=schemas.TurnResult)
def confirm_action(action_id: str, session_id: str = "default") -> dict[str, Any]:
    return orchestrator.confirm_pending(action_id, session_id).to_dict()


@router.post("/actions/{action_id}/decline", response_model=schemas.TurnResult)
def decline_action(action_id: str, session_id: str = "default") -> dict[str, Any]:
    return orchestrator.decline_pending(action_id, session_id).to_dict()


@router.get("/actions/history", response_model=schemas.ActionHistory)
def action_history(limit: Limit = 25) -> dict[str, Any]:
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


@router.post("/actions/undo", response_model=schemas.UndoOutcome)
def undo_last() -> dict[str, Any]:
    return undo.undo_last().to_dict()


@router.post("/actions/{action_id}/undo", response_model=schemas.UndoOutcome)
def undo_action(action_id: str) -> dict[str, Any]:
    return undo.undo_action(action_id).to_dict()


@router.get("/actions/pre-approvals", response_model=schemas.PreApprovals)
def list_pre_approvals() -> dict[str, Any]:
    return {"pre_approvals": gate.list_pre_approvals()}


@router.post("/actions/pre-approvals/{skill_name}", response_model=schemas.PreApprovalGranted)
def grant_pre_approval(skill_name: str) -> dict[str, Any]:
    gate.grant_pre_approval(skill_name)
    return {"granted": skill_name, "pre_approvals": gate.list_pre_approvals()}


@router.delete("/actions/pre-approvals/{skill_name}", response_model=schemas.PreApprovalRevoked)
def revoke_pre_approval(skill_name: str) -> dict[str, Any]:
    gate.revoke_pre_approval(skill_name)
    return {"revoked": skill_name, "pre_approvals": gate.list_pre_approvals()}


@router.get("/skills", response_model=schemas.SkillCatalog)
def list_skills() -> dict[str, Any]:
    return {"skills": catalog()}


@router.post("/skills/run", response_model=schemas.SkillCall)
def run_skill(request: SkillCallRequest) -> dict[str, Any]:
    """Direct invocation, still through the gate — there is no bypass."""
    from ..skills.base import SkillContext

    outcome = gate.submit(
        request.name,
        request.args,
        SkillContext(session_id=request.session_id, config=load_config()),
    )
    return outcome.to_dict()

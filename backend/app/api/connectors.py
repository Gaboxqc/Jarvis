"""Mail and calendar accounts — REQ-8, REQ-13, REQ-26.

No endpoint here ever returns a secret. `fields` on an account is rejected
outright if it carries anything that looks like one, and a credential goes
from its request body into the OS store and nowhere else.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..connectors import setup as connector_setup
from . import schemas

router = APIRouter()


class AccountRequest(BaseModel):
    kind: str                       # "mail" | "calendar"
    provider: str                   # "imap" | "caldav"
    fields: dict[str, Any] = Field(default_factory=dict)


@router.post("/connectors/accounts", response_model=schemas.AddedAccount)
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


@router.delete("/connectors/accounts/{kind}/{label}", response_model=schemas.RemovedAccount)
def remove_account(kind: str, label: str) -> dict[str, Any]:
    try:
        return connector_setup.remove_account(kind, label)
    except connector_setup.SetupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/connectors", response_model=schemas.Connectors)
def connector_status() -> dict[str, Any]:
    """Account details only. Never returns a secret, by construction."""
    from ..connectors import base as connectors

    return connectors.status()


class CredentialRequest(BaseModel):
    # Never logged, never echoed back, never written to the config file. It goes
    # from this field into the OS credential store and nowhere else.
    secret: str


@router.put("/connectors/{kind}/{label}/credential", response_model=schemas.CredentialStored)
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
    from ..connectors import base as connectors
    from ..connectors import credentials

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


@router.post("/connectors/{kind}/{label}/check", response_model=schemas.AccountCheck)
def check_connector(kind: str, label: str) -> dict[str, Any]:
    """Try the account and say whether it works.

    Without this, a wrong password is discovered the next time the user asks
    about their mail, as an error about something they were not doing.
    """
    from ..connectors import base as connectors

    try:
        return connectors.check(kind, label)
    except connectors.ConnectorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/connectors/{kind}/{label}/credential", response_model=schemas.CredentialForgotten)
def forget_credential(kind: str, label: str) -> dict[str, Any]:
    from ..connectors import credentials

    return {"removed": credentials.delete(credentials.reference(kind, label))}


@router.get("/briefing", response_model=schemas.Briefing)
def briefing() -> dict[str, Any]:
    from ..skills.planning.briefing import build

    return {
        "sections": [
            {"name": s.name, "lines": s.lines, "error": s.error, "configured": s.configured}
            for s in build()
        ]
    }

"""Settings, retention, diagnostics and the wipe — REQ-5, REQ-26, REQ-27."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db
from .. import diagnostics
from .. import preferences
from .. import retention

log = logging.getLogger("kai")

router = APIRouter()


class SettingsPatch(BaseModel):
    changes: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ClientProblem(BaseModel):
    """Something that went wrong in the desktop shell, not the backend."""

    context: str
    name: str = ""
    message: str = ""
    detail: str = ""


@router.get("/settings")
def read_settings() -> dict[str, Any]:
    return {"current": preferences.current(), "writable": preferences.writable_keys()}


@router.patch("/settings")
def write_settings(patch: SettingsPatch) -> dict[str, Any]:
    """Change a setting (REQ-26).

    What may be changed is `preferences.WRITABLE` and nothing else — a section
    or key absent from it is a 403 whatever the caller asks for. `connectors.*`
    is absent because those carry secrets and go through
    /connectors/accounts, and `brain.ollama_host` is absent because redirecting
    the model host would send every prompt somewhere else.

    Egress switches and folder lists *are* writable, which they were not
    before. Folder lists are validated against the filesystem, and every egress
    change is logged with its previous value, so widening what the assistant can
    reach leaves a record even though it no longer needs a text editor.
    """
    try:
        return preferences.update(patch.changes)
    except preferences.NotWritable as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/privacy/retention")
def retention_status() -> dict[str, Any]:
    """The window, and what is inside it right now — REQ-26."""
    return retention.describe()


@router.post("/privacy/retention/sweep")
def retention_sweep() -> dict[str, Any]:
    """Apply the window now rather than waiting for the next start or stop.

    Here because shortening the window should visibly do something. Saving "30
    days" and being told nothing happened until the next restart reads as a
    setting that did not take.
    """
    return {"removed": retention.sweep()}


@router.get("/diagnostics/logs")
def diagnostic_logs() -> dict[str, Any]:
    """Where the log is and how big it is — REQ-27.

    Names and sizes, never contents. A log is a file to attach to a bug report,
    not something to render in a chat window, and serving the text would put a
    second copy somewhere it was not already.
    """
    return diagnostics.summary()


@router.post("/diagnostics/client-problem")
def client_problem(problem: ClientProblem) -> dict[str, bool]:
    """Record a failure the frontend hit — REQ-27.

    The window can reach things the backend cannot: the updater, the WebView,
    the Live2D runtime. When one of those fails, the only account of it was a
    sentence in a card that disappears when the user clicks elsewhere, and the
    console it was also written to needs devtools that a packaged build does
    not ship.

    That is not a hypothetical gap. An update failed on a real install and the
    whole investigation came down to asking the user to read the message back,
    because nothing had kept it. A failure nobody can retrieve ten minutes
    later cannot be diagnosed, only guessed at.

    Logged, never stored in the database: it is a line in the same file
    /diagnostics/logs points at, and it goes through the same Redactor as
    everything else there.
    """
    log.warning(
        "client problem [%s] %s: %s%s",
        problem.context,
        problem.name or "Error",
        problem.message,
        f" | {problem.detail}" if problem.detail else "",
    )
    return {"ok": True}


@router.post("/privacy/wipe")
def wipe() -> dict[str, Any]:
    """REQ-26 — the single delete-everything action."""
    removed = db.wipe_all_local_data()
    # The logs are local data too. Leaving them behind after "delete everything"
    # would leave file paths and account labels on disk under a button that says
    # it removed them.
    removed["logs"] = diagnostics.wipe_logs()
    return {"removed": removed}

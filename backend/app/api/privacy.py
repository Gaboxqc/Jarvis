"""Settings, retention, diagnostics and the wipe — REQ-5, REQ-26, REQ-27."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import db
from .. import diagnostics
from .. import preferences
from .. import privacy_export
from .. import retention
from ..settings import data_dir
from . import schemas

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


@router.get("/settings", response_model=schemas.Settings)
def read_settings() -> dict[str, Any]:
    return {"current": preferences.current(), "writable": preferences.writable_keys()}


@router.patch("/settings", response_model=schemas.SettingsUpdated)
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


@router.get("/privacy/retention", response_model=schemas.RetentionWindow)
def retention_status() -> dict[str, Any]:
    """The window, and what is inside it right now — REQ-26."""
    return retention.describe()


@router.post("/privacy/retention/sweep", response_model=schemas.RetentionSwept)
def retention_sweep() -> dict[str, Any]:
    """Apply the window now rather than waiting for the next start or stop.

    Here because shortening the window should visibly do something. Saving "30
    days" and being told nothing happened until the next restart reads as a
    setting that did not take.
    """
    return {"removed": retention.sweep()}


@router.get("/diagnostics/logs", response_model=schemas.LogSummary)
def diagnostic_logs() -> dict[str, Any]:
    """Where the log is and how big it is — REQ-27.

    Names and sizes, never contents. A log is a file to attach to a bug report,
    not something to render in a chat window, and serving the text would put a
    second copy somewhere it was not already.
    """
    return diagnostics.summary()


@router.post("/diagnostics/client-problem", response_model=schemas.Acknowledged)
def client_problem(problem: ClientProblem) -> dict[str, Any]:
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


@router.post("/privacy/wipe", response_model=schemas.Wiped)
def wipe() -> dict[str, Any]:
    """REQ-26 — the single delete-everything action."""
    removed = db.wipe_all_local_data()
    # The logs are local data too. Leaving them behind after "delete everything"
    # would leave file paths and account labels on disk under a button that says
    # it removed them.
    removed["logs"] = diagnostics.wipe_logs()
    return {"removed": removed}


# -- taking it with you (REQ-26) ------------------------------------------


@router.get("/privacy/export/preview", response_model=schemas.ExportPreview)
def export_preview() -> dict[str, Any]:
    """What an export would hold, without building one.

    So the button can say "4 facts, 112 turns, 3 meetings" before it is
    pressed. Offering a download of an unknown thing is how people end up not
    pressing it.
    """
    return privacy_export.describe()


@router.post("/privacy/export")
def export_everything(background: BackgroundTasks) -> FileResponse:
    """Everything the database holds about you, as one zip — REQ-26.

    The counterpart to /privacy/wipe, and the reason it needed one: until now
    the only control over the record was destroying it. REQ-26 asks for local
    data *control*, and a switch that only points one way is not a dial.

    No secrets, by construction: `credentials.fetch` is never called, and the
    connectors section is removed from the exported config rather than emptied,
    so there is no field left to wonder about. app/privacy_export.py lists what
    else is left out and why.

    Written to a file and streamed rather than assembled in memory. Transcripts
    are the one table with no bound on their size, so an endpoint that holds
    the whole export in RAM to hand it over is one that fails on exactly the
    install that most wants it.

    The temporary file is deleted after the response is sent, which is what
    BackgroundTasks is for -- deleting it before would race the download, and
    leaving it would put a second copy of everything in the data directory
    under a button about data control.
    """
    target = data_dir() / "export" / privacy_export.suggested_filename()
    try:
        privacy_export.write_export(target)
    except OSError as exc:
        raise HTTPException(
            status_code=507,
            detail=f"Could not write the export: {exc}",
        ) from exc

    background.add_task(_discard, target)
    log.info("exported local data to %s (%d bytes)", target.name, target.stat().st_size)
    return FileResponse(
        target,
        media_type="application/zip",
        filename=target.name,
        background=background,
    )


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
        # Tidy the folder too, so a data directory does not accumulate an
        # empty `export/` nobody put there.
        path.parent.rmdir()
    except OSError:
        # A file we cannot remove is not worth failing a finished download
        # over; the next export overwrites nothing and the folder is one entry.
        log.debug("could not clean up %s", path, exc_info=True)

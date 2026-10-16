"""Focus, presence and health — REQ-23, REQ-27, REQ-31, REQ-32.

`/events` is the one stream that replaced three pollers; `/state` and
`/notifications` stay beside it because an endpoint that answers one question
once is the right shape for a script.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import __version__
from .. import db
from .. import events
from .. import focus
from .. import notifications
from ..brain import llm
from ..settings import load_config
from ..skills.registry import catalog

router = APIRouter()


class FocusRequest(BaseModel):
    minutes: int = 25


@router.get("/focus")
def focus_status() -> dict[str, Any]:
    """Focus state, plus what starting one would cost right now.

    `would_close` is the whole reason this is not just a button. Starting a
    session terminates the configured distracting apps, and unsaved work in them
    is gone. The skill decides its severity the same way -- consequential only
    when something is actually running -- so the UI can name the apps before
    asking instead of warning about a risk that does not exist today.
    """
    from ..skills.system import focus as focus_skills

    state = focus.state()
    return {"active": state.active, "minutes_left": state.minutes_left,
            "closed_apps": list(state.closed_apps),
            "would_close": focus_skills.would_close()}


@router.post("/focus/start")
def start_focus(request: FocusRequest) -> dict[str, Any]:
    from ..skills.base import SkillContext, SkillError
    from ..skills.system.focus import StartFocusSkill

    try:
        # Through the skill, not around it: it is the only place that knows how
        # to close the apps, hold reminders and pause indexing together, and a
        # second implementation would drift from it.
        result = StartFocusSkill().run({"minutes": request.minutes}, SkillContext())
    except SkillError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    state = focus.state()
    return {"active": state.active, "minutes_left": state.minutes_left,
            "closed_apps": list(state.closed_apps), "message": result.message}


@router.post("/focus/end")
def end_focus() -> dict[str, Any]:
    state = focus.end()
    return {"active": state.active}


@router.get("/events")
async def event_stream() -> StreamingResponse:
    """Everything the shell used to poll for, pushed instead — REQ-31, REQ-32.

    Presence, notifications and brain health arrived through three timers
    running for the life of the window: roughly 48 requests a minute with the
    app idle, to establish forty-seven times out of forty-eight that nothing had
    changed. See app/events.py for what is sampled and how often.

    /state and /notifications stay. The CLI uses them, they are what this is
    tested against, and an endpoint that answers one question once is the right
    shape for a script.
    """
    return StreamingResponse(
        events.stream(),
        media_type="text/event-stream",
        # Same reasoning as /turn/stream: without these a proxy or the WebView
        # holds the response back to buffer it, which turns a live stream into
        # a very slow poll.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/state")
def presence_state() -> dict[str, Any]:
    """What the presence indicator shows — REQ-32.

    Deliberately the whole contract: a state name, plus an optional emotion tag.
    The UI is given nothing about the brain, the skills or the actions, which is
    what keeps the presentation layer replaceable.
    """
    from ..capture import session as capture
    from ..voice import stt, tts

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


@router.get("/notifications")
def take_notifications() -> dict[str, Any]:
    """Drain queued notifications. Destructive: each is handed out once."""
    return {"notifications": [n.to_dict() for n in notifications.drain()]}


@router.get("/health")
def health() -> dict[str, Any]:
    config = load_config()
    skill_count = len(catalog())
    # A packaged build that lost its dynamically-discovered skills still serves
    # every endpoint and answers questions, so nothing else would reveal it.
    # Reporting ok=True there would make the one signal anyone checks a lie.
    return {
        "ok": skill_count > 0,
        # Which backend is actually running. After an update that is the first
        # thing worth knowing, and for a long time this endpoint could not say:
        # it reported the FastAPI app's hardcoded 0.1.0, or nothing at all.
        "version": __version__,
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

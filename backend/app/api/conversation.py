"""The turn — REQ-1 to REQ-7, REQ-27, REQ-32."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..brain import orchestrator
from ..memory import short_term

log = logging.getLogger("kai")

router = APIRouter()


class TurnRequest(BaseModel):
    text: str
    session_id: str = "default"
    # Present only when the client is answering a confirmation it was just given.
    # An approval without an id is not an approval (REQ-24).
    pending_action_id: str | None = None


@router.post("/turn")
def turn(request: TurnRequest) -> dict[str, Any]:
    result = orchestrator.handle_turn(
        request.text,
        request.session_id,
        pending_action_id=request.pending_action_id,
    )
    return result.to_dict()


@router.post("/turn/stream")
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

    def frames() -> Iterator[str]:
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
        frames(),
        media_type="text/event-stream",
        # Without this a proxy or the WebView can hold the whole response back
        # to buffer it, which produces exactly the all-at-once delivery
        # streaming exists to avoid.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/session/{session_id}")
def clear_session(session_id: str) -> dict[str, Any]:
    return {"cleared_turns": short_term.clear(session_id)}

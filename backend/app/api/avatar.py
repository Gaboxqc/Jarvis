"""The avatar's runtime licence — REQ-32.

Its own module rather than part of voice.py: this is a licence for a drawing
runtime compiled into the desktop binary, and the only thing it shares with
the speech endpoints is that both ask before using something that is not ours
to grant.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import preferences
from ..settings import load_config

log = logging.getLogger("kai")

router = APIRouter()


LIVE2D_LICENCE_URL = "https://www.live2d.com/en/sdk/license/"
LIVE2D_LICENCE_SUMMARY = (
    "The avatar is drawn by Live2D Cubism Core, a proprietary runtime from "
    "Live2D Inc. It is bundled with this app but licensed separately, under "
    "Live2D's Proprietary Software Licence. Accepting records that you agree "
    "to those terms; declining leaves the avatar off and changes nothing else."
)


class AvatarLicenceRequest(BaseModel):
    accepted: bool


def _avatar_state() -> dict[str, Any]:
    avatar = load_config().avatar
    return {
        "licence_accepted": avatar.licence_accepted,
        "licence_accepted_at": avatar.licence_accepted_at,
        "licence_summary": LIVE2D_LICENCE_SUMMARY,
        "licence_url": LIVE2D_LICENCE_URL,
    }


@router.get("/avatar")
def avatar_status() -> dict[str, Any]:
    """Whether the avatar's runtime may load.

    Says nothing about whether the Core file is actually present: it is a
    front-end asset compiled into the desktop binary, which this process cannot
    see. The window checks for it directly and reports that itself.
    """
    return _avatar_state()


@router.post("/avatar/licence")
def avatar_licence(request: AvatarLicenceRequest) -> dict[str, Any]:
    """Record — or withdraw — acceptance of the Live2D runtime licence.

    Stamped with the date so the record can be shown back rather than asserted.
    Withdrawing clears the date too: keeping "accepted on the 3rd" next to
    "accepted: false" is a config that contradicts itself.

    Logged at WARNING like the egress switches. This is not a privacy decision,
    but it is a legal one made on this machine, and the point of writing it down
    is that it can be found later.
    """
    changes: dict[str, Any] = {
        "licence_accepted": bool(request.accepted),
        "licence_accepted_at": (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            if request.accepted
            else ""
        ),
    }
    try:
        preferences.update({"avatar": changes})
    except preferences.NotWritable as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    log.warning(
        "Live2D runtime licence %s",
        "accepted" if request.accepted else "withdrawn",
    )
    return _avatar_state()

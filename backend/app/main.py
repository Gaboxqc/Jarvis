"""The app itself: its lifetime, its front door, and the routers.

Kept thin on purpose -- every endpoint is a direct call into the orchestrator,
the gate, or a store, so the Tauri UI and the CLI exercise exactly the same
code paths. For a long time "thin" was true of each endpoint and not of this
file, which grew to 1,413 lines and seventy of them. The endpoints now live in
app/api/, one module per area, and what is left here is the three things that
are genuinely about the application rather than about one area of it: when it
starts and stops, who is allowed to reach it, and which routers it carries.

The middleware order below is the part worth reading twice.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers

from . import __version__, db, notifications, retention, security
from .api import ROUTERS
from .index import scanner as index_scanner
from .scheduler import service as scheduler
from .settings import load_config
from .skills.registry import catalog, load_skills

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("kai")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # First, and before the port is serving. The desktop shell and the Vite dev
    # server both read this file, and both read it once at their own startup --
    # so creating it lazily on the first request means whichever of them started
    # first found nothing and spent the whole session unauthenticated. Doing it
    # here rather than in server.py covers `uvicorn app.main:app` too, which is
    # how the README says to run it in development.
    security.token()
    load_skills()
    log.info("loaded %d skills", len(catalog()))
    # Swept at both ends of the process. Startup catches the machine that was
    # off for a month; shutdown catches the session that has just run for a
    # week. Neither is enough on its own, and together they mean the window is
    # honoured without a timer that has to be right.
    retention.sweep()
    # Without this the API process has no subscriber and a due reminder is
    # consumed with nobody told -- the reminder is lost, not merely late.
    scheduler.subscribe(notifications.on_scheduler_delivery)
    scheduler.start()
    # Reconcile the document index with disk in the background. Startup must not
    # block on it: a first scan of a large Documents folder takes minutes, and
    # the assistant is fully usable without it (REQ-16, REQ-27).
    #
    # Suppressed under test. Every TestClient runs this lifespan, so each one
    # spawned a scanner thread writing to the same SQLite file as the test that
    # started it -- which surfaced as an occasional "database is locked" and one
    # unrelated failure in six full runs. A background thread racing the test
    # that started it is a flake generator, and the scan itself is covered
    # directly in test_documents.py.
    if os.environ.get("KAI_NO_BACKGROUND_SCAN") != "1" and load_config().documents.indexed_folders:
        index_scanner.scan_in_background()
    try:
        yield
    finally:
        scheduler.stop()
        scheduler.unsubscribe(notifications.on_scheduler_delivery)
        retention.sweep()
        # Before closing, and after the scheduler has stopped writing: a
        # checkpoint needs the file to itself, and shutdown is the one moment
        # that is guaranteed. After the sweep, so the space it freed is actually
        # reclaimed rather than left in the log.
        db.checkpoint()
        db.close_connection()


app = FastAPI(title="Kai", version=__version__, lifespan=lifespan)


class LocalCredentials:
    """Nothing reaches a handler without the token — see app/security.py.

    Loopback and CORS are not access control. A form on any web page can POST
    here with no preflight and no JavaScript, and before this existed that was
    enough to wipe every local record, strike a skill off the Action Gate, or
    start the microphone. The Action Gate cannot help with any of it: it governs
    what the assistant does with a request, not who was allowed to make one.

    Written as a plain ASGI middleware rather than with `@app.middleware("http")`,
    which would wrap every response in Starlette's BaseHTTPMiddleware. That class
    pumps the body through a memory stream, and `/turn/stream` is a server-sent
    event stream whose entire point is that each token arrives when it is
    produced. Authentication is a header check; it has no business standing
    between the generator and the socket.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        # The preflight carries no Authorization header -- that is the thing it
        # is asking permission to send -- so it cannot be judged by one. CORS
        # sits outside this and has already answered it; this is for whoever
        # changes the ordering below without reading why it is that way.
        if scope.get("method") == "OPTIONS":
            return await self.app(scope, receive, send)

        headers = Headers(scope=scope)
        rejection: JSONResponse | None = None

        if not security.origin_allowed(headers.get("origin")):
            rejection = JSONResponse(
                status_code=403,
                content={"detail": "Kai only answers its own windows."},
            )
        elif not security.authorizes(headers.get("authorization")):
            rejection = JSONResponse(
                status_code=401,
                content={
                    "detail": (
                        "Not authorised. Kai's API needs the token in "
                        f"{security.token_file()}."
                    )
                },
            )

        if rejection is not None:
            return await rejection(scope, receive, send)
        await self.app(scope, receive, send)


# Added before the CORS middleware on purpose. Starlette builds the stack so the
# last one added sits outermost, which leaves CORS on the outside where it can
# answer a preflight without an answer to the question this one asks. Reversed,
# every preflight would be refused for carrying no credentials and the packaged
# app would never complete a single call.
app.add_middleware(LocalCredentials)

# The desktop UI runs from a local dev server, and inside Tauri from a
# platform-specific origin. Localhost only -- this API reaches the user's files,
# mail and calendar, so it must never accept a page served from anywhere else
# (REQ-26).
#
# Allowing only tauri:// meant the installed Windows app was CORS-blocked from
# its own backend on every request -- it reported "backend unreachable" against a
# backend that was running and healthy. The pattern lives in security.py so the
# middleware above and this policy cannot disagree about what "local" means.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=security.ALLOWED_ORIGIN_PATTERN,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# One module per area, and nothing else in this file knows what is in them.
# Order has no effect on routing: every path is complete, and FastAPI matches
# on the path rather than on the order a router was added.
for _router in ROUTERS:
    app.include_router(_router)

"""The API's front door — REQ-24, REQ-26.

Loopback is not a trust boundary, and CORS is not a lock.

The backend binds 127.0.0.1 and the CORS policy names the origins the desktop
shell serves from, which together look like access control and are not. CORS
governs whether a *response* may be read, never whether a request is delivered:
a plain HTML form on any web page in any browser is a "simple request", it takes
no preflight, and the handler runs. Reproduced against this app before this
module existed --

    POST /privacy/wipe  from Origin: https://evil.example  ->  200, data gone

-- and the same shape reaches `/actions/pre-approvals/{skill}`, which strikes a
skill off the Action Gate for good, and `/voice/listen`, which records the
microphone and runs whatever was said as a turn. Every one of those endpoints
was correctly gated on the inside. None of it helps if the front door is open.

So every request carries a shared secret the page at evil.example cannot read:

    Authorization: Bearer <token>

That header is enough on its own -- an attacker who cannot read the token cannot
forge the header -- and it has a second effect worth having, which is that
sending it makes the request non-simple, so the browser preflights it and the
CORS policy gets to refuse before the handler is ever reached.

Where the token lives
---------------------

One producer, one path, three consumers. The backend creates it; the desktop
shell, the Vite dev server and anyone holding a terminal read it from

    <data dir>/api-token

Kept across restarts rather than minted per run, for two reasons: the dev server
reads it once at startup and would otherwise need restarting every time the
backend bounced, and a token you can `type` is a token you can debug with. It
sits in the same per-user directory as the database that already holds the
user's mail, so it is protected by what protects that; on POSIX the file is
written 0600 as well.

`KAI_API_TOKEN` overrides the file when set. That is how the tests pin a known
value, and the escape hatch for anyone running the backend somewhere the data
directory is not writable.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
from pathlib import Path

from .settings import data_dir

log = logging.getLogger(__name__)

API_TOKEN_ENV = "KAI_API_TOKEN"
TOKEN_FILE_NAME = "api-token"
SCHEME = "Bearer"

# Origins allowed to reach the API from a browser context, enforced twice: the
# CORS middleware is handed this same pattern, and the check below refuses on it
# as well. The duplication is the point -- CORS withholds a header the browser
# then declines to honour, which is advice; a 403 is an answer. One definition,
# so the two can never drift.
#
# `tauri.localhost` is not optional: Windows WebView2 serves the packaged app
# from http://tauri.localhost, while tauri:// is the macOS and Linux scheme.
ALLOWED_ORIGIN_PATTERN = (
    r"^(https?://(localhost|127\.0\.0\.1|tauri\.localhost)(:\d+)?|tauri://localhost)$"
)
_ALLOWED_ORIGIN = re.compile(ALLOWED_ORIGIN_PATTERN)

_cached: str | None = None


def token_file() -> Path:
    return data_dir() / TOKEN_FILE_NAME


def token() -> str:
    """This process's API token, created on first use if there isn't one."""
    global _cached
    if _cached is not None:
        return _cached
    _cached = _resolve()
    return _cached


def reset() -> None:
    """Test hook — forget the cached token so the next call re-resolves it."""
    global _cached
    _cached = None


def _resolve() -> str:
    from_env = (os.environ.get(API_TOKEN_ENV) or "").strip()
    if from_env:
        return from_env

    path = token_file()
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass

    return _create(path)


def _create(path: Path) -> str:
    value = secrets.token_urlsafe(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # O_CREAT|O_WRONLY with mode 0600 rather than write_text then chmod:
        # the window between the two is exactly when a file is world-readable.
        handle = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(value)
        log.info("wrote a new API token to %s", path)
    except OSError as exc:
        # An unwritable data directory must not stop the backend serving. The
        # token still works for this run; what is lost is any *other* process
        # being able to find it, which is a worse experience and not a breach.
        log.warning("could not persist the API token to %s: %s", path, exc)
    return value


def authorizes(header: str | None) -> bool:
    """Whether an Authorization header carries this process's token."""
    if not header:
        return False
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != SCHEME.lower():
        return False
    # Constant-time: a byte-at-a-time comparison leaks the token's prefix to
    # anything that can time the response, and localhost is where timing is
    # cleanest.
    return secrets.compare_digest(presented.strip(), token())


def origin_allowed(origin: str | None) -> bool:
    """Whether a browser Origin may talk to this API.

    A missing Origin is allowed. Non-browser clients -- curl, a test, the
    packaged shell's own HTTP calls -- do not send one, and they are already
    holding the token, which is the control that matters here.
    """
    if origin is None:
        return True
    return bool(_ALLOWED_ORIGIN.match(origin))

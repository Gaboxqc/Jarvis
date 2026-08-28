"""What to ask for when something goes wrong — REQ-26, REQ-27.

The packaged backend has always written a rotating log to `<data dir>/logs`
(see `sidecar_log_config` in server.py, which exists because an undrained pipe
had already deadlocked the server at 70 requests). What it has never done is
tell anyone that file is there. A bug report arrives with a screenshot and a
description, and the one artefact worth having sits in a folder nobody has been
given a reason to look in.

So there is an endpoint that says where it is and what is in it, and a button in
Settings that opens the folder. Neither reads the contents: a log is a file to
attach, not a thing to render in a chat window, and shipping the text through the
API would put it somewhere else as well as where it already is.

Redaction
---------

Logs get shared, and this one carries mail addresses and the absolute paths of
the user's own files. `Redactor` masks both on the way to disk.

Deliberately narrow. Redacting everything that might identify something produces
a log that proves only that the app ran, and the failures worth diagnosing here
are about which file, which folder, which account. So: addresses become
`<address>`, and the user's home directory becomes `~` -- which is the part that
turns a path into a name -- and everything else is left legible.

The console handler is not filtered. Someone running the backend from a terminal
is reading their own screen, and a redacted console makes debugging harder for
no one's benefit.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .settings import data_dir

LOG_DIR_NAME = "logs"
MAIN_LOG = "backend.log"

_ADDRESS = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def log_directory() -> Path:
    return data_dir() / LOG_DIR_NAME


@dataclass(frozen=True)
class LogFile:
    name: str
    size_bytes: int
    modified: float

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "size_bytes": self.size_bytes, "modified": self.modified}


def log_files() -> list[LogFile]:
    """What is on disk right now, newest first. Names and sizes only."""
    directory = log_directory()
    if not directory.is_dir():
        return []

    found: list[LogFile] = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        found.append(LogFile(path.name, stat.st_size, stat.st_mtime))
    found.sort(key=lambda entry: entry.modified, reverse=True)
    return found


def summary() -> dict[str, Any]:
    files = log_files()
    return {
        "directory": str(log_directory()),
        "files": [entry.to_dict() for entry in files],
        "total_bytes": sum(entry.size_bytes for entry in files),
        # False on a fresh install and after a wipe. The UI says "nothing yet"
        # rather than offering a button that opens an empty folder.
        "exists": bool(files),
    }


def redact(text: str) -> str:
    text = _ADDRESS.sub("<address>", text)
    home = str(Path.home())
    if home:
        # Case-insensitively, because Windows hands the same directory back with
        # different capitalisation depending on who asked.
        text = re.sub(re.escape(home), "~", text, flags=re.IGNORECASE)
    return text


class Redactor(logging.Filter):
    """Mask addresses and the home directory in anything written to the file.

    Applied to the formatted message rather than to the record's arguments,
    because the arguments are where the interesting values are and they arrive
    as whatever type the caller passed. Rendering first means one rule covers
    `log.info("sent to %s", address)` and an f-string equally.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:  # noqa: BLE001 - a bad format string must not lose the line
            return True
        cleaned = redact(rendered)
        if cleaned != rendered:
            record.msg = cleaned
            record.args = ()
        return True


def wipe_logs() -> int:
    """Delete the log files. Part of "delete everything" (REQ-26).

    The active file is truncated rather than unlinked: the handler is holding it
    open, and on Windows a deleted-but-open file leaves the handler writing into
    nothing for the rest of the session.
    """
    removed = 0
    for entry in log_files():
        path = log_directory() / entry.name
        try:
            if path.name == MAIN_LOG:
                os.truncate(path, 0)
            else:
                path.unlink()
            removed += 1
        except OSError:
            continue
    return removed

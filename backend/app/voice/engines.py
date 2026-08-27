"""Optional engines fetched after installation — REQ-4, REQ-26, REQ-31.

XTTS cannot ship the way the Piper voices do. Those are model files: a download
into a folder, read by an engine already frozen into the app. XTTS is a *Python
package* on top of ~2GB of torch, and a frozen build has no interpreter to
install into -- `pip install TTS` is advice that cannot be followed from inside
an installed app, which is exactly what the settings screen used to say.

So it ships as its own executable, built by the same PyInstaller step that
builds the backend, published beside the installer, and fetched on demand into
the data directory. The installer stays its current size for everyone who never
clones a voice.

Three properties this file exists to guarantee.

**Nothing large moves without being asked.** Downloads start from an explicit
call and nowhere else. There is no "while you are here" fetch on startup.

**A half-finished download is never mistaken for an engine.** The archive lands
in a temporary file, its checksum is verified before anything is unpacked, and
the unpacked tree is moved into place only once complete. An interrupted
download leaves the previous state exactly as it was.

**The checksum is a real check.** This fetches an executable over the network
and then runs it. An unverified download would be a straightforward way to hand
somebody's machine to whoever can answer a DNS query, so a missing published
checksum is refused rather than skipped -- comparing against an empty string
would be a check that always passes.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any, Callable

from ..settings import data_dir

log = logging.getLogger(__name__)

# Published beside the installer on the same release. Pinned by version rather
# than tracking "latest": a sidecar has to match the backend that speaks to it,
# and an app silently pulling a newer protocol is a bug that only shows up in
# the field.
XTTS_VERSION = "0.3.4"
XTTS_ASSET = f"kai-xtts-{XTTS_VERSION}-x64.zip"
XTTS_URL = (
    "https://github.com/Gaboxqc/Jarvis/releases/download/"
    f"v{XTTS_VERSION}/{XTTS_ASSET}"
)

# The published bundle's checksum, compared before anything is unpacked. Empty
# would mean "not published yet", and that is refused rather than skipped --
# comparing against an empty string is a check that always passes.
XTTS_SHA256 = "7d70bd86f150a9094f3881d602ae7678a87b92961c692161306d2e8d2a160e12"

# The executable inside the archive, and what it is called once installed.
XTTS_ENTRY = "kai-xtts.exe"


class EngineError(Exception):
    """Something went wrong fetching or installing an engine."""


_install_lock = threading.Lock()
_progress: dict[str, Any] = {"state": "idle", "received": 0, "total": 0, "error": None}


def engines_dir() -> Path:
    path = data_dir() / "engines"
    path.mkdir(parents=True, exist_ok=True)
    return path


def xtts_dir() -> Path:
    return engines_dir() / "xtts"


def xtts_executable() -> Path:
    return xtts_dir() / XTTS_ENTRY


def xtts_installed() -> bool:
    return xtts_executable().exists()


def progress() -> dict[str, Any]:
    """A snapshot of the current download, for the settings screen."""
    return dict(_progress)


def _set(**fields: Any) -> None:
    _progress.update(fields)


def _verify(path: Path, expected: str) -> None:
    """Refuse anything whose checksum was not published or does not match."""
    if not expected:
        raise EngineError(
            "This build has no published checksum for the voice engine, so it "
            "cannot be verified and will not be installed."
        )

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)

    if digest.hexdigest() != expected:
        raise EngineError(
            "The downloaded voice engine did not match its published checksum "
            "and was discarded."
        )


def _long_path(path: Path) -> str:
    r"""A path Windows will still open past 260 characters.

    The bundle contains files like

        _internal/sklearn/datasets/tests/data/openml/id_292/
        api-v1-jdl-dn-australian-l-2-dv-1-s-dact.json.gz

    which is a hundred characters before the install directory is prepended.
    Windows refuses anything over MAX_PATH with a FileNotFoundError naming the
    file, which reads as a corrupt download rather than as a path length -- and
    by then the archive has already been fetched and its checksum verified.

    The \?\ prefix lifts the limit. It requires a fully resolved absolute
    path, which is why this resolves rather than trusting the caller.
    """
    if os.name != "nt":
        return str(path)
    # Built from character codes rather than written as a literal: the
    # value is backslash-backslash-question-backslash, and every layer
    # between here and a shell has its own opinion about escaping it.
    prefix = chr(92) * 2 + "?" + chr(92)
    resolved = str(path.resolve())
    return resolved if resolved.startswith(prefix) else prefix + resolved


def _unpack(archive: Path, target: Path) -> None:
    """Unpack beside the target, then swap it in.

    Unpacking straight into place would leave a directory that looks installed
    the moment the first file lands, so a download interrupted at 5% would be
    indistinguishable from a working engine on the next launch.
    """
    staging = target.parent / f".{target.name}.incoming"
    if staging.exists():
        shutil.rmtree(_long_path(staging), ignore_errors=True)
    staging.mkdir(parents=True)

    root = staging.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.namelist():
            # A member path that escapes the target would let an archive write
            # anywhere the app can write.
            if not str((staging / member).resolve()).startswith(str(root)):
                raise EngineError("The archive tried to write outside its folder.")
        bundle.extractall(_long_path(staging))

    if not (staging / XTTS_ENTRY).exists():
        shutil.rmtree(staging, ignore_errors=True)
        raise EngineError(f"The archive contained no {XTTS_ENTRY}.")

    if target.exists():
        shutil.rmtree(_long_path(target), ignore_errors=True)
    # os.replace rather than Path.replace: the extended-length form is a string,
    # and Path would normalise the prefix back off.
    os.replace(_long_path(staging), _long_path(target))


def _download(url: str, target: Path) -> None:
    import httpx

    with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as response:
        response.raise_for_status()
        _set(total=int(response.headers.get("content-length") or 0))
        with target.open("wb") as handle:
            for chunk in response.iter_bytes(1024 * 256):
                handle.write(chunk)
                _set(received=_progress["received"] + len(chunk))


def install_xtts(
    url: str | None = None,
    sha256: str | None = None,
    opener: Callable[[str], bytes] | None = None,
) -> dict[str, Any]:
    """Fetch and install the cloning engine. Explicit call only.

    `opener` and the url/checksum overrides exist for tests: the real thing
    pulls hundreds of megabytes, and a suite that does that is a suite nobody
    runs.
    """
    if not _install_lock.acquire(blocking=False):
        raise EngineError("A download is already running.")

    try:
        _set(state="downloading", received=0, total=0, error=None)
        source = url or XTTS_URL
        expected = XTTS_SHA256 if sha256 is None else sha256

        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / XTTS_ASSET
            try:
                if opener is not None:
                    payload = opener(source)
                    archive.write_bytes(payload)
                    _set(received=len(payload), total=len(payload))
                else:
                    _download(source, archive)
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                raise EngineError(f"The download failed: {exc}") from exc

            _set(state="verifying")
            _verify(archive, expected)

            _set(state="installing")
            _unpack(archive, xtts_dir())

        _set(state="installed", error=None)
        log.info("voice cloning engine installed at %s", xtts_dir())
        return {"installed": True, "path": str(xtts_dir())}
    except EngineError as exc:
        _set(state="failed", error=str(exc))
        raise
    finally:
        _install_lock.release()


def remove_xtts() -> bool:
    """Delete the engine. Reclaiming the disk must be as easy as spending it."""
    if not xtts_dir().exists():
        return False
    shutil.rmtree(xtts_dir(), ignore_errors=True)
    _set(state="idle", received=0, total=0, error=None)
    log.info("voice cloning engine removed")
    return True

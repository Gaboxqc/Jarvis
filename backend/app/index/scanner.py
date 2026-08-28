"""Incremental indexing — REQ-16, REQ-31.

Walks the configured folders and reconciles them against what is already
indexed: new and changed files are re-read, deleted files are dropped.

Change detection is (size, mtime) rather than a filesystem watcher. A watcher
sees nothing while the app is closed, so it needs a reconciling scan on startup
anyway — at which point the watcher is the redundant half. Comparing stored
state catches every change regardless of whether Kai was running when it
happened, which is the property that actually matters here.

Scanning yields: it runs in a background thread, sleeps between files, and backs
off entirely on battery or during a focus session (REQ-31).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from ..settings import load_config
from . import extract, store
from .chunk import chunk_sections

log = logging.getLogger(__name__)

SLEEP_BETWEEN_FILES = 0.02
SKIP_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv", "$RECYCLE.BIN",
             "AppData", "Windows", "Program Files", "Program Files (x86)"}

_lock = threading.Lock()
_running = False
_last_scan: datetime | None = None
_paused = False


@dataclass
class ScanResult:
    indexed: int = 0
    updated: int = 0
    removed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def changed(self) -> int:
        return self.indexed + self.updated + self.removed

    def summary(self) -> str:
        if not self.changed and not self.failed:
            return "Index is already up to date."
        parts = []
        if self.indexed:
            parts.append(f"{self.indexed} new")
        if self.updated:
            parts.append(f"{self.updated} updated")
        if self.removed:
            parts.append(f"{self.removed} removed")
        if self.failed:
            parts.append(f"{self.failed} unreadable")
        return "Indexed: " + ", ".join(parts) + "."

    def to_dict(self) -> dict[str, object]:
        return {
            "indexed": self.indexed,
            "updated": self.updated,
            "removed": self.removed,
            "failed": self.failed,
            "skipped": self.skipped,
            "errors": self.errors[:10],
            "duration_seconds": round(self.duration_seconds, 2),
        }


def pause(value: bool = True) -> None:
    """Focus sessions call this so indexing never competes for a busy machine."""
    global _paused
    _paused = value


def is_paused() -> bool:
    return _paused


def should_defer() -> str | None:
    """Why scanning should not run right now, or None if it may."""
    if _paused:
        return "a focus session is active"
    if load_config().documents.pause_on_battery and _on_battery():
        return "the machine is on battery"
    return None


def _on_battery() -> bool:
    try:
        import psutil

        battery = psutil.sensors_battery()
    except Exception:  # noqa: BLE001 — desktops report nothing; treat as mains
        return False
    return bool(battery is not None and not battery.power_plugged)


def scan(force: bool = False) -> ScanResult:
    """Reconcile the index with the configured folders."""
    global _running, _last_scan

    result = ScanResult()
    started = time.monotonic()

    with _lock:
        if _running:
            result.errors.append("a scan is already running")
            return result
        _running = True

    try:
        if not force:
            reason = should_defer()
            if reason:
                result.errors.append(f"deferred: {reason}")
                return result

        config = load_config()
        folders = [f for f in config.documents.indexed_folders if f.is_dir()]
        max_bytes = max(1, config.documents.max_file_mb) * 1024 * 1024

        known = store.known_state()
        seen: set[str] = set()

        for path in _walk(folders):
            key = str(path)
            seen.add(key)
            try:
                stat = path.stat()
            except OSError:
                continue

            if stat.st_size > max_bytes:
                result.skipped += 1
                continue
            if stat.st_size == 0:
                result.skipped += 1
                continue

            previous = known.get(key)
            if previous is not None and previous == (stat.st_size, stat.st_mtime):
                continue

            outcome = _index_one(path, stat.st_size, stat.st_mtime)
            if outcome == "failed":
                result.failed += 1
                result.errors.append(path.name)
            elif previous is None:
                result.indexed += 1
            else:
                result.updated += 1

            time.sleep(SLEEP_BETWEEN_FILES)

        # Anything indexed that no longer exists on disk is dropped, so search
        # never cites a file the user deleted.
        for key in known.keys() - seen:
            store.forget_document(key)
            result.removed += 1

        _last_scan = datetime.now(timezone.utc)
        return result
    finally:
        result.duration_seconds = time.monotonic() - started
        with _lock:
            _running = False


def _index_one(path: Path, size: int, mtime: float) -> str:
    try:
        sections = extract.extract(path)
    except extract.ExtractionError as exc:
        store.record_failure(path, size=size, mtime=mtime, error=str(exc))
        return "failed"
    except Exception as exc:  # noqa: BLE001 — never let one file stop the scan
        log.exception("unexpected failure indexing %s", path)
        store.record_failure(path, size=size, mtime=mtime, error=repr(exc))
        return "failed"

    chunks = chunk_sections(sections)
    if not chunks:
        store.record_failure(path, size=size, mtime=mtime, error="no text after chunking")
        return "failed"

    store.replace_document(
        path, title=extract.title_for(path, sections), size=size, mtime=mtime, chunks=chunks
    )
    return "indexed"


def _walk(folders: list[Path], max_depth: int = 6) -> Iterator[Path]:
    import os

    for folder in folders:
        base_depth = len(folder.parts)
        for dirpath, dirnames, filenames in os.walk(folder, onerror=lambda _: None):
            current = Path(dirpath)
            if len(current.parts) - base_depth >= max_depth:
                dirnames[:] = []
            dirnames[:] = [
                d for d in dirnames if d not in SKIP_DIRS and not d.startswith((".", "$"))
            ]
            for filename in filenames:
                path = current / filename
                if extract.is_supported(path):
                    yield path


def is_due() -> bool:
    if _last_scan is None:
        return True
    interval = timedelta(minutes=max(1, load_config().documents.rescan_minutes))
    return datetime.now(timezone.utc) - _last_scan >= interval


def scan_in_background(force: bool = False) -> threading.Thread:
    thread = threading.Thread(
        target=lambda: scan(force=force), name="kai-indexer", daemon=True
    )
    thread.start()
    return thread


def maybe_scan() -> None:
    """Called from the scheduler tick; cheap when there is nothing to do."""
    if _running or not is_due() or should_defer():
        return
    scan_in_background()


def status() -> dict[str, Any]:
    config = load_config()
    return {
        "folders": [str(f) for f in config.documents.indexed_folders],
        "running": _running,
        "paused": _paused,
        "deferred_because": should_defer(),
        "last_scan": _last_scan.isoformat() if _last_scan else None,
        **store.stats(),
    }


def reset_state() -> None:
    """Test hook."""
    global _last_scan, _paused, _running
    _last_scan = None
    _paused = False
    _running = False

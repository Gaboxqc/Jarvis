"""Local SQLite storage — the only place user data is persisted (REQ-26).

One file, one schema, one wipe target. Connections are per-thread because the
scheduler thread and the request threads both touch the same database.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .settings import data_dir

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_local = threading.local()
_db_path_override: Path | None = None


def db_path() -> Path:
    if _db_path_override is not None:
        return _db_path_override
    return data_dir() / "kai.db"


def set_db_path(path: Path | None) -> None:
    """Test hook — point storage at a temp file and drop cached connections."""
    global _db_path_override
    close_connection()
    _db_path_override = path


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- REQ-6: rolling conversation window
CREATE TABLE IF NOT EXISTS conversation_turns (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text         TEXT NOT NULL,
    ts           TEXT NOT NULL,
    skill_calls  TEXT NOT NULL DEFAULT '[]',
    emotion_tag  TEXT
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON conversation_turns(session_id, ts);

-- REQ-7: durable user facts, never written silently
CREATE TABLE IF NOT EXISTS memory_facts (
    id            TEXT PRIMARY KEY,
    text          TEXT NOT NULL,
    category      TEXT NOT NULL DEFAULT 'fact',
    source_turn_id TEXT,
    created_at    TEXT NOT NULL,
    last_used_at  TEXT
);

-- REQ-24 / REQ-25: every side effect, confirmed and journalled
CREATE TABLE IF NOT EXISTS action_records (
    id           TEXT PRIMARY KEY,
    batch_id     TEXT NOT NULL,
    skill_name   TEXT NOT NULL,
    params       TEXT NOT NULL DEFAULT '{}',
    severity     TEXT NOT NULL CHECK (severity IN ('routine', 'consequential')),
    reversible   INTEGER NOT NULL DEFAULT 0,
    preview      TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL,
    undo_payload TEXT,
    result       TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL,
    executed_at  TEXT,
    expires_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_actions_batch ON action_records(batch_id);
CREATE INDEX IF NOT EXISTS idx_actions_status ON action_records(status, created_at);

-- REQ-24: explicit, listed, revocable standing approvals
CREATE TABLE IF NOT EXISTS pre_approvals (
    skill_name TEXT PRIMARY KEY,
    granted_at TEXT NOT NULL
);

-- REQ-9 / REQ-12: survives restart by living on disk, not in memory
CREATE TABLE IF NOT EXISTS scheduled_items (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    label         TEXT NOT NULL DEFAULT '',
    payload       TEXT NOT NULL DEFAULT '{}',
    next_fire_at  TEXT,
    recurrence    TEXT,
    created_at    TEXT NOT NULL,
    last_fired_at TEXT,
    active        INTEGER NOT NULL DEFAULT 1,
    delivered     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sched_due ON scheduled_items(active, next_fire_at);

-- REQ-16: one row per indexed file. size+mtime is the change detector, so a
-- rescan only touches files that actually changed.
CREATE TABLE IF NOT EXISTS indexed_documents (
    path        TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    size        INTEGER NOT NULL DEFAULT 0,
    mtime       REAL NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    indexed_at  TEXT NOT NULL,
    error       TEXT
);

-- Full-text search over chunk text. FTS5 ships with SQLite, so document search
-- works on a fresh install with no model download and no vector store. Semantic
-- retrieval can be layered on later behind index/search.py without touching
-- anything above it.
CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks USING fts5(
    text,
    path    UNINDEXED,
    section UNINDEXED,
    ordinal UNINDEXED,
    tokenize = 'porter unicode61'
);

-- REQ-19: meeting transcripts. Stored locally, listable, individually deletable.
CREATE TABLE IF NOT EXISTS transcripts (
    id               TEXT PRIMARY KEY,
    label            TEXT NOT NULL DEFAULT '',
    started_at       TEXT NOT NULL,
    ended_at         TEXT,
    sources          TEXT NOT NULL DEFAULT '[]',
    text             TEXT NOT NULL DEFAULT '',
    summary          TEXT,
    duration_seconds REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_transcripts_started ON transcripts(started_at DESC);

-- REQ-10: tasks and notes
CREATE TABLE IF NOT EXISTS tasks (
    id           TEXT PRIMARY KEY,
    text         TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'task' CHECK (kind IN ('task', 'note')),
    tags         TEXT NOT NULL DEFAULT '[]',
    due          TEXT,
    done         INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    completed_at TEXT
);
"""


def _quarantine(path: Path, reason: str) -> Path:
    """Move a damaged database aside so a fresh one can be created.

    A corrupt file otherwise fails every single open, which means the scheduler
    thread raises every few seconds, the API returns 500 for anything touching
    storage, and the app is permanently wedged with no way out from inside it.
    Losing the contents is bad; being unable to start at all is worse, and the
    file is kept so nothing is destroyed by this decision.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    quarantine = path.parent / f"corrupt-{stamp}"
    quarantine.mkdir(parents=True, exist_ok=True)

    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            try:
                candidate.replace(quarantine / candidate.name)
            except OSError:
                # Still held open by something; deleting is the only way past.
                try:
                    candidate.unlink()
                except OSError:
                    pass

    log.error(
        "database at %s was unusable (%s). Moved to %s and started a fresh one.",
        path, reason, quarantine,
    )
    return quarantine


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    path = db_path()
    if conn is not None and getattr(_local, "path", None) == path:
        return conn

    if conn is not None:
        conn.close()

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = _open(path)
    except sqlite3.DatabaseError as exc:
        # "database disk image is malformed" and friends. Retried once against a
        # clean file; a second failure is the filesystem's problem, not ours.
        _quarantine(path, str(exc))
        conn = _open(path)

    _local.conn = conn
    _local.path = path
    return conn


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, timeout=15.0)
    try:
        return _prepare(conn, path)
    except Exception:
        # The handle must be released before the caller can move the file out of
        # the way: on Windows an open handle makes both rename and delete fail,
        # so the retry would meet the same broken file and raise again.
        conn.close()
        raise


def _prepare(conn: sqlite3.Connection, path: Path) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    return conn


def checkpoint() -> int:
    """Fold the write-ahead log back into the database and truncate it.

    WAL files grow while writes happen and shrink when SQLite checkpoints them,
    which it does automatically -- but only when no reader holds the file open.
    Under normal use readers come and go constantly, so some automatic
    checkpoints are skipped as busy and the log keeps a high-water mark. An
    assistant that runs for days accumulates a WAL of several megabytes that
    never comes back down on its own.

    Measured rather than assumed: with a reader open, `wal_checkpoint(TRUNCATE)`
    reports busy and the file stays put; with none open, 1.4MB goes to zero.
    Called on shutdown, when by definition nothing else is reading.

    Returns the bytes reclaimed, or 0 if the checkpoint could not run.
    """
    path = db_path()
    log_file = path.with_name(path.name + "-wal")
    before = log_file.stat().st_size if log_file.exists() else 0
    if not before:
        return 0

    try:
        connect().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.DatabaseError as exc:
        # Never worth failing a shutdown over: the log is valid either way and
        # the next start reads it correctly.
        log.debug("wal checkpoint skipped: %s", exc)
        return 0

    after = log_file.stat().st_size if log_file.exists() else 0
    reclaimed = max(0, before - after)
    if reclaimed:
        log.info("checkpointed %d KB of write-ahead log", reclaimed // 1024)
    return reclaimed


def close_connection() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
    _local.conn = None
    _local.path = None


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    conn = connect()
    cur = conn.execute(sql, tuple(params))
    conn.commit()
    return cur


def query(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return connect().execute(sql, tuple(params)).fetchall()


def query_one(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    return connect().execute(sql, tuple(params)).fetchone()


def now() -> str:
    """UTC, ISO-8601, second precision. Every timestamp in the DB uses this."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(value: str | None, fallback: Any = None) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return fallback


def wipe_all_local_data() -> dict[str, int]:
    """REQ-26 — the single 'delete everything' action.

    Returns the row count removed per table so the UI can report what went.
    """
    tables = [
        "conversation_turns",
        "memory_facts",
        "action_records",
        "pre_approvals",
        "scheduled_items",
        "tasks",
        "document_chunks",
        "indexed_documents",
        "transcripts",
    ]
    removed: dict[str, int] = {}
    conn = connect()
    for table in tables:
        count = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
        conn.execute(f"DELETE FROM {table}")
        removed[table] = int(count)
    conn.commit()
    conn.execute("VACUUM")
    return removed

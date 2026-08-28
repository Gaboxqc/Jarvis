"""Keeping the write-ahead log from growing forever — REQ-26.

This started as a bug report I wrote myself and got wrong. Reading db.py, the
query helpers run a SELECT and never commit, which looks like a leaked read
transaction holding the log open. Measuring says otherwise: Python's sqlite3
opens no transaction for a SELECT, the cursor is released as soon as the rows
are fetched, and `in_transaction` is False afterwards either way.

What is real is smaller. SQLite checkpoints the log automatically, but only
when nothing else is reading, and under normal use readers come and go
constantly -- so some automatic checkpoints are skipped as busy and the file
keeps its high-water mark. An assistant left running for days ends up with
several megabytes of log that never comes back down on its own.

So these pin the mechanism rather than the imagined bug: a reader blocks
truncation, no reader allows it, and shutdown is when there is guaranteed to
be none.
"""

from __future__ import annotations

import sqlite3

from app import db


def _fill(rows: int = 300) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS probe (id INTEGER PRIMARY KEY, v TEXT)")
    for _ in range(rows):
        db.execute("INSERT INTO probe (v) VALUES (?)", ("x" * 300,))


def _wal_size() -> int:
    path = db.db_path()
    log_file = path.with_name(path.name + "-wal")
    return log_file.stat().st_size if log_file.exists() else 0


def test_reading_does_not_leave_a_transaction_open(workspace):
    """The bug I reported and could not reproduce, kept as a guard."""
    _fill(10)
    connection = db.connect()

    db.query("SELECT * FROM probe")
    assert connection.in_transaction is False

    # fetchone leaves rows unread, which is the case that looked suspicious.
    db.query_one("SELECT * FROM probe")
    assert connection.in_transaction is False


def test_the_log_is_reclaimed_on_checkpoint(workspace):
    _fill()
    assert _wal_size() > 0

    reclaimed = db.checkpoint()

    assert reclaimed > 0
    assert _wal_size() == 0


def test_an_open_reader_blocks_reclamation(workspace):
    """Why this belongs at shutdown rather than on a timer.

    A checkpoint that runs while anything is reading reports busy and leaves
    the file alone, so a periodic one would often do nothing at exactly the
    times the app is busy enough to need it.
    """
    _fill()
    before = _wal_size()

    reader = sqlite3.connect(db.db_path(), timeout=5.0)
    cursor = reader.execute("SELECT * FROM probe")
    cursor.fetchone()  # deliberately not exhausted
    try:
        db.checkpoint()
        assert _wal_size() == before, "the log shrank while a reader held it open"
    finally:
        cursor.close()
        reader.close()

    assert db.checkpoint() > 0
    assert _wal_size() == 0


def test_a_second_checkpoint_costs_nothing(workspace):
    """Shutdown runs this every time, including when there is nothing to do.

    Note that a brand new database does not have an empty log: opening one
    writes the schema, which is a write like any other. So the free case is the
    second checkpoint, not the first.
    """
    db.connect()
    db.checkpoint()

    assert db.checkpoint() == 0

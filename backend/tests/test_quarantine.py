"""When the database is moved aside, and when it must not be.

Quarantine exists because a corrupt file fails every open forever, which
wedges the app with no way out from inside it. The cost of being wrong about
which files are corrupt is the user's data moved aside and replaced with an
empty one, so the question of what counts is the whole safety of the feature.

It used to catch `sqlite3.DatabaseError`, which is not "corrupt": the
OperationalError raised for `database is locked`, `unable to open database
file` and `disk I/O error` all inherit from it. `_prepare()` takes locks to
set its PRAGMAs, so a second process holding the file past the open timeout
was enough to have a healthy database quarantined.

That is not theoretical. A database quarantined on the developer's machine in
August still reports `integrity_check: ok` and holds 3,329 chunks across 33
documents, and orphaned backends outliving their parent -- which this project
has hit repeatedly on Windows -- are exactly what holds a file open.
"""

from __future__ import annotations

import sqlite3

import pytest

from app import db


@pytest.fixture(autouse=True)
def _isolated():
    db.close_connection()
    yield
    db.close_connection()
    db.set_db_path(None)


def corrupt_file(path):
    path.write_bytes(b"SQLite format 3\x00" + b"\xde\xad\xbe\xef" * 4096)


# -- what must still be quarantined --------------------------------------


def test_a_corrupt_file_is_still_moved_aside(tmp_path):
    broken = tmp_path / "kai.db"
    corrupt_file(broken)
    db.set_db_path(broken)

    conn = db.connect()

    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    kept = list(tmp_path.glob("corrupt-*/kai.db"))
    assert kept, "the damaged file was destroyed instead of kept"


# -- what must not be ----------------------------------------------------


def test_a_locked_database_is_not_treated_as_corrupt(tmp_path, monkeypatch):
    """The case that cost a real database.

    A lock is transient and the file is fine. Starting empty on top of
    somebody's data because another process was slow to let go is the one
    outcome worse than refusing to start.
    """
    live = tmp_path / "kai.db"
    db.set_db_path(live)
    db.connect()  # create a real, healthy database
    db.close_connection()
    before = live.read_bytes()

    def busy(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db, "_open", busy)

    with pytest.raises(sqlite3.OperationalError):
        db.connect()

    assert not list(tmp_path.glob("corrupt-*")), "a locked database was quarantined"
    assert live.read_bytes() == before, "a locked database was modified"


@pytest.mark.parametrize(
    "message",
    [
        "database is locked",
        "unable to open database file",
        "disk I/O error",
        "attempt to write a readonly database",
    ],
)
def test_transient_failures_propagate(tmp_path, monkeypatch, message):
    """Every one of these inherits from DatabaseError and none means corrupt.

    Parametrised rather than written once, because the bug was not "we forgot
    about locks" -- it was that a whole family of unrelated errors shared one
    branch with corruption.
    """
    live = tmp_path / "kai.db"
    db.set_db_path(live)
    db.connect()
    db.close_connection()

    monkeypatch.setattr(
        db, "_open", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError(message))
    )

    with pytest.raises(sqlite3.OperationalError):
        db.connect()
    assert not list(tmp_path.glob("corrupt-*")), f"{message!r} was treated as corruption"


@pytest.mark.parametrize(
    "message",
    [
        "database disk image is malformed",
        "file is not a database",
        "file is encrypted or is not a database",
    ],
)
def test_corruption_messages_are_recognised(message):
    assert db._is_corruption(sqlite3.DatabaseError(message))


@pytest.mark.parametrize(
    "message",
    ["database is locked", "unable to open database file", "disk I/O error"],
)
def test_transient_messages_are_not(message):
    assert not db._is_corruption(sqlite3.OperationalError(message))


# -- and nothing is ever deleted -----------------------------------------


def test_a_file_that_cannot_be_moved_is_left_alone(tmp_path, monkeypatch):
    """`_quarantine` promised nothing would be destroyed and then deleted.

    The old code unlinked any file it failed to move, calling it "the only way
    past" -- in exactly the case where the file was held open by something
    still running, which is when the data is most likely to still matter.
    """
    broken = tmp_path / "kai.db"
    corrupt_file(broken)
    before = broken.read_bytes()

    def immovable(self, target):
        raise OSError(32, "The process cannot access the file")

    monkeypatch.setattr("pathlib.Path.replace", immovable)

    with pytest.raises(db.DatabaseUnavailable):
        db._quarantine(broken, "database disk image is malformed")

    assert broken.exists(), "the file was deleted after the move failed"
    assert broken.read_bytes() == before


def test_the_quarantine_error_is_not_a_sqlite_error():
    """Otherwise connect() would catch it and quarantine over the top.

    Same reasoning as MigrationError, and the same trap: the handler this is
    raised through is the one that treats sqlite3.DatabaseError as corruption.
    """
    assert not issubclass(db.DatabaseUnavailable, sqlite3.DatabaseError)

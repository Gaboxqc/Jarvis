"""Schema migrations — REQ-26, REQ-27.

The app updates itself. Version 0.3.5 is installed on machines right now, each
with a database full of the user's conversations, memories and action history,
and the next release that needs a column has to reach all of them.

Before this, it could not. `_prepare()` ran CREATE TABLE IF NOT EXISTS, which
does nothing to a table that already exists, and SCHEMA_VERSION was written into
`meta` and never read back. A new column would have appeared on fresh installs
and nowhere else, and the app would have worked perfectly for a new user and
raised "no such column" for everyone who already had it -- the failure mode that
only reaches people who have been using it longest.

The interesting tests here are not the happy path. They are the two mistakes
this machinery makes easy: bumping SCHEMA_VERSION without writing the migration,
and writing a migration that fails halfway.
"""

from __future__ import annotations

import sqlite3

import pytest

from app import db


def _v1_database(path, version: int = 1) -> None:
    """A database as an installed copy of Kai would have left it."""
    conn = sqlite3.connect(path)
    conn.executescript(db.SCHEMA)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?)", (str(version),)
    )
    conn.execute(
        "INSERT INTO tasks(id, text, kind, created_at) VALUES('keep', 'buy milk', 'task', ?)",
        (db.now(),),
    )
    conn.commit()
    conn.close()


def _version_in(path) -> int:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(row[0])
    finally:
        conn.close()


@pytest.fixture
def database(tmp_path, monkeypatch):
    """A database file at a path db.connect() will use, closed between uses."""
    path = tmp_path / "kai.db"
    db.close_connection()
    db.set_db_path(path)
    yield path
    db.close_connection()
    db.set_db_path(None)


# -- the structural guard -------------------------------------------------


def test_every_version_between_one_and_current_has_a_migration():
    """The mistake this machinery makes easy.

    Bumping SCHEMA_VERSION and forgetting the migration produces a build that
    refuses to open any existing database. Bumping it and editing SCHEMA but not
    MIGRATIONS produces something worse -- two different schemas depending on
    when the user installed, and no error from either.
    """
    missing = [
        version
        for version in range(2, db.SCHEMA_VERSION + 1)
        if version not in db.MIGRATIONS
    ]
    assert not missing, (
        f"SCHEMA_VERSION is {db.SCHEMA_VERSION} but there is no migration to {missing}. "
        "Every step from 2 upwards needs one."
    )


def test_no_migration_claims_a_version_that_does_not_exist_yet():
    """The other direction: a migration written before the version was bumped
    never runs, and looks like it did."""
    ahead = [version for version in db.MIGRATIONS if version > db.SCHEMA_VERSION]
    assert not ahead, f"migrations {ahead} are above SCHEMA_VERSION {db.SCHEMA_VERSION}"


def test_migrations_start_at_two():
    """Version 1 is what SCHEMA builds. There is nothing to migrate to it."""
    assert all(version >= 2 for version in db.MIGRATIONS)


# -- a fresh database -----------------------------------------------------


def test_a_new_database_is_stamped_at_the_current_version(database):
    db.connect()

    assert _version_in(database) == db.SCHEMA_VERSION


def test_a_new_database_runs_no_migrations(database, monkeypatch):
    """SCHEMA already built the current shape. Running the migrations over it
    would try to add columns that are already there."""
    ran: list[int] = []
    monkeypatch.setattr(db, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(
        db, "MIGRATIONS", {2: ("ALTER TABLE tasks ADD COLUMN priority INTEGER DEFAULT 0",)}
    )
    monkeypatch.setattr(db, "_apply", lambda conn, found: ran.append(found))

    db.connect()

    assert ran == []
    assert _version_in(database) == 2


# -- an existing database -------------------------------------------------


def test_an_existing_database_is_migrated_and_keeps_its_rows(database, monkeypatch):
    _v1_database(database)
    monkeypatch.setattr(db, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(
        db, "MIGRATIONS", {2: ("ALTER TABLE tasks ADD COLUMN priority INTEGER DEFAULT 0",)}
    )

    db.connect()

    assert _version_in(database) == 2
    row = db.query_one("SELECT text, priority FROM tasks WHERE id = 'keep'")
    assert row["text"] == "buy milk"
    assert row["priority"] == 0


def test_several_versions_are_applied_in_order(database, monkeypatch):
    """Order is asserted by making the steps depend on each other: 3 alters a
    table 2 creates, and 4 reads a column 3 adds. Run out of order, any of them
    raises. That is a stronger claim than watching which SQL went past."""
    _v1_database(database)
    monkeypatch.setattr(db, "SCHEMA_VERSION", 4)
    monkeypatch.setattr(db, "MIGRATIONS", {
        2: ("CREATE TABLE reminders_archive (id TEXT PRIMARY KEY)",),
        3: ("ALTER TABLE reminders_archive ADD COLUMN archived_at TEXT",),
        4: ("INSERT INTO reminders_archive(id, archived_at) VALUES('x', 'now')",),
    })

    db.connect()

    assert _version_in(database) == 4
    assert db.query_one("SELECT archived_at FROM reminders_archive")["archived_at"] == "now"


def test_only_the_missing_steps_run(database, monkeypatch):
    """A database already at 2 must not be handed migration 2 again -- ALTER
    TABLE ADD COLUMN is not idempotent and would raise on the duplicate."""
    _v1_database(database, version=2)
    monkeypatch.setattr(db, "SCHEMA_VERSION", 3)
    monkeypatch.setattr(db, "MIGRATIONS", {
        2: ("ALTER TABLE tasks ADD COLUMN two TEXT",),   # must not run again
        3: ("ALTER TABLE tasks ADD COLUMN three TEXT",),
    })

    db.connect()

    assert _version_in(database) == 3
    columns = {row[1] for row in db.query("PRAGMA table_info(tasks)")}
    assert "three" in columns


# -- when it goes wrong ---------------------------------------------------


def test_a_failed_migration_leaves_the_version_where_it_was(database, monkeypatch):
    """The stamp is inside the transaction. A database that claims to be at 2
    while still shaped like 1 is worse than one that admits it failed: the next
    start would skip the migration and every query would meet the old schema.
    """
    _v1_database(database)
    monkeypatch.setattr(db, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(db, "MIGRATIONS", {
        2: (
            "ALTER TABLE tasks ADD COLUMN fine TEXT",
            "ALTER TABLE nothing_of_the_sort ADD COLUMN broken TEXT",
        ),
    })

    with pytest.raises(db.MigrationError):
        db.connect()

    assert _version_in(database) == 1
    conn = sqlite3.connect(database)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    finally:
        conn.close()
    assert "fine" not in columns, "the first statement was not rolled back"


def test_a_bumped_version_with_no_migration_refuses_rather_than_lying(database, monkeypatch):
    _v1_database(database)
    monkeypatch.setattr(db, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(db, "MIGRATIONS", {})

    with pytest.raises(db.MigrationError, match="no migration"):
        db.connect()

    assert _version_in(database) == 1


def test_a_database_from_a_newer_build_is_left_alone(database, monkeypatch, caplog):
    """Someone rolled back to an older Kai. Its data is not this build's to
    rewrite, and refusing to start would strand it behind a version they may no
    longer have."""
    _v1_database(database, version=99)

    with caplog.at_level("ERROR"):
        db.connect()

    assert _version_in(database) == 99
    assert "newer version" in caplog.text
    assert db.query_one("SELECT text FROM tasks WHERE id = 'keep'")["text"] == "buy milk"


def test_an_unreadable_version_does_not_stop_the_app(database, monkeypatch):
    """Whatever wrote 'banana' there, refusing to open the file is not the
    proportionate response (REQ-27)."""
    _v1_database(database)
    conn = sqlite3.connect(database)
    conn.execute("UPDATE meta SET value='banana' WHERE key='schema_version'")
    conn.commit()
    conn.close()

    db.connect()

    assert db.query_one("SELECT text FROM tasks WHERE id = 'keep'")["text"] == "buy milk"


def test_a_failed_migration_does_not_quarantine_the_database(database, monkeypatch):
    """The bug these tests caught in their own first run.

    connect() treats sqlite3.DatabaseError as a corrupt disk image: it moves the
    file aside and starts an empty one, which is right for a file that cannot be
    read and catastrophic for a file that reads perfectly and merely met a
    migration with a typo in it. As first written, a bad migration deleted every
    user's data on upgrade -- the exact outcome the migration existed to avoid.

    So MigrationError is not a DatabaseError, and this is the test that says so
    in terms of what the user would lose.
    """
    _v1_database(database)
    monkeypatch.setattr(db, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(db, "MIGRATIONS", {2: ("ALTER TABLE no_such_table ADD COLUMN x TEXT",)})

    with pytest.raises(db.MigrationError):
        db.connect()

    assert not list(database.parent.glob("corrupt-*")), "the database was quarantined"
    conn = sqlite3.connect(database)
    try:
        row = conn.execute("SELECT text FROM tasks WHERE id='keep'").fetchone()
    finally:
        conn.close()
    assert row is not None and row[0] == "buy milk", "the user's data was thrown away"


# -- the first real migration ---------------------------------------------


def test_a_version_one_database_gains_the_vector_tables(database):
    """Schema 2, and the first time this machinery carried a real change rather
    than a synthetic one. An install from before semantic retrieval has to end
    up with the same tables a fresh one does, or search behaves differently
    depending on when you installed -- which is the whole failure the version
    read exists to prevent.
    """
    _v1_database(database)

    db.connect()

    assert _version_in(database) == db.SCHEMA_VERSION
    tables = {
        row["name"]
        for row in db.query("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"chunk_vectors", "fact_vectors"} <= tables
    # And the rows that were there before are still there.
    assert db.query_one("SELECT text FROM tasks WHERE id = 'keep'")["text"] == "buy milk"


def _v3_database(database) -> None:
    """A database as version 3 left it: the chunk_vectors counter, and no
    fact_vectors one.

    Built by removing the version 4 statements from the current SCHEMA rather
    than by keeping a copy of the old one. A stored copy is a second schema to
    maintain and it rots silently; this cannot drift from what version 4
    actually added, because it is defined as its absence.
    """
    _v1_database(database, version=3)
    conn = sqlite3.connect(database)
    for name in ("fact_vectors_ai", "fact_vectors_au", "fact_vectors_ad"):
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
    conn.execute("DELETE FROM index_state WHERE key = 'fact_vectors_generation'")
    conn.commit()
    conn.close()


def test_a_version_three_database_gains_the_fact_vector_counter(database):
    """Schema 4. memory/long_term.py holds the fact vectors resident and asks
    this counter whether they have gone stale; on an install that never got the
    triggers, the counter would never move and recall would keep answering from
    a matrix built before the user's last five memories."""
    _v3_database(database)

    db.connect()

    assert _version_in(database) == db.SCHEMA_VERSION
    assert db.query_one(
        "SELECT value FROM index_state WHERE key = 'fact_vectors_generation'"
    ) is not None

    # And it actually moves, which is the only property the cache reads.
    before = int(db.query_one(
        "SELECT value FROM index_state WHERE key = 'fact_vectors_generation'"
    )["value"])
    db.execute(
        "INSERT INTO fact_vectors(fact_id, model, vector) VALUES('f', 'm', X'00000000')"
    )
    after = int(db.query_one(
        "SELECT value FROM index_state WHERE key = 'fact_vectors_generation'"
    )["value"])
    assert after > before, "the migrated database did not get the triggers"


def test_a_fresh_database_has_the_same_triggers_as_a_migrated_one(database, tmp_path):
    """The drift this machinery makes easy, in the form it actually took.

    Schema 4 is three triggers and a row, written out twice -- once in SCHEMA
    for fresh installs and once in MIGRATIONS for existing ones. Nothing but a
    comparison notices when one copy gains a statement the other does not, and
    the symptom would be a stale cache on exactly one half of the installed
    base.

    Triggers and index_state rows, not just tables: version 4 adds no table at
    all, so the existing table comparison below would have passed a migration
    that did nothing whatsoever.
    """
    _v3_database(database)
    db.connect()
    migrated = {
        row["name"] for row in db.query("SELECT name FROM sqlite_master WHERE type='trigger'")
    }
    migrated_keys = {row["key"] for row in db.query("SELECT key FROM index_state")}
    db.close_connection()

    db.set_db_path(tmp_path / "fresh4.db")
    db.connect()
    fresh = {
        row["name"] for row in db.query("SELECT name FROM sqlite_master WHERE type='trigger'")
    }
    fresh_keys = {row["key"] for row in db.query("SELECT key FROM index_state")}

    assert fresh == migrated
    assert fresh_keys == migrated_keys


def test_a_fresh_database_has_the_same_tables_as_a_migrated_one(database, tmp_path):
    """SCHEMA and MIGRATIONS have to agree. They are edited in different places
    and nothing but this notices when they drift."""
    _v1_database(database)
    db.connect()
    migrated = {
        row["name"] for row in db.query("SELECT name FROM sqlite_master WHERE type='table'")
    }
    db.close_connection()

    db.set_db_path(tmp_path / "fresh.db")
    db.connect()
    fresh = {
        row["name"] for row in db.query("SELECT name FROM sqlite_master WHERE type='table'")
    }

    assert fresh == migrated

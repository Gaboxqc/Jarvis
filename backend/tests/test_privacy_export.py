"""Taking your data with you — REQ-26.

`wipe_all_local_data()` was the only control over the record: all of it, or
none. REQ-26 asks for local data *control*, and a switch that only points at
destruction is not a dial.

Most of this file is about what the zip must *not* contain. An export is the
one operation that deliberately gathers everything into a single file somebody
will then email to themselves, so a secret that leaks into it leaks further
than it would anywhere else in this app.
"""

from __future__ import annotations

import json
import zipfile

import pytest
import yaml
from fastapi.testclient import TestClient

from app import privacy_export
from app.memory import long_term
from app.settings import reset_config_cache


@pytest.fixture
def client(workspace):
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def filled(workspace):
    """A database with something of every kind in it."""
    from app import db
    from app.memory import short_term
    from app.scheduler import store as sched_store
    from app.skills.planning import tasks as task_store

    long_term.add("The user is allergic to shellfish", "fact")
    task_store.create_task("Renew the lease", "task", None)
    short_term.record("s", "user", "what is on my calendar")
    short_term.record("s", "assistant", "Nothing today.")
    sched_store.add(kind=sched_store.KIND_REMINDER, label="Call the dentist", fire_at=None)
    db.execute(
        "INSERT INTO transcripts(id, label, started_at, text, summary, duration_seconds) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        (
            "t1",
            "Q3 / planning",
            db.now(),
            "Ana said the deposit was 1200.",
            db.dumps({"summary": "Budget talk.", "decisions": ["Raise it"], "actions": ["Email Ana"]}),
            61.0,
        ),
    )
    db.execute(
        "INSERT INTO indexed_documents(path, title, size, chunk_count, indexed_at) "
        "VALUES(?, ?, ?, ?, ?)",
        ("C:/Users/someone/Documents/lease.pdf", "lease", 1024, 7, db.now()),
    )
    return workspace


def archive(client) -> zipfile.ZipFile:
    import io

    response = client.post("/privacy/export")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    return zipfile.ZipFile(io.BytesIO(response.content))


# -- it holds what it says it holds ---------------------------------------


def test_the_export_contains_every_kind_of_record(client, filled):
    with archive(client) as zip_file:
        names = set(zip_file.namelist())

        assert {
            "README.md",
            "memory.json",
            "tasks.json",
            "reminders.json",
            "conversations.json",
            "actions.json",
            "documents.json",
            "config.yaml",
        } <= names
        assert any(name.startswith("transcripts/") for name in names)

        facts = json.loads(zip_file.read("memory.json"))
        assert any("shellfish" in fact["text"] for fact in facts)

        turns = json.loads(zip_file.read("conversations.json"))
        assert any("calendar" in turn["text"] for turn in turns)


def test_a_transcript_is_readable_without_this_app(client, filled):
    """Markdown for these alone: a transcript is prose someone may want to
    read, and the summary and action items are a document rather than a row."""
    with archive(client) as zip_file:
        name = next(n for n in zip_file.namelist() if n.startswith("transcripts/"))
        text = zip_file.read(name).decode("utf-8")

    assert "Budget talk." in text
    assert "Raise it" in text
    assert "Email Ana" in text
    assert "Ana said the deposit was 1200." in text


def test_a_label_with_a_slash_does_not_become_a_directory(client, filled):
    """The label is whatever the user called the meeting. "Q3 / planning" must
    not turn into a folder, and two meetings with the same name must not
    overwrite each other -- hence the id on the end."""
    with archive(client) as zip_file:
        names = [n for n in zip_file.namelist() if n.startswith("transcripts/")]

    assert len(names) == 1
    assert names[0].count("/") == 1
    assert "Q3" in names[0]


def test_nothing_is_removed_by_exporting(client, filled):
    """It is the opposite of the wipe, and sits next to it in the UI."""
    before = len(long_term.all_facts())

    archive(client).close()

    assert len(long_term.all_facts()) == before


# -- and nothing it should not --------------------------------------------


def test_the_connectors_section_is_absent_rather_than_empty(client, workspace, config_file):
    """Removed, not emptied. `connectors: {}` leaves a reader wondering what
    was in it, and leaves a future field able to slip through because the
    section is still being copied.

    An iCal "secret address" is a credential in its own right: it grants read
    access to a whole calendar to anyone holding the URL.
    """
    raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    raw["connectors"] = {
        "calendar": [{"label": "work", "url": "https://calendar.example/secret-address"}],
        "mail": [{"label": "home", "username": "someone@example.com"}],
    }
    config_file.write_text(yaml.safe_dump(raw), encoding="utf-8")
    reset_config_cache()

    with archive(client) as zip_file:
        exported = zip_file.read("config.yaml").decode("utf-8")

    assert "connectors" not in yaml.safe_load(exported)
    assert "secret-address" not in exported
    assert "someone@example.com" not in exported


def test_the_api_token_is_not_in_the_export(client, workspace):
    """It authenticates this install. It is not a record of anything, and
    exporting it only spreads it."""
    from app import security

    token = security.token()

    with archive(client) as zip_file:
        everything = b"".join(zip_file.read(name) for name in zip_file.namelist())

    assert token.encode() not in everything


def test_the_document_text_is_not_in_the_export(client, filled):
    """documents.json lists what was indexed -- paths and counts. The text is
    a copy of the user's own files, which they already have; a second copy
    inside an export turns "here is your data" into a duplicate Documents
    folder, and a 3,000-chunk index into tens of megabytes."""
    from app import db

    db.execute(
        "INSERT INTO document_chunks(text, path, section, ordinal) VALUES(?, ?, ?, ?)",
        ("the quarterly figures are confidential", "C:/docs/x.pdf", "p1", 0),
    )

    with archive(client) as zip_file:
        everything = b"".join(zip_file.read(name) for name in zip_file.namelist())
        documents = json.loads(zip_file.read("documents.json"))

    assert b"quarterly figures are confidential" not in everything
    # The listing is still there, which is the useful half.
    assert any("lease.pdf" in entry["path"] for entry in documents)


def test_the_readme_says_what_was_left_out(client, filled):
    """An export that silently omits things is not a complete answer to "give
    me my data". Saying what is missing, and why, is."""
    with archive(client) as zip_file:
        readme = zip_file.read("README.md").decode("utf-8")

    assert "not here" in readme.lower()
    assert "Credential Manager" in readme
    assert "documents.json" in readme


# -- and it says what it will be before it is built -----------------------


def test_the_preview_counts_without_building_anything(client, filled):
    counts = client.get("/privacy/export/preview").json()["counts"]

    assert counts["memory"] == 1
    assert counts["tasks"] == 1
    assert counts["transcripts"] == 1
    assert counts["conversations"] == 2


def test_the_preview_names_what_is_excluded(client, filled):
    excludes = client.get("/privacy/export/preview").json()["excludes"]

    assert any("password" in item for item in excludes)


def test_an_empty_install_still_exports(client, workspace):
    """A fresh install has nothing in it, and "export" must not be an error
    there -- the answer is an empty record, not a failure."""
    with archive(client) as zip_file:
        assert json.loads(zip_file.read("memory.json")) == []
        assert "README.md" in zip_file.namelist()


def test_the_temporary_file_does_not_outlive_the_download(client, filled):
    """Leaving it behind would put a second copy of everything in the data
    directory, under a button about data control."""
    from app.settings import data_dir

    archive(client).close()

    leftovers = list((data_dir() / "export").glob("*.zip")) if (data_dir() / "export").exists() else []
    assert leftovers == []


def test_writing_the_export_reports_its_size(workspace, filled, tmp_path):
    """The function behind the endpoint, so the counts are checkable without
    going through HTTP."""
    result = privacy_export.write_export(tmp_path / "out.zip")

    assert result["bytes"] > 0
    assert result["counts"]["memory"] == 1
    assert zipfile.is_zipfile(tmp_path / "out.zip")

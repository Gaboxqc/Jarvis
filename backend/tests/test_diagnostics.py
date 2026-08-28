"""Logs, and what is in them — REQ-26, REQ-27.

The packaged backend has written a rotating log since the pipe-deadlock fix. It
has never told anyone. A bug report arrives with a screenshot and a sentence,
and the one file worth having sits somewhere the user was given no reason to
look.

Two things are tested here, and the second matters more than the first. Saying
where the log is, is easy. Making it a file someone can attach without handing
over their mail addresses and the shape of their home directory is the part that
has to keep working, because it fails silently and nobody finds out until the
log is already in a GitHub issue.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app import diagnostics


@pytest.fixture
def client(workspace):
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def log_file(workspace):
    directory = diagnostics.log_directory()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / diagnostics.MAIN_LOG
    path.write_text("started\n", encoding="utf-8")
    return path


# -- redaction ------------------------------------------------------------


def test_mail_addresses_are_masked():
    assert diagnostics.redact("replying to ana@example.com now") == (
        "replying to <address> now"
    )


def test_the_home_directory_becomes_a_tilde(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostics.Path, "home", classmethod(lambda cls: tmp_path))

    cleaned = diagnostics.redact(f"indexed {tmp_path}\\Documents\\lease.pdf")

    assert str(tmp_path) not in cleaned
    # The filename survives. Redacting the whole path would leave a log that
    # proves only that something was indexed.
    assert "lease.pdf" in cleaned


def test_the_home_directory_is_matched_whatever_its_capitalisation(monkeypatch, tmp_path):
    """Windows hands the same directory back cased differently depending on who
    asked, and a case-sensitive rule would mask some lines and not others."""
    monkeypatch.setattr(diagnostics.Path, "home", classmethod(lambda cls: tmp_path))

    cleaned = diagnostics.redact(f"opened {str(tmp_path).upper()}\\notes.txt")

    assert str(tmp_path).upper() not in cleaned


def test_the_filter_redacts_the_formatted_message_not_the_template():
    """`log.info("sent to %s", address)` has to be caught as surely as an
    f-string. The interesting values arrive as arguments."""
    record = logging.LogRecord(
        "kai", logging.INFO, __file__, 1, "sent to %s", ("ana@example.com",), None
    )

    diagnostics.Redactor().filter(record)

    assert "ana@example.com" not in record.getMessage()
    assert "<address>" in record.getMessage()


def test_a_line_with_nothing_to_hide_is_left_exactly_as_it_was():
    record = logging.LogRecord("kai", logging.INFO, __file__, 1, "loaded %d skills", (48,), None)

    diagnostics.Redactor().filter(record)

    assert record.getMessage() == "loaded 48 skills"


def test_a_broken_format_string_does_not_lose_the_line():
    """A filter that raises drops the record, and the records most worth having
    are the ones written while something was already going wrong."""
    record = logging.LogRecord("kai", logging.INFO, __file__, 1, "%d %d", (1,), None)

    assert diagnostics.Redactor().filter(record) is True


def test_the_file_handler_is_the_one_that_redacts():
    """The console is not filtered, and that is deliberate: someone reading
    their own terminal is not sharing anything, and a masked path is harder to
    act on."""
    import server

    config = server.sidecar_log_config("info")

    assert config["handlers"]["file"]["filters"] == ["redact"]
    assert config["filters"]["redact"]["()"] == "app.diagnostics.Redactor"


# -- what the endpoint says -----------------------------------------------


def test_the_endpoint_reports_where_the_log_is(client, log_file):
    body = client.get("/diagnostics/logs").json()

    assert body["exists"] is True
    assert body["directory"] == str(diagnostics.log_directory())
    assert [entry["name"] for entry in body["files"]] == [diagnostics.MAIN_LOG]
    assert body["total_bytes"] == log_file.stat().st_size


def test_the_endpoint_never_returns_the_contents(client, log_file):
    """A log is a file to attach, not something to render in a chat window.
    Serving the text would put a second copy somewhere it was not already."""
    log_file.write_text("secret-marker-not-for-the-api\n", encoding="utf-8")

    assert "secret-marker" not in client.get("/diagnostics/logs").text


def test_a_fresh_install_says_there_is_nothing_yet(client):
    body = client.get("/diagnostics/logs").json()

    # The UI shows "nothing logged yet" rather than a button that opens an
    # empty folder.
    assert body["exists"] is False
    assert body["files"] == []


# -- the wipe -------------------------------------------------------------


def test_delete_everything_takes_the_logs_too(client, log_file):
    """They are local data. Leaving file paths and account labels on disk under
    a button that says it removed everything is the kind of gap REQ-26 exists
    to close."""
    removed = client.post("/privacy/wipe").json()["removed"]

    assert removed["logs"] == 1
    assert log_file.read_text(encoding="utf-8") == ""


def test_the_active_log_is_truncated_rather_than_unlinked(log_file):
    """The handler is holding it open. On Windows a deleted-but-open file leaves
    the handler writing into nothing for the rest of the session."""
    diagnostics.wipe_logs()

    assert log_file.exists()
    assert log_file.stat().st_size == 0


def test_rotated_files_are_removed_outright(workspace, log_file):
    rotated = diagnostics.log_directory() / f"{diagnostics.MAIN_LOG}.1"
    rotated.write_text("older\n", encoding="utf-8")

    assert diagnostics.wipe_logs() == 2
    assert not rotated.exists()

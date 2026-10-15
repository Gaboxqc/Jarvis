"""Failures in the window, recorded where they can be read again.

The updater, the WebView and the Live2D runtime are reachable only from the
desktop shell, so when one fails the backend never hears about it. Until now
the entire account of such a failure was a sentence in a card that disappears
on the next click, plus a console line that a packaged build has no devtools
to show.

That gap cost a real investigation: an update failed on an install, and
working out why came down to asking the person to read the message back off
their screen. By then the card had been navigated away from.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(workspace):
    # Built inline like the rest of the suite; conftest attaches the bearer
    # token to every TestClient, so there is no auth plumbing here.
    with TestClient(app) as c:
        yield c


def test_a_client_failure_reaches_the_log(client, caplog):
    with caplog.at_level(logging.WARNING, logger="kai"):
        response = client.post(
            "/diagnostics/client-problem",
            json={
                "context": "updater.downloadAndInstall",
                "name": "Error",
                "message": "Authentication failed or was cancelled",
                "detail": "offered 0.4.2, running 0.4.1",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}

    written = "\n".join(record.getMessage() for record in caplog.records)
    # Every part has to survive: the context says which subsystem, the message
    # says what happened, the detail says which versions were involved. Any one
    # of them missing is another round trip to the person who hit it.
    assert "updater.downloadAndInstall" in written
    assert "Authentication failed or was cancelled" in written
    assert "offered 0.4.2, running 0.4.1" in written


def test_the_context_alone_is_enough(client, caplog):
    """A report with nothing but a context is still worth recording.

    The caller is an error handler. If it had to assemble a complete report to
    be accepted, the cases where the error is a bare string -- which is most of
    what a rejected promise carries -- would report nothing at all.
    """
    with caplog.at_level(logging.WARNING, logger="kai"):
        response = client.post(
            "/diagnostics/client-problem", json={"context": "avatar.loadCore"}
        )

    assert response.status_code == 200
    assert "avatar.loadCore" in "\n".join(r.getMessage() for r in caplog.records)


def test_it_does_not_store_anything(client):
    """A log line, not a row.

    Nothing here belongs in the database: it is diagnostic text, it goes in
    the file /diagnostics/logs points at, and it is subject to the same
    retention and the same Redactor as the rest of that file.
    """
    before = client.get("/diagnostics/logs").json()
    client.post(
        "/diagnostics/client-problem",
        json={"context": "updater.check", "message": "offline"},
    )
    after = client.get("/diagnostics/logs").json()

    assert set(before) == set(after)

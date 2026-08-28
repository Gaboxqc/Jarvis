"""The API's front door — REQ-24, REQ-26.

Written against a reproduction, not a theory. Before app/security.py existed,
this exact request succeeded:

    POST /privacy/wipe
    Origin: https://evil.example
    Content-Type: application/x-www-form-urlencoded

    -> 200, and every local record gone

No JavaScript, no preflight, no CORS violation -- a form POST is a "simple
request", so the browser sends it and only declines to show the attacker the
answer. Loopback did not help; the Action Gate did not help, because it governs
what the assistant does with a request rather than who was permitted to make
one.

So the tests here are about the door: what a caller without the token can reach
(nothing), and what a page on another origin can reach (nothing), including the
two endpoints whose loss would be worst -- the one that deletes everything, and
the one that strikes a skill off the gate permanently.
"""

from __future__ import annotations

import os
import stat

import pytest
from fastapi.testclient import TestClient

from app import db, security
from tests.conftest import TEST_API_TOKEN

EVIL = "https://evil.example"


@pytest.fixture
def client(workspace):
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def anonymous(client):
    """The same client with its credentials taken away."""
    client.headers.pop("Authorization", None)
    return client


# -- what an unauthenticated caller can reach -----------------------------


def test_the_wipe_endpoint_refuses_a_caller_with_no_token(anonymous):
    db.execute(
        "INSERT INTO tasks(id, text, kind, created_at) VALUES(?, ?, 'task', ?)",
        ("keep-me", "still here afterwards", db.now()),
    )

    response = anonymous.post("/privacy/wipe")

    assert response.status_code == 401
    assert db.query_one("SELECT id FROM tasks WHERE id = 'keep-me'") is not None


def test_a_standing_approval_cannot_be_granted_without_the_token(anonymous):
    """The worst one to lose. A pre-approval removes a skill from the gate for
    good, so this is the endpoint that turns one open request into every
    future action running unasked."""
    response = anonymous.post("/actions/pre-approvals/system.launch_app")

    assert response.status_code == 401
    from app.actions import gate

    assert "system.launch_app" not in gate.pre_approved_skills()


def test_reading_is_refused_too(anonymous):
    """Not only writes. /memory and /capture/transcripts are the user's own
    words; an unauthenticated reader of those is a leak, not an inconvenience."""
    for path in ("/memory", "/health", "/capture/transcripts", "/actions/history"):
        assert anonymous.get(path).status_code == 401, path


def test_a_wrong_token_is_refused(client):
    client.headers["Authorization"] = "Bearer not-the-token"
    assert client.get("/memory").status_code == 401


def test_a_bare_token_without_the_scheme_is_refused(client):
    client.headers["Authorization"] = TEST_API_TOKEN
    assert client.get("/memory").status_code == 401


def test_the_right_token_still_gets_through(client):
    assert client.get("/memory").status_code == 200


# -- what another origin can reach ----------------------------------------


def test_the_reproduction_is_refused(anonymous):
    """The precise shape that worked before: a form POST from a web page."""
    response = anonymous.post(
        "/privacy/wipe",
        data={"anything": "1"},
        headers={"Origin": EVIL},
    )

    assert response.status_code == 403


def test_a_foreign_origin_is_refused_even_holding_the_token(client):
    """Belt and braces. If the token ever leaks -- a screenshot, a shared log --
    the origin check is what still stands between a web page and the API."""
    response = client.post("/privacy/wipe", headers={"Origin": EVIL})

    assert response.status_code == 403


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:5173",       # the Vite dev server
        "http://127.0.0.1:5173",
        "http://tauri.localhost",      # Windows WebView2 serves the packaged app here
        "tauri://localhost",           # macOS and Linux
    ],
)
def test_the_app_s_own_origins_are_allowed(client, origin):
    assert client.get("/memory", headers={"Origin": origin}).status_code == 200


def test_a_caller_with_no_origin_is_allowed(client):
    """curl, the CLI, a test. They send no Origin and hold the token, which is
    the control that applies to them."""
    assert "Origin" not in client.headers
    assert client.get("/memory").status_code == 200


# -- the token itself -----------------------------------------------------


def test_the_environment_overrides_the_file(workspace, monkeypatch):
    monkeypatch.setenv(security.API_TOKEN_ENV, "from-the-environment")
    security.reset()
    try:
        assert security.token() == "from-the-environment"
        assert not security.token_file().exists()
    finally:
        security.reset()


def test_a_token_is_created_once_and_then_reused(workspace, monkeypatch):
    monkeypatch.delenv(security.API_TOKEN_ENV, raising=False)
    security.reset()
    try:
        first = security.token()
        assert first and len(first) >= 32
        assert security.token_file().read_text(encoding="utf-8").strip() == first

        # A second process, same data directory: it must not mint a new one, or
        # the desktop shell and the backend would each hold a different token.
        security.reset()
        assert security.token() == first
    finally:
        security.reset()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes; Windows uses the ACL on LOCALAPPDATA")
def test_the_token_file_is_not_readable_by_anyone_else(workspace, monkeypatch):
    monkeypatch.delenv(security.API_TOKEN_ENV, raising=False)
    security.reset()
    try:
        security.token()
        mode = stat.S_IMODE(security.token_file().stat().st_mode)
        assert mode & (stat.S_IRGRP | stat.S_IROTH) == 0
    finally:
        security.reset()


def test_an_unwritable_data_directory_still_yields_a_working_token(workspace, monkeypatch):
    """A backend that cannot serve is worse than one whose token no other
    process can look up."""
    monkeypatch.delenv(security.API_TOKEN_ENV, raising=False)

    def refuse(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(security.os, "open", refuse)
    security.reset()
    try:
        assert security.token()
    finally:
        security.reset()


def test_the_comparison_rejects_the_empty_and_the_absent():
    for header in (None, "", "Bearer", "Bearer ", "Basic " + TEST_API_TOKEN):
        assert security.authorizes(header) is False, header


def test_the_cors_policy_and_the_middleware_share_one_definition():
    """Two places deciding what "local" means is two places to get it wrong."""
    from app import main

    for route_middleware in main.app.user_middleware:
        if route_middleware.cls.__name__ == "CORSMiddleware":
            assert (
                route_middleware.kwargs["allow_origin_regex"]
                == security.ALLOWED_ORIGIN_PATTERN
            )
            break
    else:
        pytest.fail("the CORS middleware is not installed")

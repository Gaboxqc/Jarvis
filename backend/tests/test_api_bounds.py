"""What the API refuses to be asked for — REQ-27.

The token (app/security.py) decides *who* may call. This is about what a caller
holding it may ask for, which is a separate question and was unanswered: every
`limit` was a bare int with a default, so `?limit=100000000` was a valid request
that would read a whole table into memory and serialise it.

Nobody was going to send that on purpose. A UI that computes a page size, a
script with an off-by-a-lot, or a retry loop that doubles something are all
ordinary mistakes, and on a local assistant the blast radius of one is the
machine the user is working on.

422 rather than a clamp, deliberately. Silently honouring a different request
than the one made is how a caller comes to believe it has the whole history
when it has the first two hundred rows.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(workspace):
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    "path",
    ["/actions/history?limit={}", "/documents/search?q=lease&limit={}"],
)
@pytest.mark.parametrize("limit", [0, -1, 100_000_000])
def test_an_unreasonable_limit_is_refused(client, path, limit):
    assert client.get(path.format(limit)).status_code == 422


@pytest.mark.parametrize(
    "path",
    ["/actions/history?limit=25", "/documents/search?q=lease&limit=5"],
)
def test_an_ordinary_limit_still_works(client, path):
    assert client.get(path).status_code == 200


def test_the_upper_bound_itself_is_allowed(client):
    """The bound is inclusive. A caller asking for exactly the maximum is
    asking a legitimate question, and refusing it would make the documented
    limit a number that cannot be used."""
    assert client.get("/actions/history?limit=200").status_code == 200


def test_an_empty_search_is_refused_rather_than_run(client):
    """FTS5 is happy to be handed nothing and match nothing. Saying so is more
    use than an empty result set that looks like an answer."""
    assert client.get("/documents/search?q=").status_code == 422


def test_an_enormous_search_string_is_refused(client):
    """The query goes to a query planner, not to a LIKE. Length is the input
    that decides how long it runs for."""
    assert client.get("/documents/search?q=" + "a" * 5000).status_code == 422

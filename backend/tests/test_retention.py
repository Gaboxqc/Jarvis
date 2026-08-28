"""How long the record is kept — REQ-26.

`conversation_turns` and `action_records` grew without bound, and the only
control over either was "delete everything". Those rows are not abstract: the
parameters of a mail action are a subject line and an address, the result of a
file action is a list of the user's own paths.

Most of what is worth testing here is what the sweep refuses to touch. Deleting
old rows is easy; the failure that would matter is a retention window quietly
eating a memory the user asked to keep, or a confirmation they have not answered
yet.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import yaml
from fastapi.testclient import TestClient

from app import db, retention
from app.actions import journal
from app.settings import reset_config_cache


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(
        microsecond=0
    ).isoformat()


def turn(days_old: int, text: str = "something said") -> str:
    row_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO conversation_turns(id, session_id, role, text, ts) "
        "VALUES(?, 'ui', 'user', ?, ?)",
        (row_id, text, _ago(days_old)),
    )
    return row_id


def action(days_old: int, status: str = journal.STATUS_EXECUTED) -> str:
    row_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO action_records(id, batch_id, skill_name, severity, status, created_at) "
        "VALUES(?, ?, 'mail.send', 'consequential', ?, ?)",
        (row_id, str(uuid.uuid4()), status, _ago(days_old)),
    )
    return row_id


def set_window(config_file, **values) -> None:
    raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    raw["retention"] = values
    config_file.write_text(yaml.safe_dump(raw), encoding="utf-8")
    reset_config_cache()


@pytest.fixture
def client(workspace):
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


# -- what goes ------------------------------------------------------------


def test_conversation_turns_past_the_window_are_removed(workspace, config_file):
    set_window(config_file, conversation_days=30, history_days=30)
    old, recent = turn(60), turn(2)

    removed = retention.sweep()

    assert removed["conversation_turns"] == 1
    assert db.query_one("SELECT id FROM conversation_turns WHERE id = ?", (old,)) is None
    assert db.query_one("SELECT id FROM conversation_turns WHERE id = ?", (recent,)) is not None


def test_finished_actions_past_the_window_are_removed(workspace, config_file):
    set_window(config_file, conversation_days=30, history_days=30)
    old, recent = action(60), action(2)

    removed = retention.sweep()

    assert removed["action_records"] == 1
    assert journal.get(old) is None
    assert journal.get(recent) is not None


# -- what stays -----------------------------------------------------------


def test_a_pending_confirmation_is_never_swept_by_age(workspace, config_file):
    """A question the user has not answered yet. Expiring one by age is the
    gate's job, with its own TTL and its own reasons -- and a retention setting
    quietly consuming it would make a confirmation disappear rather than expire.
    """
    set_window(config_file, conversation_days=1, history_days=1)
    pending = action(400, status=journal.STATUS_PENDING)

    retention.sweep()

    assert journal.get(pending) is not None


@pytest.mark.parametrize("table, insert", [
    ("memory_facts",
     "INSERT INTO memory_facts(id, text, created_at) VALUES('m', 'allergic to peanuts', ?)"),
    ("tasks",
     "INSERT INTO tasks(id, text, created_at) VALUES('t', 'renew the lease', ?)"),
    ("transcripts",
     "INSERT INTO transcripts(id, label, started_at) VALUES('r', 'standup', ?)"),
])
def test_what_the_user_asked_to_keep_is_kept(workspace, config_file, table, insert):
    """Every one of these is something the user explicitly asked for, is listed
    in the app, and is individually deletable. Removing them on a timer would be
    the assistant throwing away work it was told to hold on to."""
    set_window(config_file, conversation_days=1, history_days=1)
    db.execute(insert, (_ago(500),))

    retention.sweep()

    assert db.query_one(f"SELECT COUNT(*) AS c FROM {table}")["c"] == 1


def test_zero_keeps_everything(workspace, config_file):
    """The honest spelling of "off". Someone who wants the whole record should
    not have to pick a number large enough never to arrive."""
    set_window(config_file, conversation_days=0, history_days=0)
    turn(5000)
    action(5000)

    removed = retention.sweep()

    assert removed == {"conversation_turns": 0, "action_records": 0}
    assert db.query_one("SELECT COUNT(*) AS c FROM conversation_turns")["c"] == 1
    assert db.query_one("SELECT COUNT(*) AS c FROM action_records")["c"] == 1


def test_the_default_is_ninety_days_not_forever(workspace):
    from app.settings import RetentionSettings

    assert RetentionSettings().conversation_days == 90
    assert RetentionSettings().history_days == 90


# -- through the API ------------------------------------------------------


def test_the_status_endpoint_reports_the_window_and_what_is_in_it(client, config_file):
    set_window(config_file, conversation_days=45, history_days=15)
    turn(1)
    turn(2)
    action(1)

    body = client.get("/privacy/retention").json()

    assert body["conversation_days"] == 45
    assert body["history_days"] == 15
    assert body["conversation_turns"] == 2
    assert body["action_records"] == 1


def test_the_sweep_can_be_asked_for_now(client, config_file):
    """Shortening the window has to visibly do something. Saving "30 days" and
    being told nothing happened until the next restart reads as a setting that
    did not take."""
    set_window(config_file, conversation_days=30, history_days=30)
    turn(90)

    removed = client.post("/privacy/retention/sweep").json()["removed"]

    assert removed["conversation_turns"] == 1


def test_the_window_is_writable_from_the_privacy_screen(client, config_file):
    response = client.patch(
        "/settings", json={"changes": {"retention": {"conversation_days": 7}}}
    )

    assert response.status_code == 200
    reset_config_cache()
    from app.settings import load_config

    assert load_config().retention.conversation_days == 7


def test_a_negative_window_is_read_as_keep_forever(workspace, config_file):
    """Rather than as "delete everything older than the future"."""
    set_window(config_file, conversation_days=-5, history_days=-5)
    turn(5000)

    assert retention.sweep()["conversation_turns"] == 0

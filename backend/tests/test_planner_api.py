"""Tasks and reminders over the API — REQ-9, REQ-10.

These existed only as skills, which meant the only way to tick something off
was to type a sentence and hope the router understood it. The screen that
replaces that needs plain CRUD.

Not going through the Action Gate is deliberate and is the thing worth pinning:
the gate exists so the assistant cannot act without the user knowing, and a
person pressing "Done" on a row they are looking at already knows. What must
stay true is that the two paths agree — the same list, the same Markdown
mirror — so the model and the user never see different task lists.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.settings import data_dir
from app.skills.base import SkillContext
from app.skills.planning import tasks as task_store


@pytest.fixture
def client(workspace):
    with TestClient(app) as c:
        yield c


# -- tasks -----------------------------------------------------------------


def test_a_task_can_be_added_and_listed(client):
    created = client.post("/tasks", json={"text": "buy milk"}).json()
    assert created["text"] == "buy milk"

    listed = client.get("/tasks").json()["tasks"]
    assert [t["text"] for t in listed] == ["buy milk"]
    assert listed[0]["done"] is False


def test_tags_are_extracted_like_the_skill_does(client):
    created = client.post("/tasks", json={"text": "renew passport #admin"}).json()
    assert created["text"] == "renew passport"
    assert created["tags"] == ["admin"]


def test_a_task_can_be_completed_and_reopened(client):
    task = client.post("/tasks", json={"text": "buy milk"}).json()

    assert client.patch(f"/tasks/{task['id']}?done=true").json()["done"] is True
    assert client.get("/tasks").json()["tasks"][0]["done"] is True

    # Reopening matters: "done" is the easiest button to press by accident.
    assert client.patch(f"/tasks/{task['id']}?done=false").json()["done"] is False
    assert client.get("/tasks").json()["tasks"][0]["done"] is False


def test_completed_tasks_can_be_hidden(client):
    first = client.post("/tasks", json={"text": "done one"}).json()
    client.post("/tasks", json={"text": "still open"})
    client.patch(f"/tasks/{first['id']}?done=true")

    open_only = client.get("/tasks?include_done=false").json()["tasks"]
    assert [t["text"] for t in open_only] == ["still open"]


def test_a_task_can_be_deleted(client):
    task = client.post("/tasks", json={"text": "buy milk"}).json()
    assert client.delete(f"/tasks/{task['id']}").status_code == 200
    assert client.get("/tasks").json()["tasks"] == []


def test_an_empty_task_is_refused(client):
    assert client.post("/tasks", json={"text": "   "}).status_code == 400


@pytest.mark.parametrize("call", ["patch", "delete"])
def test_acting_on_a_missing_task_is_404(client, call):
    response = getattr(client, call)("/tasks/does-not-exist")
    assert response.status_code == 404


# -- the two paths must not diverge ---------------------------------------


def test_the_api_and_the_skill_share_one_list(client):
    """A task added by clicking must be visible to the model, and vice versa."""
    client.post("/tasks", json={"text": "added by clicking"})
    task_store.AddTaskSkill().run({"text": "added by asking"}, SkillContext())

    from_api = {t["text"] for t in client.get("/tasks").json()["tasks"]}
    assert from_api == {"added by clicking", "added by asking"}


def test_the_markdown_mirror_is_written_by_the_api_too(client):
    """REQ-10: the list stays readable if Kai is uninstalled tomorrow.

    The mirror is written by the skills. An API that wrote to SQLite without it
    would leave the file silently stale — the exact failure the mirror exists to
    prevent.
    """
    client.post("/tasks", json={"text": "buy milk"})

    mirror = data_dir() / task_store.MIRROR_NAME
    assert mirror.exists()
    assert "buy milk" in mirror.read_text(encoding="utf-8")


def test_completing_through_the_api_updates_the_mirror(client):
    task = client.post("/tasks", json={"text": "buy milk"}).json()
    client.patch(f"/tasks/{task['id']}?done=true")

    text = (data_dir() / task_store.MIRROR_NAME).read_text(encoding="utf-8")
    assert "- [x] buy milk" in text


# -- reminders -------------------------------------------------------------


def test_reminders_are_listed(client):
    from app.skills.planning.reminders import AddReminderSkill

    AddReminderSkill().run({"what": "call the dentist", "when": "in 2 hours"}, SkillContext())

    reminders = client.get("/reminders").json()["reminders"]
    assert any("dentist" in r["label"] for r in reminders)


def test_a_reminder_can_be_cancelled(client):
    from app.skills.planning.reminders import AddReminderSkill

    AddReminderSkill().run({"what": "call the dentist", "when": "in 2 hours"}, SkillContext())
    reminder = client.get("/reminders").json()["reminders"][0]

    assert client.delete(f"/reminders/{reminder['id']}").status_code == 200
    assert client.get("/reminders").json()["reminders"] == []


def test_cancelling_a_missing_reminder_is_404(client):
    assert client.delete("/reminders/nope").status_code == 404

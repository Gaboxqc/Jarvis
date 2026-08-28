"""Connectors — REQ-8, REQ-11, REQ-13, REQ-14, REQ-24, REQ-26, REQ-27.

No test here touches a real mail server or calendar. Connectors are stubbed at
the module boundary; what is under test is the contract around them — that
secrets never land in config, that sending always confirms, and that one dead
source doesn't take the briefing down with it.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from app.actions import gate
from app.connectors import base as connectors
from app.connectors import credentials
from app.connectors import mail
from app.settings import reset_config_cache
from app.skills.base import SkillContext

ICS_TEMPLATE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:evt-1
SUMMARY:Standup
DTSTART:{start1}
DTEND:{end1}
LOCATION:Meet
END:VEVENT
BEGIN:VEVENT
UID:evt-2
SUMMARY:Dentist
DTSTART:{start2}
DTEND:{end2}
END:VEVENT
END:VCALENDAR
"""


def _utc_stamp(hour: int, minute: int = 0) -> str:
    """A UTC timestamp for today at a given *local* hour.

    The agenda covers the local day, so an ICS built from the UTC date is wrong
    whenever the two disagree — which is most of the day for anyone west of
    Greenwich. Generating from local time is also what a real calendar does.
    """
    local_today = datetime.now().astimezone().replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    return local_today.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_ics(folder: Path) -> Path:
    path = folder / "cal.ics"
    path.write_text(
        ICS_TEMPLATE.format(
            start1=_utc_stamp(9), end1=_utc_stamp(9, 15),
            start2=_utc_stamp(14), end2=_utc_stamp(15),
        ),
        encoding="utf-8",
    )
    return path


def configure(config_file: Path, **sections) -> None:
    raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    raw.setdefault("connectors", {})
    raw["connectors"].update(sections)
    config_file.write_text(yaml.safe_dump(raw), encoding="utf-8")
    reset_config_cache()


class FakeMessage:
    """Stands in for connectors.mail.Message without an IMAP server."""

    def __init__(self, uid, sender, subject, snippet="", needs_reply=False, automated=False):
        self.uid = uid
        self.sender = sender
        self.sender_name = sender.split("@")[0]
        self.subject = subject
        self.snippet = snippet
        self.body = snippet
        self.date = datetime.now(timezone.utc)
        self.unread = True
        self._needs_reply = needs_reply
        self._automated = automated

    @property
    def probably_needs_reply(self):
        return self._needs_reply

    @property
    def looks_automated(self):
        return self._automated

    def describe(self):
        return f"{self.sender_name} - {self.subject}"

    def to_dict(self):
        return {"uid": self.uid, "from": self.sender, "subject": self.subject}


# -- secrets never touch config (REQ-26) ----------------------------------


def test_config_holds_a_reference_never_a_secret(workspace, config_file):
    configure(config_file, mail=[{
        "label": "work", "provider": "imap", "host": "imap.example.com",
        "username": "me@example.com",
    }])

    entry = connectors.find("mail", "work")
    rendered = entry.to_dict()

    assert entry.credential_ref == "mail:work"
    # Nothing that could carry a password appears in the serialised form.
    assert "password" not in rendered
    assert "secret" not in rendered
    assert set(rendered) == {
        "kind", "label", "provider", "target", "username", "writable",
        "enabled", "credential_stored", "credential_ref",
    }


def test_the_config_file_itself_never_gains_a_password(workspace, config_file, monkeypatch):
    saved: dict[str, str] = {}
    monkeypatch.setattr(credentials, "store", lambda ref, secret: saved.__setitem__(ref, secret))

    configure(config_file, mail=[{
        "label": "work", "provider": "imap", "host": "h", "username": "u",
    }])
    credentials.store("mail:work", "hunter2")

    text = config_file.read_text(encoding="utf-8")
    assert "hunter2" not in text
    assert saved["mail:work"] == "hunter2"


def test_a_missing_password_says_how_to_add_it(workspace, config_file, monkeypatch):
    monkeypatch.setattr(credentials, "fetch", lambda ref: None)
    configure(config_file, mail=[{
        "label": "work", "provider": "imap", "host": "h", "username": "u",
    }])

    with pytest.raises(connectors.AuthFailed, match="/connect mail work"):
        connectors.find("mail", "work").secret()


def test_credentials_module_never_logs_a_secret():
    """A password in a log file is a password on disk."""
    source = inspect.getsource(credentials)

    for line in source.splitlines():
        if "log." in line:
            assert "secret" not in line, f"credentials.py logs a secret: {line.strip()}"


def test_an_unusable_keyring_backend_is_reported_not_trusted(monkeypatch):
    """The 'fail' and 'null' backends discard writes silently."""

    class NullKeyring:
        pass

    NullKeyring.__name__ = "NullKeyring"

    class FakeKeyringModule:
        @staticmethod
        def get_keyring():
            return NullKeyring()

    monkeypatch.setattr(credentials, "_keyring", lambda: FakeKeyringModule)

    status = credentials.status()
    assert status.available is False
    assert "credential store" in status.detail.lower()


# -- not configured is not a fault (REQ-8, REQ-13, REQ-27) ----------------


def test_no_calendar_configured_offers_setup(workspace):
    with pytest.raises(connectors.NotConfigured, match="kai.config.yaml"):
        connectors.require("calendar")


def test_asking_the_agenda_without_a_calendar_explains_itself(workspace):
    outcome = gate.submit("calendar.agenda", {"when": "today"}, SkillContext())

    assert outcome.status == gate.FAILED
    assert "No calendar is connected" in (outcome.error or "")


def test_an_unknown_account_label_lists_the_real_ones(workspace, config_file):
    configure(config_file, mail=[{"label": "work", "provider": "imap", "host": "h"}])

    with pytest.raises(connectors.ConnectorError, match="I have: work"):
        connectors.find("mail", "personal")


# -- calendar reading (REQ-8) ---------------------------------------------


def test_events_are_read_from_an_ics_file(workspace, config_file, tmp_path):
    path = write_ics(tmp_path)
    configure(config_file, calendar=[{"label": "personal", "provider": "ics", "url": str(path)}])

    outcome = gate.submit("calendar.agenda", {"when": "today"}, SkillContext())

    assert outcome.status == gate.EXECUTED
    assert "Standup" in outcome.message
    assert "Dentist" in outcome.message


def test_a_broken_calendar_is_named_not_hidden(workspace, config_file, tmp_path):
    """REQ-27 — one dead source must not silently empty the agenda."""
    good = write_ics(tmp_path)
    configure(config_file, calendar=[
        {"label": "personal", "provider": "ics", "url": str(good)},
        {"label": "broken", "provider": "ics", "url": str(tmp_path / "missing.ics")},
    ])

    outcome = gate.submit("calendar.agenda", {"when": "today"}, SkillContext())

    assert outcome.status == gate.EXECUTED
    assert "Standup" in outcome.message          # the working one still shows
    assert "broken" in outcome.message           # the failure is named


def test_free_slots_are_found_between_events(workspace, config_file, tmp_path):
    path = write_ics(tmp_path)
    configure(config_file, calendar=[{"label": "personal", "provider": "ics", "url": str(path)}])

    outcome = gate.submit("calendar.find_free_time", {"when": "today", "minutes": 60},
                          SkillContext())

    assert outcome.status == gate.EXECUTED
    assert outcome.result.data["slots"]


def test_a_read_only_calendar_refuses_writes(workspace, config_file, tmp_path):
    path = write_ics(tmp_path)
    configure(config_file, calendar=[{"label": "personal", "provider": "ics", "url": str(path)}])

    parked = gate.submit(
        "calendar.create_event", {"title": "Lunch", "when": "tomorrow at 13:00"}, SkillContext()
    )
    assert parked.status == gate.NEEDS_CONFIRMATION

    result = gate.confirm(parked.action_id, SkillContext())
    assert result.status == gate.FAILED
    assert "read-only" in (result.error or "")


def test_creating_an_event_previews_the_resolved_time(workspace, config_file, tmp_path):
    """REQ-8 — the user has to see what 'thursday at 3' turned into."""
    path = write_ics(tmp_path)
    configure(config_file, calendar=[
        {"label": "work", "provider": "caldav", "url": "https://example.com",
         "username": "u", "writable": True},
        {"label": "personal", "provider": "ics", "url": str(path)},
    ])

    outcome = gate.submit(
        "calendar.create_event",
        {"title": "Dentist", "when": "tomorrow at 15:00", "minutes": 30},
        SkillContext(),
    )

    assert outcome.status == gate.NEEDS_CONFIRMATION
    assert "15:00" in outcome.preview
    assert "15:30" in outcome.preview      # the end time too
    assert "Dentist" in outcome.preview
    assert "work" in outcome.preview       # and which calendar


def test_an_unparseable_time_is_refused_before_confirmation(workspace, config_file):
    configure(config_file, calendar=[
        {"label": "work", "provider": "caldav", "url": "https://x", "writable": True},
    ])

    outcome = gate.submit(
        "calendar.create_event", {"title": "Thing", "when": "sometime soonish"}, SkillContext()
    )

    assert outcome.status == gate.FAILED
    assert "couldn't work out when" in (outcome.error or "")


# -- mail triage (REQ-13) -------------------------------------------------


def test_inbox_splits_what_needs_a_reply(workspace, config_file, monkeypatch):
    configure(config_file, mail=[{"label": "work", "provider": "imap", "host": "h"}])
    monkeypatch.setattr(mail, "fetch_unread", lambda cfg, limit=25, days=14: [
        FakeMessage("1", "ana@example.com", "Can you review the contract?", needs_reply=True),
        FakeMessage("2", "noreply@shop.com", "Your receipt", automated=True),
        FakeMessage("3", "bob@example.com", "FYI notes from today"),
    ])

    outcome = gate.submit("mail.inbox", {}, SkillContext())

    assert outcome.status == gate.EXECUTED
    assert "needs a reply" in outcome.message
    assert "Can you review the contract?" in outcome.message
    assert "1 automated" in outcome.message
    assert outcome.result.data["needs_reply"] == ["1"]


def test_reading_mail_does_not_mark_it_read():
    """BODY.PEEK, not BODY — summarising must not change inbox state."""
    source = inspect.getsource(mail)

    assert "BODY.PEEK" in source
    assert '"(BODY[])"' not in source


def test_expired_credentials_are_distinguished_from_a_network_fault():
    """The reply for 'password rejected' is re-entry; for 'server down' it is not."""
    assert issubclass(connectors.AuthFailed, connectors.ConnectorError)
    assert issubclass(connectors.NotConfigured, connectors.ConnectorError)
    assert not issubclass(connectors.AuthFailed, connectors.NotConfigured)


# -- sending (REQ-14) -----------------------------------------------------


def test_drafting_sends_nothing(workspace, config_file, monkeypatch):
    configure(config_file, mail=[{"label": "work", "provider": "imap", "host": "h"}])
    sent: list = []
    monkeypatch.setattr(mail, "send", lambda cfg, msg: sent.append(msg))

    outcome = gate.submit(
        "mail.draft",
        {"to": "ana@example.com", "subject": "Contract", "body": "Looks good to me."},
        SkillContext(),
    )

    assert outcome.status == gate.EXECUTED
    assert sent == []
    assert outcome.result.data["sent"] is False


def test_sending_always_asks_first(workspace, config_file, monkeypatch):
    configure(config_file, mail=[{"label": "work", "provider": "imap", "host": "h",
                                  "username": "me@example.com"}])
    sent: list = []
    monkeypatch.setattr(mail, "send", lambda cfg, msg: sent.append(msg))

    outcome = gate.submit(
        "mail.send",
        {"to": "ana@example.com", "subject": "Contract", "body": "Looks good."},
        SkillContext(),
    )

    assert outcome.status == gate.NEEDS_CONFIRMATION
    assert "ana@example.com" in outcome.preview
    assert "cannot be recalled" in outcome.preview
    assert outcome.reversible is False
    assert sent == []


def test_sending_can_never_be_pre_approved(workspace, config_file, monkeypatch):
    """REQ-14 — confirmation is per message; no standing approval satisfies it.

    Without mail.send opting out, the pre-approval mechanism would be a
    legitimate-looking way to make mail send silently.
    """
    configure(config_file, mail=[{"label": "work", "provider": "imap", "host": "h"}])
    sent: list = []
    monkeypatch.setattr(mail, "send", lambda cfg, msg: sent.append(msg))

    gate.grant_pre_approval("mail.send")

    outcome = gate.submit(
        "mail.send", {"to": "a@b.com", "subject": "x", "body": "y"}, SkillContext()
    )

    assert outcome.status == gate.NEEDS_CONFIRMATION
    assert sent == []


def test_confirming_one_message_does_not_send_a_second(workspace, config_file, monkeypatch):
    configure(config_file, mail=[{"label": "work", "provider": "imap", "host": "h",
                                  "username": "me@example.com"}])
    sent: list = []
    monkeypatch.setattr(mail, "send", lambda cfg, msg: sent.append(str(msg["To"])))

    first = gate.submit("mail.send", {"to": "one@example.com", "subject": "a", "body": "b"},
                        SkillContext())
    second = gate.submit("mail.send", {"to": "two@example.com", "subject": "c", "body": "d"},
                         SkillContext())

    gate.confirm(first.action_id, SkillContext())

    assert sent == ["one@example.com"]
    from app.actions import journal

    assert journal.get(second.action_id).status == journal.STATUS_PENDING


def test_a_bad_address_is_refused(workspace, config_file, monkeypatch):
    configure(config_file, mail=[{"label": "work", "provider": "imap", "host": "h"}])
    monkeypatch.setattr(mail, "send", lambda cfg, msg: None)

    parked = gate.submit("mail.send", {"to": "not-an-address", "subject": "x", "body": "y"},
                         SkillContext())
    result = gate.confirm(parked.action_id, SkillContext())

    assert result.status == gate.FAILED
    assert "doesn't look like an email address" in (result.error or "")


# -- marking (REQ-13, REQ-24) ---------------------------------------------


def test_marking_a_few_messages_is_routine_but_bulk_asks(workspace, config_file, monkeypatch):
    configure(config_file, mail=[{"label": "work", "provider": "imap", "host": "h"}])
    from app.skills.registry import get_skill

    skill = get_skill("mail.mark")

    monkeypatch.setattr(mail, "search_messages",
                        lambda cfg, q, limit=50: [FakeMessage(str(i), "a@b.com", "s")
                                                  for i in range(2)])
    assert skill.severity({"query": "x", "action": "read"}) == "routine"

    monkeypatch.setattr(mail, "search_messages",
                        lambda cfg, q, limit=50: [FakeMessage(str(i), "a@b.com", "s")
                                                  for i in range(40)])
    assert skill.severity({"query": "x", "action": "read"}) == "consequential"


# -- briefing (REQ-11, REQ-27) --------------------------------------------


def test_briefing_includes_tasks_and_reminders(workspace):
    from app.scheduler import store as sched_store
    from app.skills.planning.briefing import build

    gate.submit("planning.add_task", {"text": "renew the passport"}, SkillContext())
    sched_store.add(kind="reminder", label="call the bank",
                    fire_at=datetime.now(timezone.utc) + timedelta(hours=2))

    sections = {s.name: s for s in build(("tasks", "reminders"))}

    assert any("passport" in line for line in sections["tasks"].lines)
    assert any("call the bank" in line for line in sections["reminders"].lines)


def test_a_failing_section_is_reported_not_dropped(workspace, monkeypatch):
    """REQ-11 — 'nothing needs you' must never be a lie caused by a timeout."""
    from app.skills.planning import briefing

    def explode():
        raise RuntimeError("imap is down")

    monkeypatch.setitem(briefing.BUILDERS, "mail", explode)

    sections = {s.name: s for s in briefing.build(("tasks", "mail"))}

    assert sections["mail"].error == "imap is down"
    assert "couldn't check" in sections["mail"].render()


def test_briefing_keeps_the_configured_order(workspace):
    from app.skills.planning.briefing import build

    order = tuple(s.name for s in build(("mail", "tasks", "calendar")))

    assert order == ("mail", "tasks", "calendar")


def test_briefing_survives_having_no_connectors_at_all(workspace):
    outcome = gate.submit("planning.briefing", {}, SkillContext())

    assert outcome.status == gate.EXECUTED
    assert outcome.message

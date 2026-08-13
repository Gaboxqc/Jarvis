"""Adding an account — REQ-13, REQ-26.

Most of this file is about what the path refuses, because that is what makes it
acceptable at all. Connectors were deliberately excluded from
preferences.WRITABLE — they decide what leaves the machine — so a second way to
write them is only defensible while it stays narrow.

Two lines must hold:

  no secret ever reaches the config file -- not a password, and not an ics
  calendar URL, which is a bearer credential wearing a URL's clothes

The rest is ordinary validation, plus the same comment-preservation guarantee
the settings writer has: the config's comments are its only documentation.
"""

from __future__ import annotations

import pytest
import yaml

from app.connectors import setup
from app.settings import load_config, reset_config_cache


@pytest.fixture
def config_with_connectors(workspace, config_file):
    config_file.write_text(
        "# Kai configuration\n"
        "persona:\n"
        "  name: Kai            # what it calls itself\n"
        "connectors:\n"
        "  # No password is ever written here.\n"
        "  calendar: []\n"
        "  mail: []\n"
        "privacy:\n"
        "  allow_web_search: true\n",
        encoding="utf-8",
    )
    reset_config_cache()
    return config_file


def accounts(path, kind):
    return (yaml.safe_load(path.read_text(encoding="utf-8"))["connectors"] or {}).get(kind) or []


# -- the refusals ----------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["password", "pass", "app_password", "PASSWORD", "secret", "token", "api_key", "credential"],
)
def test_a_password_is_never_written_to_the_config(config_with_connectors, field):
    with pytest.raises(setup.SetupError, match="Credential Manager"):
        setup.add_account("mail", "imap", {
            "label": "gmail", "host": "imap.gmail.com",
            "username": "me@gmail.com", field: "hunter2",
        })

    assert accounts(config_with_connectors, "mail") == []
    assert "hunter2" not in config_with_connectors.read_text(encoding="utf-8")


def test_an_ical_address_never_lands_in_the_config_file(config_with_connectors):
    """Google's "secret address" is a bearer credential in a URL's clothes.

    Anyone holding it reads the whole calendar without logging in. It used to be
    refused outright and left to hand-editing; it is now addable from the UI,
    which is strictly better *provided* the address goes to the OS credential
    store instead of the config file. `url` is not an allowed field for ics, so
    there is no way to write one here even by asking.
    """
    with pytest.raises(setup.SetupError, match="Not something a ics account has"):
        setup.add_account("calendar", "ics", {
            "label": "personal",
            "url": "https://calendar.google.com/calendar/ical/abc123secret/basic.ics",
        })
    assert "abc123secret" not in config_with_connectors.read_text(encoding="utf-8")


def test_an_ics_calendar_is_added_with_no_address_in_the_file(config_with_connectors):
    setup.add_account("calendar", "ics", {"label": "personal"})

    entry = accounts(config_with_connectors, "calendar")[0]
    assert entry == {"provider": "ics", "label": "personal"}
    # Nothing resembling an address: the config entry only says the calendar
    # exists. The address arrives separately as a credential.
    assert "url" not in entry


def test_the_ics_address_is_read_from_the_credential_store(config_with_connectors, monkeypatch):
    from app.connectors import base, credentials

    setup.add_account("calendar", "ics", {"label": "personal"})
    secret = "https://calendar.google.com/calendar/ical/abc123secret/basic.ics"
    monkeypatch.setattr(credentials, "fetch", lambda ref: secret)

    account = base.find("calendar", "personal")
    assert account.resolved_url == secret
    assert account.needs_credential is True


def test_a_hand_edited_ics_url_still_works(workspace, config_file):
    """Existing calendars were configured with the URL in the file.

    Moving where the address lives must not silently break a calendar that was
    set up before the move.
    """
    from app.connectors import base
    from app.settings import reset_config_cache

    config_file.write_text(
        "connectors:\n"
        "  calendar:\n"
        "    - label: old\n"
        "      provider: ics\n"
        "      url: https://example.com/legacy.ics\n"
        "  mail: []\n",
        encoding="utf-8",
    )
    reset_config_cache()

    assert base.find("calendar", "old").resolved_url == "https://example.com/legacy.ics"


def test_an_unknown_field_is_refused_rather_than_dropped(config_with_connectors):
    """Silently ignoring a field is how an account ends up half-configured."""
    with pytest.raises(setup.SetupError, match="oauth_token|Not something"):
        setup.add_account("mail", "imap", {
            "label": "x", "host": "h", "username": "u", "folder": "INBOX",
        })


@pytest.mark.parametrize("kind,provider", [("mail", "caldav"), ("calendar", "imap"),
                                           ("mail", "smtp"), ("nonsense", "imap")])
def test_mismatched_kinds_are_refused(config_with_connectors, kind, provider):
    with pytest.raises(setup.SetupError):
        setup.add_account(kind, provider, {"label": "x", "host": "h", "username": "u"})


# -- what it accepts -------------------------------------------------------


def test_a_mail_account_is_added(config_with_connectors):
    result = setup.add_account("mail", "imap", {
        "label": "gmail",
        "host": "imap.gmail.com",
        "port": 993,
        "username": "me@gmail.com",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
    })

    entry = accounts(config_with_connectors, "mail")[0]
    assert entry["label"] == "gmail"
    assert entry["provider"] == "imap"
    assert entry["host"] == "imap.gmail.com"
    assert "password" not in entry

    # The account is inert until its secret is stored, and nothing else says so.
    assert result["needs_secret"] is True
    assert result["secret_kind"] == "password"


def test_a_caldav_calendar_is_added(config_with_connectors):
    """CalDAV is fine: the URL is a server address, not a secret."""
    setup.add_account("calendar", "caldav", {
        "label": "work",
        "url": "https://caldav.fastmail.com/dav/",
        "username": "me@fastmail.com",
        "writable": True,
    })

    entry = accounts(config_with_connectors, "calendar")[0]
    assert entry["url"] == "https://caldav.fastmail.com/dav/"
    assert entry["writable"] is True


def test_the_account_is_visible_to_the_loader(config_with_connectors):
    setup.add_account("mail", "imap", {
        "label": "gmail", "host": "imap.gmail.com", "username": "me@gmail.com",
    })
    # settings.Config keeps connectors as raw dicts on purpose: connectors/base.py
    # owns their shape, and they must never be reachable as attributes that could
    # be written back through the generic settings path.
    labels = [entry["label"] for entry in load_config().connectors["mail"]]
    assert "gmail" in labels


def test_adding_keeps_every_comment(config_with_connectors):
    """The config's comments are its only documentation."""
    before = sum(1 for line in config_with_connectors.read_text(encoding="utf-8").splitlines()
                 if "#" in line)
    setup.add_account("mail", "imap", {
        "label": "gmail", "host": "imap.gmail.com", "username": "me@gmail.com",
    })
    after_text = config_with_connectors.read_text(encoding="utf-8")

    assert sum(1 for line in after_text.splitlines() if "#" in line) == before
    assert "# what it calls itself" in after_text
    assert "# No password is ever written here." in after_text


def test_a_duplicate_label_is_refused(config_with_connectors):
    fields = {"label": "gmail", "host": "imap.gmail.com", "username": "me@gmail.com"}
    setup.add_account("mail", "imap", dict(fields))

    with pytest.raises(setup.SetupError, match="already"):
        setup.add_account("mail", "imap", dict(fields))
    assert len(accounts(config_with_connectors, "mail")) == 1


@pytest.mark.parametrize("bad", ["", "  ", "no/slashes", "a" * 60, "semi;colon"])
def test_a_bad_label_is_refused(config_with_connectors, bad):
    with pytest.raises(setup.SetupError, match="label"):
        setup.add_account("mail", "imap", {
            "label": bad, "host": "h.example.com", "username": "u",
        })


def test_a_missing_server_is_refused(config_with_connectors):
    with pytest.raises(setup.SetupError, match="server"):
        setup.add_account("mail", "imap", {"label": "x", "username": "u"})


@pytest.mark.parametrize("port", [0, -1, 70000, "imap"])
def test_a_bad_port_is_refused(config_with_connectors, port):
    with pytest.raises(setup.SetupError, match="port"):
        setup.add_account("mail", "imap", {
            "label": "x", "host": "h.example.com", "username": "u", "port": port,
        })


def test_a_caldav_url_must_be_http(config_with_connectors):
    with pytest.raises(setup.SetupError, match="http"):
        setup.add_account("calendar", "caldav", {
            "label": "x", "url": "caldav.example.com", "username": "u",
        })


# -- removal ---------------------------------------------------------------


def test_an_account_can_be_removed(config_with_connectors):
    setup.add_account("mail", "imap", {
        "label": "gmail", "host": "imap.gmail.com", "username": "me@gmail.com",
    })
    setup.remove_account("mail", "gmail")
    assert accounts(config_with_connectors, "mail") == []


def test_removing_an_absent_account_says_so(config_with_connectors):
    with pytest.raises(setup.SetupError, match="no mail account"):
        setup.remove_account("mail", "nothing")


def test_removal_leaves_the_stored_password_alone(config_with_connectors, monkeypatch):
    """The credential belongs to the OS store.

    Deleting it here would mean an accidental removal silently destroyed a
    password the user may not have written down anywhere else.
    """
    from app.connectors import credentials

    deleted: list[str] = []
    monkeypatch.setattr(credentials, "delete", lambda ref: deleted.append(ref), raising=False)

    setup.add_account("mail", "imap", {
        "label": "gmail", "host": "imap.gmail.com", "username": "me@gmail.com",
    })
    setup.remove_account("mail", "gmail")

    assert deleted == []


# -- the boundary this sits beside ----------------------------------------


def test_the_generic_settings_writer_still_refuses_connectors(config_with_connectors):
    """This module is a narrow exception, not a relaxation of the allow-list."""
    from app import preferences

    with pytest.raises(preferences.NotWritable):
        preferences.update({"connectors": {"mail": [{"label": "sneaky"}]}})


# -- setting the password from the app (REQ-26) ----------------------------
#
# This moved out of the terminal because an assistant whose accounts can only be
# set up by typing commands is not configurable by the people it is for. The
# secret now crosses one loopback hop, so these pin what holds around it.


@pytest.fixture
def client(config_with_connectors):
    from fastapi.testclient import TestClient

    from app.main import app

    setup.add_account("mail", "imap", {
        "label": "gmail", "host": "imap.gmail.com", "username": "me@gmail.com",
    })
    with TestClient(app) as c:
        yield c


def test_a_password_can_be_stored_from_the_app(client, monkeypatch):
    from app.connectors import credentials

    saved = {}
    monkeypatch.setattr(credentials, "store", lambda ref, secret: saved.update({ref: secret}))

    response = client.put("/connectors/mail/gmail/credential", json={"secret": "hunter2"})

    assert response.status_code == 200
    assert saved == {"mail:gmail": "hunter2"}


def test_the_password_is_never_echoed_back(client, monkeypatch):
    """The response says a reference, never the value."""
    from app.connectors import credentials

    monkeypatch.setattr(credentials, "store", lambda ref, secret: None)
    body = client.put("/connectors/mail/gmail/credential", json={"secret": "hunter2"}).json()

    assert "hunter2" not in json_dumps(body)
    assert body["credential_ref"] == "mail:gmail"


def test_the_password_is_never_logged(client, monkeypatch, caplog):
    from app.connectors import credentials

    monkeypatch.setattr(credentials, "store", lambda ref, secret: None)
    with caplog.at_level("DEBUG"):
        client.put("/connectors/mail/gmail/credential", json={"secret": "hunter2"})

    assert "hunter2" not in caplog.text


def test_the_password_never_reaches_the_config_file(client, config_with_connectors, monkeypatch):
    from app.connectors import credentials

    monkeypatch.setattr(credentials, "store", lambda ref, secret: None)
    client.put("/connectors/mail/gmail/credential", json={"secret": "hunter2"})

    assert "hunter2" not in config_with_connectors.read_text(encoding="utf-8")


def test_no_endpoint_hands_a_secret_back(client, monkeypatch):
    """The whole surface, not just the one that stored it."""
    from app.connectors import credentials

    monkeypatch.setattr(credentials, "store", lambda ref, secret: None)
    monkeypatch.setattr(credentials, "fetch", lambda ref: "hunter2")
    client.put("/connectors/mail/gmail/credential", json={"secret": "hunter2"})

    for path in ("/connectors", "/health", "/settings", "/state"):
        response = client.get(path)
        if response.status_code == 200:
            assert "hunter2" not in json_dumps(response.json()), path


def test_an_empty_password_is_refused(client):
    assert client.put("/connectors/mail/gmail/credential", json={"secret": "   "}).status_code == 400


def test_setting_a_password_for_an_unknown_account_is_404(client):
    response = client.put("/connectors/mail/nothing/credential", json={"secret": "x"})
    assert response.status_code == 404


def json_dumps(value) -> str:
    import json

    return json.dumps(value, default=str)

"""Writable settings and notifications — REQ-5, REQ-9, REQ-26.

Two properties matter here.

The allow-list is a security boundary, not a convenience: anything deciding what
leaves the machine or what the assistant may touch has to stay hand-edited, or
the file stops being trustworthy as a record of those decisions.

And a write must not destroy the file's comments. They are the config's only
documentation, and a PyYAML round-trip silently deletes all 74 of them.
"""

from __future__ import annotations

import pytest
import yaml

from app import notifications, preferences
from app.settings import load_config, reset_config_cache


def comment_count(path) -> int:
    """Full-line and trailing comments both count.

    The shipped config uses both, and losing either loses documentation.
    """
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if "#" in line)


@pytest.fixture
def commented_config(workspace, config_file):
    """A config carrying comments, as the shipped one does."""
    config_file.write_text(
        "# Kai configuration\n"
        "persona:\n"
        "  name: Kai            # what it calls itself\n"
        "  verbosity: terse\n"
        "voice:\n"
        "  # Master switch for speech\n"
        "  enabled: false\n"
        "  output_enabled: true # speaks replies\n"
        "privacy:\n"
        "  allow_web_search: true   # leaves the machine\n"
        "system:\n"
        "  allowed_roots:\n"
        "    - ~/Downloads\n",
        encoding="utf-8",
    )
    reset_config_cache()
    return config_file


# -- what may be changed --------------------------------------------------


def test_a_local_preference_can_be_changed(commented_config):
    preferences.update({"voice": {"enabled": True}})

    assert load_config().voice.enabled is True
    assert "enabled: true" in commented_config.read_text(encoding="utf-8")


def test_writing_keeps_every_comment(commented_config):
    """The comments are the file's documentation.

    yaml.safe_dump drops all of them, which would quietly delete the config's
    own explanation the first time anyone flipped a switch in the UI.
    """
    before = comment_count(commented_config)
    assert before >= 4

    preferences.update({"voice": {"enabled": True}, "persona": {"verbosity": "chatty"}})

    assert comment_count(commented_config) == before
    text = commented_config.read_text(encoding="utf-8")
    assert "# what it calls itself" in text
    assert "# Master switch for speech" in text


def test_the_file_stays_valid_yaml(commented_config):
    preferences.update({"voice": {"stt_model": "small"}})

    parsed = yaml.safe_load(commented_config.read_text(encoding="utf-8"))
    assert parsed["voice"]["stt_model"] == "small"
    assert parsed["privacy"]["allow_web_search"] is True  # untouched


# -- what may not (REQ-26) ------------------------------------------------


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("privacy", "allow_web_search", False),
        ("privacy", "allow_cloud_llm", True),
        ("system", "allowed_roots", ["C:/"]),
        ("documents", "indexed_folders", ["C:/"]),
        ("connectors", "mail", []),
        ("brain", "ollama_host", "http://evil.example.com"),
    ],
)
def test_security_relevant_settings_are_refused(commented_config, section, key, value):
    with pytest.raises(preferences.NotWritable, match="kai.config.yaml"):
        preferences.update({section: {key: value}})


def test_a_refused_key_leaves_the_file_untouched(commented_config):
    """All-or-nothing: a rejected key must not half-apply the rest."""
    before = commented_config.read_text(encoding="utf-8")

    with pytest.raises(preferences.NotWritable):
        preferences.update({
            "voice": {"enabled": True},              # allowed
            "privacy": {"allow_web_search": False},  # refused
        })

    assert commented_config.read_text(encoding="utf-8") == before
    assert load_config().voice.enabled is False


def test_a_bad_value_is_refused(commented_config):
    with pytest.raises(preferences.NotWritable, match="one of"):
        preferences.update({"voice": {"stt_model": "gigantic"}})
    with pytest.raises(preferences.NotWritable, match="true or false"):
        preferences.update({"voice": {"enabled": "yes please"}})


def test_the_allow_list_names_no_security_setting():
    """A regression guard on the boundary itself."""
    flat = {f"{s}.{k}" for s, keys in preferences.WRITABLE.items() for k in keys}

    for forbidden in ("privacy", "connectors", "system", "documents", "brain", "actions"):
        assert not any(name.startswith(forbidden + ".") for name in flat), (
            f"{forbidden}.* must not be writable from the UI"
        )


# -- notifications (REQ-9) ------------------------------------------------


class FakeDelivery:
    item_id = "abc"
    kind = "reminder"
    label = "take the bins out"

    def message(self):
        return "Reminder (was due Thu 06 Aug at 22:03): take the bins out"


def test_a_due_reminder_reaches_the_desktop(workspace):
    """The API process had no subscriber, so reminders were consumed silently.

    The item was marked delivered and nobody was told -- lost, not late.
    """
    notifications.clear()
    notifications.on_scheduler_delivery(FakeDelivery())

    queued = notifications.peek()
    assert len(queued) == 1
    assert "take the bins out" in queued[0].body
    # REQ-9: a late reminder states the time it was actually for.
    assert "was due" in queued[0].body


def test_notifications_are_handed_out_once(workspace):
    notifications.clear()
    notifications.on_scheduler_delivery(FakeDelivery())

    assert len(notifications.drain()) == 1
    assert notifications.drain() == []


def test_the_queue_is_bounded(workspace):
    """A machine left off for a week must not return hundreds of toasts."""
    notifications.clear()
    for index in range(notifications.MAX_QUEUED + 25):
        notifications.publish("reminder", "Reminder", f"item {index}")

    queued = notifications.drain()
    assert len(queued) == notifications.MAX_QUEUED
    # The newest survive; the oldest are the ones worth dropping.
    assert queued[-1].body.endswith(str(notifications.MAX_QUEUED + 24))


def test_a_broken_delivery_does_not_raise(workspace):
    """A raising subscriber is swallowed by the scheduler, which is the exact
    silent loss this module exists to prevent."""
    class Broken:
        def message(self):
            raise RuntimeError("nope")

    notifications.clear()
    notifications.on_scheduler_delivery(Broken())  # must not raise
    assert notifications.peek() == []

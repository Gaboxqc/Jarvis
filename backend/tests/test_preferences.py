"""Writable settings and notifications — REQ-5, REQ-9, REQ-26.

Two properties matter here.

The allow-list is a security boundary, not a convenience: anything deciding what
leaves the machine or what the assistant may touch has to stay hand-edited, or
the file stops being trustworthy as a record of those decisions.

And a write must not destroy the file's comments. They are the config's only
documentation, and a PyYAML round-trip silently deletes all 74 of them.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

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


# -- what may not, and what now may with guards (REQ-26) -------------------
#
# Most of this section used to assert that privacy, system and documents were
# unwritable. They are writable now: the app is required to be configurable
# without a text editor, and the old exclusion was protecting *visibility*,
# which logging serves better than making a setting hard to reach.
#
# What replaced it is narrower and about blast radius, so that is what is
# tested here.


@pytest.mark.parametrize(
    "section,key,value",
    [
        # Secrets. These go through connectors/setup.py, which refuses them too.
        ("connectors", "mail", []),
        ("connectors", "calendar", []),
        # Not exposed on purpose: pointing the assistant at a different model
        # host is how you would exfiltrate every prompt, and it is not something
        # anyone needs a settings screen for.
        ("brain", "ollama_host", "http://evil.example.com"),
        ("actions", "pre_approved", ["system.organize_folder"]),
        ("skills", "disabled", []),
    ],
)
def test_settings_outside_the_allow_list_are_refused(commented_config, section, key, value):
    with pytest.raises(preferences.NotWritable, match="kai.config.yaml"):
        preferences.update({section: {key: value}})


def test_connectors_are_never_writable_here():
    """A regression guard on the one section that carries secrets."""
    assert "connectors" not in preferences.WRITABLE


def test_the_model_host_is_not_writable():
    """Redirecting the model host would send every prompt somewhere else."""
    assert "ollama_host" not in preferences.WRITABLE.get("brain", {})


def test_an_egress_switch_can_be_changed(commented_config):
    preferences.update({"privacy": {"allow_web_search": False}})
    assert load_config().privacy.allow_web_search is False


def test_changing_egress_is_logged_loudly_with_the_old_value(commented_config, caplog):
    """Turning on web search has to leave a mark somewhere the user can find.

    This is the whole trade for making these writable: they stopped being hard
    to reach, so they became impossible to change quietly.
    """
    with caplog.at_level("WARNING"):
        preferences.update({"privacy": {"allow_web_search": False}})

    assert "egress setting changed" in caplog.text
    assert "privacy.allow_web_search" in caplog.text
    assert "was True" in caplog.text


def test_an_ordinary_preference_is_not_logged_as_egress(commented_config, caplog):
    with caplog.at_level("WARNING"):
        preferences.update({"voice": {"enabled": True}})
    assert "egress setting changed" not in caplog.text


# -- folders: the blast radius of every file skill -------------------------


def test_folders_must_exist(commented_config, tmp_path):
    with pytest.raises(preferences.NotWritable, match="doesn't exist"):
        preferences.update({"system": {"allowed_roots": [str(tmp_path / "nope")]}})


def test_a_file_is_not_a_folder(commented_config, tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(preferences.NotWritable, match="not a folder"):
        preferences.update({"documents": {"indexed_folders": [str(target)]}})


def test_a_whole_drive_is_refused(commented_config):
    """Handing over a drive is not a preference someone sets by accident."""
    root = Path(sys.executable).anchor
    with pytest.raises(preferences.NotWritable, match="whole drive"):
        preferences.update({"system": {"allowed_roots": [root]}})


def test_a_windows_directory_is_refused(commented_config):
    windows = Path(os.environ.get("SystemRoot", "C:/Windows"))
    if not windows.exists():
        pytest.skip("no Windows directory on this machine")
    with pytest.raises(preferences.NotWritable, match="belongs to Windows"):
        preferences.update({"system": {"allowed_roots": [str(windows)]}})


def test_an_empty_folder_list_is_refused(commented_config):
    """Silently ending up with no roots would disable every file skill."""
    with pytest.raises(preferences.NotWritable, match="at least one folder"):
        preferences.update({"system": {"allowed_roots": []}})


def test_folders_are_stored_resolved(commented_config, tmp_path):
    """A relative or ~-relative path in the file would resolve against whatever
    directory the backend happened to start in."""
    # Not "docs": the workspace fixture already made one.
    nested = tmp_path / "indexed-here"
    nested.mkdir()
    preferences.update({"documents": {"indexed_folders": [str(nested)]}})

    stored = load_config().documents.indexed_folders
    assert [str(p) for p in stored] == [str(nested.resolve())]


def test_a_refused_key_leaves_the_file_untouched(commented_config):
    """All-or-nothing: a rejected key must not half-apply the rest."""
    before = commented_config.read_text(encoding="utf-8")

    with pytest.raises(preferences.NotWritable):
        preferences.update({
            "voice": {"enabled": True},          # allowed
            "connectors": {"mail": []},          # refused
        })

    assert commented_config.read_text(encoding="utf-8") == before
    assert load_config().voice.enabled is False


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


# -- the boundary that did not move ---------------------------------------


def test_no_skill_can_write_settings():
    """Widening the assistant's reach must stay a human decision.

    The API can now change allowed_roots, which is exactly what a
    prompt-injected model would want to do — point the assistant at the whole
    disk and then read it. That is safe only while nothing the *model* can
    invoke reaches this module. Skills are the only thing the model can invoke.
    """
    import pkgutil
    from pathlib import Path as _Path

    import app.skills as skills_package

    offenders = []
    for module in pkgutil.walk_packages(skills_package.__path__, "app.skills."):
        path = _Path(skills_package.__path__[0]).parent.parent / (
            module.name.replace(".", "/") + ".py"
        )
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        if "preferences" in source or "connectors.setup" in source:
            offenders.append(module.name)

    assert not offenders, (
        f"these skills can reach the settings writer: {offenders}. "
        "A skill is model-invokable, so this would let a prompt injection "
        "widen allowed_roots and then read whatever it liked."
    )


# -- the file must still look hand-written afterwards -----------------------


def test_changing_a_list_keeps_the_comment_that_follows_it(workspace, config_file, tmp_path):
    """ruamel hangs a comment off the index of the item it follows.

    So the comment documenting `max_file_mb` is stored against the last entry of
    `indexed_folders`, and replacing that list threw it away — deleting the
    documentation of a setting the user never touched.
    """
    folder = tmp_path / "papers"
    folder.mkdir()
    config_file.write_text(
        "documents:\n"
        "  indexed_folders:\n"
        "    - ~/Documents\n"
        "  # Files larger than this are skipped rather than stalling a scan.\n"
        "  max_file_mb: 25\n",
        encoding="utf-8",
    )
    reset_config_cache()

    preferences.update({"documents": {"indexed_folders": [str(folder)]}})

    text = config_file.read_text(encoding="utf-8")
    assert "# Files larger than this are skipped" in text
    assert str(folder) in text


def test_untouched_lists_are_not_reindented(workspace, config_file, tmp_path):
    """Changing one setting must not rewrite the shape of unrelated ones.

    ruamel re-emits every sequence at its own default indent unless told
    otherwise, so a one-key change produced a diff across the whole file and
    made the config look rewritten rather than edited.
    """
    folder = tmp_path / "papers"
    folder.mkdir()
    config_file.write_text(
        "documents:\n"
        "  indexed_folders:\n"
        "    - ~/Documents\n"
        "system:\n"
        "  allowed_roots:\n"
        "    - ~/Downloads\n"
        "    - ~/Desktop\n",
        encoding="utf-8",
    )
    reset_config_cache()

    preferences.update({"documents": {"indexed_folders": [str(folder)]}})

    text = config_file.read_text(encoding="utf-8")
    # The list nobody asked to change keeps its four-space entries.
    assert "    - ~/Downloads" in text
    assert "    - ~/Desktop" in text


def test_a_trailing_comment_moves_to_the_end_when_the_list_grows(workspace, config_file, tmp_path):
    """A comment after the last item documents what comes next, not that item.

    Pinning it to its old index stranded it mid-list: adding one folder left
    "# Apps closed when a focus session starts" sitting between two paths,
    reading as though it described one of them.
    """
    extra = tmp_path / "music"
    extra.mkdir()
    config_file.write_text(
        "system:\n"
        "  allowed_roots:\n"
        "    - ~/Downloads\n"
        "    - ~/Desktop\n"
        "  # Apps closed when a focus session starts (REQ-23)\n"
        "  distracting_apps:\n"
        "    - Discord.exe\n",
        encoding="utf-8",
    )
    reset_config_cache()

    preferences.update({
        "system": {"allowed_roots": [str(tmp_path), str(extra)]},
    })

    lines = config_file.read_text(encoding="utf-8").splitlines()
    comment_at = next(i for i, l in enumerate(lines) if "Apps closed when" in l)
    apps_at = next(i for i, l in enumerate(lines) if "distracting_apps" in l)
    last_root_at = max(i for i, l in enumerate(lines) if str(extra) in l)

    # It has to sit after every folder and immediately before what it documents.
    assert last_root_at < comment_at < apps_at

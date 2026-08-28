"""Writable preferences — REQ-5, REQ-26.

The allow-list below is the whole security argument: a setting absent from it
cannot be changed through the API at all, whatever the caller asks for.

Most of it used to be much shorter. `privacy.*`, `system.allowed_roots` and
`documents.indexed_folders` were excluded on the reasoning that a UI toggle
silently disagreeing with the file would make the file untrustworthy — and
those are exactly the settings someone opens the file to be sure of.

That reasoning does not survive contact with the requirement that the app be
configurable without a text editor. It was also weaker than it looked: writes go
through ruamel and update the file in place with its comments intact, so the UI
and the file never disagree. What the exclusion was really protecting was that
these decisions stay *visible*, and visibility is better served by logging every
egress change at WARNING with its old value than by making the setting hard to
reach and leaving people to edit YAML.

Two guards remain, because they are about blast radius rather than visibility:
folder lists must point at real directories and may not hand over a whole drive
or a Windows directory, and `connectors.*` still cannot be written here — those
carry secrets and go through connectors/setup.py, which refuses them.

Writes keep the file's comments. A PyYAML round-trip drops all 74 of them, which
would quietly delete the config's own documentation the first time anyone
flipped a switch in the UI.
"""

from __future__ import annotations

import io
import logging
import os
import threading
from pathlib import Path
from typing import Any

from .settings import config_path, load_config, reset_config_cache

log = logging.getLogger(__name__)

_write_lock = threading.Lock()

class Folders:
    """Marker for a list of directories the user is handing the assistant.

    Not just "a list of strings". Each entry has to exist and be a directory, or
    the setting is a promise the assistant cannot keep and the failure surfaces
    later as an empty search rather than as a bad setting.
    """


class Sensitive:
    """Marker for a setting that changes what leaves the machine (REQ-26).

    Writable, but never silently: every change is logged at WARNING with the old
    and new value. These were read-only until the app was required to be
    configurable without a text editor, and the honest trade is that the UI can
    now turn on web search — so the record of it having happened has to exist
    somewhere the user can find.
    """


# Folders that must never become an allowed root or an indexed folder from a
# click. Handing the assistant an entire drive is not a preference, and neither
# is pointing it at Windows.
_FORBIDDEN_ROOTS = {"windows", "program files", "program files (x86)", "programdata", "system32"}


# section -> key -> validator. Anything absent is refused.
WRITABLE: dict[str, dict[str, Any]] = {
    "voice": {
        "enabled": bool,
        "input_enabled": bool,
        "output_enabled": bool,
        "wake_enabled": bool,
        "voice_id": str,
        "stt_model": {"tiny", "base", "small", "medium"},
        "wake_word": str,
        "language": {"en", "es"},
        "tts_engine": {"piper", "xtts"},
        # Writable so it can be withdrawn, which must be as easy as granting it.
        "clone_consent": bool,
        # Written by the endpoint that stamps the date; see main.py.
        "xtts_licence_accepted": bool,
        "xtts_licence_accepted_at": str,
    },
    "persona": {
        "name": str,
        "verbosity": {"terse", "normal", "chatty"},
        "language": {"en", "es"},
        "address_style": str,
        "tone_description": str,
        "idle_timeout_minutes": int,
    },
    # Everything below became writable when the app was required to be
    # configurable without a text editor. The original reasoning for keeping
    # them out was that a UI toggle disagreeing with the file would make the
    # file untrustworthy -- but writes go through ruamel and update the file in
    # place, comments intact, so they never disagree. What the reasoning was
    # really protecting was that these decisions be *visible*, which is now the
    # job of Sensitive's logging rather than of making them hard to reach.
    "privacy": {
        "allow_web_search": Sensitive,
        "allow_live_data": Sensitive,
        "allow_cloud_llm": Sensitive,
    },
    "documents": {
        "indexed_folders": Folders,
        "max_file_mb": int,
        "pause_on_battery": bool,
    },
    "system": {
        # The files the assistant may read and organise. Guarded harder than the
        # rest: this is the blast radius of every file skill.
        "allowed_roots": Folders,
    },
    "avatar": {
        # Live2D's runtime licence. Writable so it can be withdrawn, which has
        # to be as easy as granting it, and written by the same endpoint that
        # stamps the date -- see main.py.
        "licence_accepted": bool,
        "licence_accepted_at": str,
    },
    "brain": {
        "model": str,
        "temperature": float,
        "context_tokens": int,
    },
    # How long the record of what was said and done is kept. Plain ints rather
    # than Sensitive: nothing here changes what leaves the machine, and the
    # direction that needs care is *shortening* the window, which destroys data
    # -- reported in the reply and visible in the counts, not logged as egress.
    "retention": {
        "conversation_days": int,
        "history_days": int,
    },
}


class NotWritable(Exception):
    """The caller tried to change something the UI may not change."""


def _check(section: str, key: str, value: Any) -> Any:
    allowed = WRITABLE.get(section)
    if allowed is None or key not in allowed:
        raise NotWritable(
            f"'{section}.{key}' can only be changed by editing kai.config.yaml."
        )

    rule = allowed[key]
    if isinstance(rule, set):
        if value not in rule:
            raise NotWritable(f"'{section}.{key}' must be one of {sorted(rule)}.")
        return value
    if rule is bool or rule is Sensitive:
        if not isinstance(value, bool):
            raise NotWritable(f"'{section}.{key}' must be true or false.")
        return value
    if rule is Folders:
        return _check_folders(section, key, value)
    if rule is int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise NotWritable(f"'{section}.{key}' must be a whole number.") from exc
    if rule is float:
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise NotWritable(f"'{section}.{key}' must be a number.") from exc
    return str(value)


def _check_folders(section: str, key: str, value: Any) -> list[str]:
    """Validate a list of directories the user is handing the assistant."""
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise NotWritable(f"'{section}.{key}' is a list of folders.")

    cleaned: list[str] = []
    for entry in value:
        text = str(entry).strip()
        if not text:
            continue
        path = Path(os.path.expandvars(os.path.expanduser(text)))
        try:
            resolved = path.resolve()
        except OSError as exc:
            raise NotWritable(f"'{text}' isn't a usable path: {exc}") from exc

        if not resolved.exists():
            raise NotWritable(f"'{resolved}' doesn't exist.")
        if not resolved.is_dir():
            raise NotWritable(f"'{resolved}' is a file, not a folder.")
        # A drive root hands over everything, including other users' profiles
        # and the OS itself. That is not a preference someone sets by accident.
        if resolved.parent == resolved:
            raise NotWritable(
                f"'{resolved}' is a whole drive. Pick the folders you actually "
                "want reachable."
            )
        if resolved.name.lower() in _FORBIDDEN_ROOTS or (
            resolved.parent == resolved.anchor
            and resolved.name.lower() in _FORBIDDEN_ROOTS
        ):
            raise NotWritable(f"'{resolved}' belongs to Windows, not to you.")

        cleaned.append(str(resolved))

    if not cleaned:
        raise NotWritable(f"'{section}.{key}' needs at least one folder.")
    return cleaned


def _replace_list(existing: Any, value: list[Any]) -> None:
    """Swap a list's contents while keeping the comments hanging off it.

    ruamel attaches a comment to the *index* of the sequence item it follows, so
    a comment written to document the next setting is stored against the last
    folder in the list above it. Assigning a plain Python list -- by name or by
    slice -- clears that mapping, and the comment vanishes from a file the user
    never edited. Changing one folder should not silently delete the
    documentation of an unrelated setting.

    A comment on the *final* item follows the end of the list rather than that
    particular entry -- it is almost always documentation for the next setting
    down -- so it moves to whatever the new final item is. Pinning it to its
    old index instead strands it mid-list: growing allowed_roots by one left
    "# Apps closed when a focus session starts" sitting between two folders,
    reading as though it described one of them.

    Comments on other items keep their position where it still exists.
    """
    comments = dict(getattr(getattr(existing, "ca", None), "items", {}) or {})
    was_last = len(existing) - 1
    existing[:] = value

    if not comments or not hasattr(existing, "ca"):
        return
    now_last = len(existing) - 1
    if now_last < 0:
        return
    for index, token in comments.items():
        target = now_last if index >= was_last else min(index, now_last)
        existing.ca.items.setdefault(target, token)


def update(changes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Apply a nested patch to the config file, keeping its comments.

    All-or-nothing: every key is validated before anything is written, so a
    rejected key cannot leave half a change on disk.
    """
    validated: dict[str, dict[str, Any]] = {}
    for section, values in (changes or {}).items():
        if not isinstance(values, dict):
            raise NotWritable(f"'{section}' should be a group of settings.")
        for key, value in values.items():
            validated.setdefault(section, {})[key] = _check(section, key, value)

    if not validated:
        return {"changed": {}}

    try:
        from ruamel.yaml import YAML
    except ImportError as exc:  # pragma: no cover
        raise NotWritable("Settings can't be saved (ruamel.yaml isn't installed).") from exc

    # Only the egress settings need a "was" for the audit line, and only those
    # are read here — keeping this narrow avoids reaching into config sections
    # whose shape is not this module's business.
    before: dict[str, Any] = {}
    for section, values in validated.items():
        for key in values:
            if WRITABLE[section][key] is Sensitive:
                before[f"{section}.{key}"] = getattr(
                    getattr(load_config(), section, None), key, None
                )

    path = config_path()
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096  # don't reflow long comment lines
    # Match the shipped file's style. Without this, ruamel re-emits every
    # sequence at its own default indent -- including lists nobody touched --
    # so changing one setting produces a diff across unrelated parts of the
    # file and makes the config look rewritten rather than edited.
    yaml.indent(mapping=2, sequence=4, offset=2)

    with _write_lock:
        try:
            document = yaml.load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            raise NotWritable(f"The config file couldn't be read: {exc}") from exc

        for section, values in validated.items():
            target = document.get(section)
            if target is None:
                document[section] = target = {}
            for key, value in values.items():
                existing = target.get(key)
                if isinstance(value, list) and isinstance(existing, list):
                    _replace_list(existing, value)
                else:
                    target[key] = value

        buffer = io.StringIO()
        yaml.dump(document, buffer)
        rendered = buffer.getvalue()

        try:
            # Write beside the target and replace, so an interrupted write
            # cannot leave the user with a truncated config.
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(rendered, encoding="utf-8")
            temporary.replace(path)
        except OSError as exc:
            raise NotWritable(f"The config file couldn't be written: {exc}") from exc

    # The loader keys its cache on mtime; drop it so the next turn sees this.
    reset_config_cache()

    # Anything governing what leaves the machine is recorded loudly and by name.
    # These stopped being read-only so the app could be configured without a
    # text editor; the trade is that turning one on has to leave a mark
    # somewhere the user can find afterwards (REQ-26).
    for section, values in validated.items():
        for key, value in values.items():
            if WRITABLE[section][key] is Sensitive:
                log.warning(
                    "egress setting changed: %s.%s -> %s (was %s)",
                    section, key, value, before.get(f"{section}.{key}"),
                )

    log.info("updated settings: %s", validated)
    return {"changed": validated, "config_file": str(path)}


def writable_keys() -> dict[str, list[str]]:
    """What the UI is permitted to change, for it to render honestly."""
    return {section: sorted(keys) for section, keys in WRITABLE.items()}


def current() -> dict[str, Any]:
    """Present values of the writable settings."""
    config = load_config()
    return {
        "voice": {
            "enabled": config.voice.enabled,
            "input_enabled": config.voice.input_enabled,
            "output_enabled": config.voice.output_enabled,
            "wake_enabled": config.voice.wake_enabled,
            "voice_id": config.voice.voice_id,
            "stt_model": config.voice.stt_model,
            "wake_word": config.voice.wake_word,
            "language": config.voice.language,
        },
        "persona": {
            "name": config.persona.name,
            "verbosity": config.persona.verbosity,
            "language": config.persona.language,
            "address_style": config.persona.address_style,
            "tone_description": config.persona.tone_description,
            "idle_timeout_minutes": config.persona.idle_timeout_minutes,
        },
        "privacy": {
            "allow_web_search": config.privacy.allow_web_search,
            "allow_live_data": config.privacy.allow_live_data,
            "allow_cloud_llm": config.privacy.allow_cloud_llm,
        },
        "documents": {
            # Paths are Path objects internally; the API is JSON.
            "indexed_folders": [str(p) for p in config.documents.indexed_folders],
            "max_file_mb": config.documents.max_file_mb,
            "pause_on_battery": config.documents.pause_on_battery,
        },
        "system": {
            "allowed_roots": [str(p) for p in config.system.allowed_roots],
        },
        "brain": {
            "model": config.brain.model,
            "temperature": config.brain.temperature,
            "context_tokens": config.brain.context_tokens,
        },
    }

"""Writable preferences — REQ-5, REQ-26.

The UI can change local preferences. It cannot change anything that decides
what leaves this machine or what the assistant is allowed to touch.

The allow-list below is the whole security argument. `privacy.*`,
`connectors.*`, `system.allowed_roots` and `documents.indexed_folders` are
absent on purpose: a UI toggle that silently disagreed with the file would make
the file untrustworthy, and those four are exactly the settings someone would
check the file to be sure of. They stay editable only by a human opening
kai.config.yaml.

Writes go through ruamel so the file keeps its comments. A PyYAML round-trip
drops all 74 of them, which would quietly delete the config's own documentation
the first time anyone flipped a switch in the UI.
"""

from __future__ import annotations

import io
import logging
import threading
from typing import Any

from .settings import config_path, load_config, reset_config_cache

log = logging.getLogger(__name__)

_write_lock = threading.Lock()

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
    },
    "persona": {
        "name": str,
        "verbosity": {"terse", "normal", "chatty"},
        "language": {"en", "es"},
        "address_style": str,
        "idle_timeout_minutes": int,
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
    if rule is bool:
        if not isinstance(value, bool):
            raise NotWritable(f"'{section}.{key}' must be true or false.")
        return value
    if rule is int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise NotWritable(f"'{section}.{key}' must be a whole number.") from exc
    return str(value)


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

    path = config_path()
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096  # don't reflow long comment lines

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
            "idle_timeout_minutes": config.persona.idle_timeout_minutes,
        },
    }

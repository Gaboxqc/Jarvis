"""Adding an account without hand-editing YAML — REQ-13, REQ-26.

Connectors were deliberately left out of `preferences.WRITABLE`: they decide
what leaves the machine, and a switch the UI can flip silently makes the config
file untrustworthy as a record of that decision. That reasoning still holds, so
this is a separate, narrower path rather than a relaxation of the allow-list.

What makes it safe is that connector fields split cleanly:

    account details   label, provider, host, port, username, smtp_host, ...
    secrets           the password, and an `ics` calendar URL

The password never appears here. It is typed at an OS prompt by `/connect` and
goes into the Windows Credential Manager, exactly as before — this only writes
the details needed to know *which* account to ask about.

An `ics` calendar URL is refused outright. Google calls it a "secret address in
iCal format" and that is precise: whoever holds the URL can read the whole
calendar without logging in. It is a bearer credential wearing a URL's clothes,
so it stays hand-edited in a gitignored file rather than travelling through an
API, a log line and a browser field.
"""

from __future__ import annotations

import io
import logging
import re
import threading
from typing import Any

from ..settings import config_path, reset_config_cache

log = logging.getLogger(__name__)

_write_lock = threading.Lock()


class SetupError(Exception):
    """The account could not be added, with a reason worth showing a user."""


# Fields each provider is allowed to carry. Anything not listed is rejected
# rather than ignored: silently dropping a field the caller thought mattered is
# how an account ends up half-configured with no indication why.
_ALLOWED = {
    "imap": {"label", "host", "port", "username", "smtp_host", "smtp_port",
             "from_address", "enabled"},
    "caldav": {"label", "url", "username", "writable", "enabled"},
}

_KIND_PROVIDERS = {"mail": {"imap"}, "calendar": {"caldav"}}

# Anything that looks like it carries a secret. Checked by name across every
# field, so a caller cannot smuggle one through under a spelling we accept.
_SECRET_NAMES = re.compile(r"pass|pwd|secret|token|api_?key|credential", re.IGNORECASE)

_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,40}$")


def add_account(kind: str, provider: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Append one account to connectors.<kind> and say what to do next."""
    kind = (kind or "").strip().lower()
    provider = (provider or "").strip().lower()

    if kind not in _KIND_PROVIDERS:
        raise SetupError("Accounts are either 'mail' or 'calendar'.")

    if kind == "calendar" and provider == "ics":
        raise SetupError(
            "An iCal URL can't be added this way. Google calls it a \"secret address\" "
            "because anyone holding it can read your whole calendar without logging "
            "in — it's a password in the shape of a URL. Paste it into "
            f"{config_path()} yourself, under connectors.calendar."
        )
    if provider not in _KIND_PROVIDERS[kind]:
        allowed = " or ".join(sorted(_KIND_PROVIDERS[kind]))
        raise SetupError(f"A {kind} account here has to be {allowed}.")

    for name in fields:
        if _SECRET_NAMES.search(name):
            raise SetupError(
                "Passwords are never written to the config file. Add the account "
                "first, then run /connect and type it at the prompt — it goes "
                "straight into the Windows Credential Manager."
            )

    unknown = set(fields) - _ALLOWED[provider]
    if unknown:
        raise SetupError(f"Not something a {provider} account has: {', '.join(sorted(unknown))}.")

    entry = _validate(provider, fields)
    label = entry["label"]

    with _write_lock:
        document, yaml = _load()
        connectors = document.get("connectors")
        if connectors is None:
            document["connectors"] = connectors = {}

        existing = connectors.get(kind)
        # An empty list round-trips from YAML as None when the key is `mail:`
        # with nothing under it, which is how the shipped example ships.
        if not isinstance(existing, list):
            existing = []
            connectors[kind] = existing

        if any(str(e.get("label", "")).lower() == label.lower() for e in existing
               if isinstance(e, dict)):
            raise SetupError(f"There's already a {kind} account called '{label}'.")

        existing.append({"provider": provider, **entry})
        _save(document, yaml)

    log.info("added %s account %r (%s)", kind, label, provider)
    return {
        "kind": kind,
        "label": label,
        "provider": provider,
        "config_file": str(config_path()),
        # The account is inert until it has a password, and nothing else in the
        # system will say so, so the answer carries the next step.
        "next_step": f"/connect {kind} {label}",
        "needs_password": True,
    }


def remove_account(kind: str, label: str) -> dict[str, Any]:
    """Drop an account from the config. The stored password is left alone.

    Deliberately: the credential belongs to the OS store, and removing it is a
    separate decision the user makes in the OS. Deleting it here would mean an
    accidental removal silently destroyed a password they may not have written
    down anywhere else.
    """
    kind = (kind or "").strip().lower()
    if kind not in _KIND_PROVIDERS:
        raise SetupError("Accounts are either 'mail' or 'calendar'.")

    with _write_lock:
        document, yaml = _load()
        entries = (document.get("connectors") or {}).get(kind)
        if not isinstance(entries, list):
            raise SetupError(f"There's no {kind} account called '{label}'.")

        keep = [e for e in entries
                if not (isinstance(e, dict) and str(e.get("label", "")).lower() == label.lower())]
        if len(keep) == len(entries):
            raise SetupError(f"There's no {kind} account called '{label}'.")

        document["connectors"][kind] = keep
        _save(document, yaml)

    log.info("removed %s account %r", kind, label)
    return {"removed": label, "kind": kind, "config_file": str(config_path())}


# -- internals -------------------------------------------------------------


def _validate(provider: str, fields: dict[str, Any]) -> dict[str, Any]:
    label = str(fields.get("label", "")).strip()
    if not _LABEL.match(label):
        raise SetupError(
            "The label is how you'll refer to this account later — letters, "
            "numbers, spaces, dashes."
        )

    entry: dict[str, Any] = {"label": label}

    if provider == "imap":
        host = str(fields.get("host", "")).strip()
        username = str(fields.get("username", "")).strip()
        if not host:
            raise SetupError("An IMAP account needs a server, e.g. imap.gmail.com.")
        if not username:
            raise SetupError("An IMAP account needs the username you sign in with.")
        entry["host"] = host
        entry["port"] = _port(fields.get("port", 993))
        entry["username"] = username
        if fields.get("smtp_host"):
            entry["smtp_host"] = str(fields["smtp_host"]).strip()
        if fields.get("smtp_port"):
            entry["smtp_port"] = _port(fields["smtp_port"])
        if fields.get("from_address"):
            entry["from_address"] = str(fields["from_address"]).strip()
    else:  # caldav
        url = str(fields.get("url", "")).strip()
        username = str(fields.get("username", "")).strip()
        if not url.lower().startswith(("http://", "https://")):
            raise SetupError("A CalDAV account needs a server URL starting http:// or https://.")
        if not username:
            raise SetupError("A CalDAV account needs the username you sign in with.")
        entry["url"] = url
        entry["username"] = username
        entry["writable"] = bool(fields.get("writable", True))

    if "enabled" in fields:
        entry["enabled"] = bool(fields["enabled"])
    return entry


def _port(value: Any) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise SetupError(f"'{value}' isn't a port number.") from exc
    if not 1 <= port <= 65535:
        raise SetupError(f"{port} isn't a port number.")
    return port


def _load():
    try:
        from ruamel.yaml import YAML
    except ImportError as exc:  # pragma: no cover
        raise SetupError("The config can't be edited (ruamel.yaml isn't installed).") from exc

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096  # the comments in this file are its documentation
    try:
        return yaml.load(config_path().read_text(encoding="utf-8")) or {}, yaml
    except OSError as exc:
        raise SetupError(f"The config file couldn't be read: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise SetupError(f"The config file isn't valid YAML: {exc}") from exc


def _save(document, yaml) -> None:
    path = config_path()
    buffer = io.StringIO()
    yaml.dump(document, buffer)
    try:
        # Write beside the target and replace, so an interrupted write cannot
        # leave a truncated config behind.
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(buffer.getvalue(), encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        raise SetupError(f"The config file couldn't be written: {exc}") from exc
    reset_config_cache()

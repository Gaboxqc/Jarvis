"""Connector configuration and shared errors — REQ-8, REQ-13, REQ-26, REQ-27.

Connectors are opt-in. Nothing here is required for the assistant to be useful,
and every skill that depends on one has to cope with it being absent, which is
why `NotConfigured` is a distinct type rather than a generic failure: the reply
for "you haven't set this up" is an offer to set it up, and the reply for "your
password expired" is an offer to re-enter it. Collapsing them into one error
would mean giving the wrong one half the time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..settings import load_config
from ..skills.base import SkillError
from . import credentials


class ConnectorError(SkillError):
    """A connector failed at runtime.

    Deliberately a SkillError: a mail server being down or a calendar being
    read-only is an *expected* failure with a sentence the user should read, not
    an internal fault. Without this inheritance the Action Gate treats it as an
    unexpected exception and replaces the useful message with "hit an unexpected
    error", which tells the user nothing and hides what to do about it (REQ-27).
    """


class NotConfigured(ConnectorError):
    """No connector of this kind is set up. Offer setup, don't report a fault."""


class AuthFailed(ConnectorError):
    """Credentials were rejected or missing. Offer re-entry, once."""


@dataclass
class ConnectorConfig:
    kind: str  # "calendar" | "mail"
    label: str
    provider: str  # ics | caldav | imap
    url: str = ""
    host: str = ""
    port: int = 0
    username: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    from_address: str = ""
    writable: bool = False
    enabled: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def credential_ref(self) -> str:
        return credentials.reference(self.kind, self.label)

    @property
    def needs_credential(self) -> bool:
        # `ics` is in here because a Google "secret address in iCal format" is a
        # bearer credential: whoever holds the URL reads the whole calendar
        # without logging in. Storing it in the OS credential store rather than
        # in kai.config.yaml keeps the config file safe to open in front of
        # someone or paste into a bug report, which is the promise this module
        # is built on.
        #
        # An ics account configured the old way, with the URL written into the
        # file, keeps working -- see `resolved_url`.
        return self.provider in {"caldav", "imap", "ics"}

    @property
    def resolved_url(self) -> str:
        """The URL to fetch, wherever it happens to live.

        Config first, so existing hand-edited calendars are untouched; the
        credential store otherwise, which is where the UI puts it.
        """
        if self.url:
            return self.url
        if self.provider == "ics":
            return credentials.fetch(self.credential_ref) or ""
        return ""

    @property
    def has_credential(self) -> bool:
        return credentials.has(self.credential_ref) if self.needs_credential else True

    def secret(self) -> str:
        """Fetch the password at the moment of use, never cached in the object."""
        value = credentials.fetch(self.credential_ref)
        if not value:
            raise AuthFailed(
                f"No password is saved for '{self.label}'. "
                f"Run: /connect {self.kind} {self.label}"
            )
        return value

    def to_dict(self) -> dict[str, Any]:
        """Safe to render anywhere — contains no secret and never will."""
        return {
            "kind": self.kind,
            "label": self.label,
            "provider": self.provider,
            "target": self.url or f"{self.host}:{self.port}" if (self.url or self.host) else "",
            "username": self.username,
            "writable": self.writable,
            "enabled": self.enabled,
            "credential_stored": self.has_credential,
            "credential_ref": self.credential_ref,
        }


def _build(kind: str, raw: dict[str, Any]) -> ConnectorConfig | None:
    label = str(raw.get("label") or "").strip()
    provider = str(raw.get("provider") or "").strip().lower()
    if not label or not provider:
        return None
    return ConnectorConfig(
        kind=kind,
        label=label,
        provider=provider,
        url=str(raw.get("url") or ""),
        host=str(raw.get("host") or ""),
        port=int(raw.get("port") or 0),
        username=str(raw.get("username") or ""),
        smtp_host=str(raw.get("smtp_host") or ""),
        smtp_port=int(raw.get("smtp_port") or 587),
        from_address=str(raw.get("from_address") or raw.get("username") or ""),
        writable=bool(raw.get("writable", False)),
        enabled=bool(raw.get("enabled", True)),
        extra={k: v for k, v in raw.items() if k not in {
            "label", "provider", "url", "host", "port", "username",
            "smtp_host", "smtp_port", "from_address", "writable", "enabled",
        }},
    )


def configured(kind: str) -> list[ConnectorConfig]:
    raw = getattr(load_config(), "connectors", {}) or {}
    entries = raw.get(kind) or []
    built = [_build(kind, entry) for entry in entries if isinstance(entry, dict)]
    return [entry for entry in built if entry is not None and entry.enabled]


def require(kind: str) -> list[ConnectorConfig]:
    entries = configured(kind)
    if not entries:
        noun = "calendar" if kind == "calendar" else "mail account"
        raise NotConfigured(
            f"No {noun} is connected yet. Add one under connectors.{kind} in "
            f"kai.config.yaml, then run /connect {kind} <label> to save the password."
        )
    return entries


def find(kind: str, label: str = "") -> ConnectorConfig:
    entries = require(kind)
    if not label:
        return entries[0]
    for entry in entries:
        if entry.label.lower() == label.lower():
            return entry
    names = ", ".join(e.label for e in entries)
    raise ConnectorError(f"There's no {kind} account called '{label}'. I have: {names}.")


def check(kind: str, label: str = "") -> dict[str, Any]:
    """Try an account and report whether it works, changing nothing.

    Setup feedback. Without it a wrong password surfaces days later, as an
    error about something the user was not doing at the time.
    """
    config = find(kind, label)
    if kind == "mail":
        from . import mail

        return mail.check(config)

    from datetime import timedelta

    from . import calendar as calendar_connector

    since, until = calendar_connector.day_bounds()
    try:
        events = calendar_connector.events_between(config, since, until + timedelta(days=14))
        return {"ok": True, "label": config.label, "events": len(events)}
    except ConnectorError as exc:
        return {"ok": False, "label": config.label, "error": str(exc)}


def status() -> dict[str, Any]:
    return {
        "credential_store": credentials.status().to_dict(),
        "calendar": [entry.to_dict() for entry in configured("calendar")],
        "mail": [entry.to_dict() for entry in configured("mail")],
    }

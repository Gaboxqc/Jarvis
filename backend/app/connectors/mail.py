"""Mail access — REQ-13, REQ-14, REQ-26, REQ-27.

IMAP and SMTP from the standard library. No SDK, no OAuth client secret, and it
works with any provider that accepts an app-specific password — which is all of
the major ones.

Sending is separated from everything else in this module on purpose. Reading,
summarising and flagging are recoverable; a sent message is not, and REQ-14
requires per-message confirmation that never inherits from an earlier approval.
`send()` here is only ever reached through the Action Gate.
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import logging
import re
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from typing import Any

from .base import AuthFailed, ConnectorConfig, ConnectorError

log = logging.getLogger(__name__)

TIMEOUT = 20
MAX_BODY_CHARS = 4000

# Phrases that usually mean a human is waiting on the user. Crude, but it runs
# before the model sees anything and costs nothing.
_NEEDS_REPLY = re.compile(
    r"\b(can you|could you|would you|please (send|confirm|review|advise|let)|"
    r"let me know|thoughts\?|any update|following up|reminder|deadline|"
    r"by (monday|tuesday|wednesday|thursday|friday|tomorrow|end of)|\?)",
    re.IGNORECASE,
)
_AUTOMATED = re.compile(
    r"(no-?reply|do-?not-?reply|notifications?@|newsletter|mailer-daemon|"
    r"unsubscribe|automated)",
    re.IGNORECASE,
)


@dataclass
class Message:
    uid: str
    sender: str
    sender_name: str
    subject: str
    date: datetime | None
    snippet: str
    unread: bool = True
    folder: str = "INBOX"
    body: str = ""

    @property
    def looks_automated(self) -> bool:
        return bool(_AUTOMATED.search(f"{self.sender} {self.subject}"))

    @property
    def probably_needs_reply(self) -> bool:
        if self.looks_automated:
            return False
        return bool(_NEEDS_REPLY.search(f"{self.subject} {self.snippet}"))

    def describe(self) -> str:
        when = self.date.astimezone().strftime("%d %b %H:%M") if self.date else ""
        who = self.sender_name or self.sender
        return f"{who} - {self.subject} [{when}]"

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "from": self.sender,
            "from_name": self.sender_name,
            "subject": self.subject,
            "date": self.date.isoformat() if self.date else None,
            "snippet": self.snippet,
            "unread": self.unread,
            "needs_reply": self.probably_needs_reply,
        }


def _connect(config: ConnectorConfig) -> imaplib.IMAP4_SSL:
    host = config.host or ""
    if not host:
        raise ConnectorError(f"Mail account '{config.label}' has no host configured.")
    port = config.port or 993

    # The context is passed explicitly, and that is the whole point. Left out,
    # imaplib falls back to `ssl._create_stdlib_context()`, which sets
    # check_hostname=False and verify_mode=CERT_NONE -- so any certificate at
    # all is accepted and anyone able to intercept the connection collects the
    # password and reads the mailbox. `create_default_context()` is the one that
    # verifies, and it is what send() twenty lines down has always used.
    try:
        connection = imaplib.IMAP4_SSL(
            host, port, ssl_context=ssl.create_default_context(), timeout=TIMEOUT
        )
    except Exception as exc:  # noqa: BLE001
        raise ConnectorError(f"Couldn't reach {host}: {exc}") from exc

    try:
        connection.login(config.username, config.secret())
    except AuthFailed:
        raise
    except imaplib.IMAP4.error as exc:
        # Distinguished from a network fault so the reply can offer re-entry
        # rather than "try again later" (REQ-27).
        raise AuthFailed(
            f"{host} rejected the login for '{config.label}'. If you use 2FA you need an "
            f"app password. Run: /connect mail {config.label}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise ConnectorError(f"Login to {host} failed: {exc}") from exc

    return connection


class _Session:
    """Context manager that always logs out, even on failure."""

    def __init__(self, config: ConnectorConfig, folder: str = "INBOX", readonly: bool = True):
        self.config = config
        self.folder = folder
        self.readonly = readonly
        self.connection: imaplib.IMAP4_SSL | None = None

    def __enter__(self) -> imaplib.IMAP4_SSL:
        self.connection = _connect(self.config)
        status, _ = self.connection.select(self.folder, readonly=self.readonly)
        if status != "OK":
            self.connection.logout()
            raise ConnectorError(f"Couldn't open the folder '{self.folder}'.")
        return self.connection

    def __exit__(self, *exc_info: Any) -> None:
        if self.connection is not None:
            try:
                self.connection.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                self.connection.logout()
            except Exception:  # noqa: BLE001
                pass


def _decode(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return str(raw).strip()


def _extract_body(message: email.message.EmailMessage) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_content()
                except Exception:  # noqa: BLE001
                    continue
        return ""
    try:
        return message.get_content()
    except Exception:  # noqa: BLE001
        return ""


def _to_message(uid: str, raw_bytes: bytes, folder: str, unread: bool) -> Message:
    parsed = email.message_from_bytes(raw_bytes, policy=email.policy.default)

    sender_name, sender = "", ""
    from_header = parsed.get("From")
    if from_header is not None:
        addresses = getattr(from_header, "addresses", ())
        if addresses:
            sender_name = addresses[0].display_name or ""
            sender = addresses[0].addr_spec or ""
        else:
            sender = _decode(from_header)

    when = None
    if parsed.get("Date"):
        try:
            when = parsedate_to_datetime(parsed["Date"])
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            when = None

    body = _extract_body(parsed)
    collapsed = re.sub(r"\s+", " ", body).strip()

    return Message(
        uid=uid,
        sender=sender,
        sender_name=sender_name,
        subject=_decode(parsed.get("Subject")) or "(no subject)",
        date=when,
        snippet=collapsed[:300],
        unread=unread,
        folder=folder,
        body=collapsed[:MAX_BODY_CHARS],
    )


def fetch_unread(config: ConnectorConfig, limit: int = 25, days: int = 14) -> list[Message]:
    """Unread messages, newest first."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
    messages: list[Message] = []

    with _Session(config) as connection:
        status, data = connection.search(None, "UNSEEN", "SINCE", since)
        if status != "OK":
            raise ConnectorError("The mail server refused the search.")

        uids = (data[0] or b"").split()
        for uid in reversed(uids[-limit:]):
            # BODY.PEEK leaves the unread flag alone. Plain BODY would silently
            # mark the user's mail as read just for summarising it.
            status, payload = connection.fetch(uid, "(BODY.PEEK[])")
            if status != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            messages.append(
                _to_message(uid.decode(), payload[0][1], config.label, unread=True)
            )

    messages.sort(key=lambda m: m.date or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return messages


def search_messages(
    config: ConnectorConfig, query: str, limit: int = 10, days: int = 365
) -> list[Message]:
    """Search recent mail.

    Bounded by date on purpose. IMAP TEXT search scans message bodies server
    side, and on a real mailbox that is enormous — the account this was first
    run against had 33,000 unread alone, where an unbounded search exceeds the
    connection timeout and returns nothing at all. A year covers what anyone
    actually asks about, and `days` widens it when they don't.
    """
    messages: list[Message] = []
    safe = query.replace('"', "")
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).strftime("%d-%b-%Y")

    with _Session(config) as connection:
        status, data = connection.search(None, "TEXT", f'"{safe}"', "SINCE", since)
        if status != "OK":
            raise ConnectorError("The mail server refused the search.")

        uids = (data[0] or b"").split()
        for uid in reversed(uids[-limit:]):
            status, payload = connection.fetch(uid, "(BODY.PEEK[])")
            if status != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            messages.append(
                _to_message(uid.decode(), payload[0][1], config.label, unread=False)
            )
    return messages


def modify_flags(config: ConnectorConfig, uids: list[str], flag: str, add: bool = True) -> int:
    """Set or clear a flag. Gated, and reversible by calling again."""
    if not uids:
        return 0
    changed = 0
    with _Session(config, readonly=False) as connection:
        for uid in uids:
            status, _ = connection.store(uid, "+FLAGS" if add else "-FLAGS", flag)
            if status == "OK":
                changed += 1
    return changed


def build_draft(
    config: ConnectorConfig, *, to: str, subject: str, body: str, reply_to_uid: str = ""
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = config.from_address or config.username
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    return message


def send(config: ConnectorConfig, message: EmailMessage) -> str:
    """Send a message. Only ever called after per-message confirmation (REQ-14)."""
    host = config.smtp_host or config.host.replace("imap.", "smtp.")
    if not host:
        raise ConnectorError(f"Mail account '{config.label}' has no smtp_host configured.")
    port = config.smtp_port or 587

    try:
        context = ssl.create_default_context()
        # Annotated as the base class: 465 is implicit TLS and 587 is STARTTLS,
        # and the two branches produce different types for the same variable.
        server: smtplib.SMTP
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=TIMEOUT)
        with server:
            if port != 465:
                server.starttls(context=context)
            server.login(config.username, config.secret())
            server.send_message(message)
    except AuthFailed:
        raise
    except smtplib.SMTPAuthenticationError as exc:
        raise AuthFailed(
            f"{host} rejected the login for '{config.label}'. "
            f"Run: /connect mail {config.label}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise ConnectorError(f"The message wasn't sent: {exc}") from exc

    return str(message["To"])


def check(config: ConnectorConfig) -> dict[str, Any]:
    """Verify a connection without changing anything, for setup feedback."""
    try:
        with _Session(config) as connection:
            status, data = connection.search(None, "UNSEEN")
            count = len((data[0] or b"").split()) if status == "OK" else 0
        return {"ok": True, "unread": count, "label": config.label}
    except (ConnectorError, AuthFailed) as exc:
        return {"ok": False, "error": str(exc), "label": config.label}

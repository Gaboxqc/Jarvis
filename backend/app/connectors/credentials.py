"""Credential storage — REQ-26.

Secrets live in the operating system's credential store (Windows Credential
Manager, macOS Keychain, Secret Service). Configuration files hold only an
opaque reference, never the secret itself, so `kai.config.yaml` stays safe to
open in front of someone, commit by accident, or paste into a bug report.

Passwords are entered by the user directly into a terminal prompt that does not
echo. They are not passed as arguments, not written to disk, and not logged —
the value goes from the keyboard into the OS store and nowhere else.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

SERVICE = "kai-assistant"


class CredentialError(Exception):
    """The credential store is unavailable or refused."""


def _keyring() -> Any:
    try:
        import keyring
    except ImportError as exc:
        raise CredentialError(
            "The 'keyring' package isn't installed, so credentials can't be stored securely."
        ) from exc
    return keyring


def reference(kind: str, label: str) -> str:
    """The opaque handle written into config in place of a secret."""
    return f"{kind}:{label}"


def store(ref: str, secret: str) -> None:
    """Save a secret. The caller must never log or echo `secret`."""
    if not secret:
        raise CredentialError("An empty password can't be stored.")
    try:
        _keyring().set_password(SERVICE, ref, secret)
    except Exception as exc:  # noqa: BLE001
        raise CredentialError(f"The credential store refused to save this: {exc}") from exc
    # Deliberately logs the reference only — never the value.
    log.info("stored credential for %s", ref)


def fetch(ref: str) -> str | None:
    if not ref:
        return None
    try:
        return _keyring().get_password(SERVICE, ref)
    except Exception as exc:  # noqa: BLE001
        raise CredentialError(f"The credential store couldn't be read: {exc}") from exc


def delete(ref: str) -> bool:
    try:
        _keyring().delete_password(SERVICE, ref)
        log.info("removed credential for %s", ref)
        return True
    except Exception:  # noqa: BLE001 — deleting something absent is not an error
        return False


def has(ref: str) -> bool:
    try:
        return fetch(ref) is not None
    except CredentialError:
        return False


@dataclass
class StoreStatus:
    available: bool
    backend: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"available": self.available, "backend": self.backend, "detail": self.detail}


def status() -> StoreStatus:
    try:
        backend = _keyring().get_keyring()
    except CredentialError as exc:
        return StoreStatus(available=False, backend="none", detail=str(exc))

    name = type(backend).__name__
    # The "fail" and "null" backends silently discard everything written to
    # them. Storing a password into one and reporting success would be worse
    # than refusing, so treat them as unavailable.
    if "Fail" in name or "Null" in name:
        return StoreStatus(
            available=False,
            backend=name,
            detail="No usable OS credential store was found on this machine.",
        )
    return StoreStatus(available=True, backend=name)


def prompt_and_store(ref: str, prompt: str = "Password") -> bool:
    """Read a secret from the terminal without echoing, and store it.

    This is the only path by which a password enters the system. It is
    interactive on purpose: the user types it themselves, so it never appears in
    a command line, a config file, a log, or a conversation.
    """
    import getpass

    try:
        secret = getpass.getpass(f"{prompt}: ")
    except (EOFError, KeyboardInterrupt):
        return False
    if not secret:
        return False
    store(ref, secret)
    return True

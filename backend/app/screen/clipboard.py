"""Clipboard access — REQ-17, REQ-26.

Reads and writes the Windows clipboard through ctypes, with no dependency. The
clipboard is the highest-value half of "help me with this": most of the time the
thing someone wants explained, translated or rewritten is text they can copy,
and reading it costs nothing and needs no OCR.

Read only when asked. Nothing here polls or watches the clipboard.
"""

from __future__ import annotations

import ctypes
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
MAX_CHARS = 20_000


class ClipboardUnavailable(Exception):
    """The clipboard could not be read or written."""


def is_supported() -> bool:
    return os.name == "nt"


def _user32() -> Any:
    if not is_supported():
        raise ClipboardUnavailable("Clipboard access is Windows-only in this build.")
    return ctypes.windll.user32


def _open(retries: int = 5) -> None:
    """Take the clipboard, retrying briefly.

    Windows hands the clipboard to one process at a time, and something else
    holding it for a few milliseconds is normal rather than an error.
    """
    import time

    user32 = _user32()
    for attempt in range(retries):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.05 * (attempt + 1))
    raise ClipboardUnavailable("Another program is holding the clipboard right now.")


def has_text() -> bool:
    try:
        return bool(_user32().IsClipboardFormatAvailable(CF_UNICODETEXT))
    except ClipboardUnavailable:
        return False


def read_text() -> str:
    """Return clipboard text, or "" when it holds something that isn't text."""
    user32 = _user32()
    kernel32 = ctypes.windll.kernel32

    if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return ""

    _open()
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        kernel32.GlobalLock.restype = ctypes.c_void_p
        pointer = kernel32.GlobalLock(ctypes.c_void_p(handle))
        if not pointer:
            return ""
        try:
            text = ctypes.c_wchar_p(pointer).value or ""
        finally:
            kernel32.GlobalUnlock(ctypes.c_void_p(handle))
    finally:
        user32.CloseClipboard()

    return text[:MAX_CHARS]


def write_text(text: str) -> bool:
    """Replace the clipboard contents."""
    user32 = _user32()
    kernel32 = ctypes.windll.kernel32

    data = str(text)
    size = (len(data) + 1) * ctypes.sizeof(ctypes.c_wchar)

    _open()
    try:
        user32.EmptyClipboard()
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            raise ClipboardUnavailable("Couldn't allocate memory for the clipboard.")

        kernel32.GlobalLock.restype = ctypes.c_void_p
        pointer = kernel32.GlobalLock(ctypes.c_void_p(handle))
        if not pointer:
            raise ClipboardUnavailable("Couldn't lock clipboard memory.")
        try:
            ctypes.memmove(pointer, ctypes.create_unicode_buffer(data), size)
        finally:
            kernel32.GlobalUnlock(ctypes.c_void_p(handle))

        # Ownership of the handle passes to the system on success; it must not
        # be freed here.
        if not user32.SetClipboardData(CF_UNICODETEXT, ctypes.c_void_p(handle)):
            raise ClipboardUnavailable("Windows refused the clipboard write.")
    finally:
        user32.CloseClipboard()

    return True


def describe(text: str) -> str:
    words = len(text.split())
    lines = len(text.splitlines())
    return f"{words} words, {lines} line(s)"

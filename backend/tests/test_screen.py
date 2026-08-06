"""Screen and clipboard assistance — REQ-17, REQ-26, REQ-31.

No test captures the real screen. The capture layer is stubbed, and what is
under test is the contract: that a capture only ever happens inside an explicit
call, that it is disclosed, and that the image is never persisted.
"""

from __future__ import annotations

import inspect

import pytest

from app.actions import gate
from app.screen import capture, clipboard
from app.skills.base import SkillContext


# -- capture only on request (REQ-17) -------------------------------------


def test_nothing_captures_the_screen_outside_an_explicit_call():
    """There must be no watcher, timer or background thread taking pictures."""
    source = inspect.getsource(capture)

    assert "Thread" not in source
    assert "while True" not in source
    for scheduled in ("schedule", "interval", "poll"):
        assert scheduled not in source.lower().replace("polling", "")


def test_the_captured_image_is_never_written_anywhere():
    """REQ-26 — only extracted text may leave this module."""
    source = inspect.getsource(capture)

    for forbidden in ("write_bytes", "imwrite", "save(", "open(", "to_png", "tofile"):
        assert forbidden not in source, f"capture.py should not call {forbidden}"


def test_the_clipboard_is_only_read_when_asked():
    source = inspect.getsource(clipboard)

    assert "Thread" not in source
    assert "while True" not in source


# -- clipboard ------------------------------------------------------------


def test_reading_the_clipboard_returns_its_text(workspace, monkeypatch):
    monkeypatch.setattr(clipboard, "is_supported", lambda: True)
    monkeypatch.setattr(clipboard, "read_text", lambda: "Se ruega no fumar")

    outcome = gate.submit("screen.clipboard", {}, SkillContext())

    assert outcome.status == gate.EXECUTED
    assert "Se ruega no fumar" in outcome.message
    # The read is disclosed, not silent.
    assert "clipboard" in outcome.message.lower()


def test_an_empty_clipboard_is_reported_not_guessed_around(workspace, monkeypatch):
    monkeypatch.setattr(clipboard, "is_supported", lambda: True)
    monkeypatch.setattr(clipboard, "read_text", lambda: "   ")

    outcome = gate.submit("screen.clipboard", {}, SkillContext())

    assert outcome.status == gate.FAILED
    assert "empty" in (outcome.error or "")


def test_a_busy_clipboard_is_reported_conversationally(workspace, monkeypatch):
    def busy():
        raise clipboard.ClipboardUnavailable("Another program is holding the clipboard right now.")

    monkeypatch.setattr(clipboard, "is_supported", lambda: True)
    monkeypatch.setattr(clipboard, "read_text", busy)

    outcome = gate.submit("screen.clipboard", {}, SkillContext())

    assert outcome.status == gate.FAILED
    assert "holding the clipboard" in (outcome.error or "")


def test_copying_preserves_what_was_there_so_it_can_be_undone(workspace, monkeypatch):
    """Replacing someone's clipboard without a way back loses what they held."""
    state = {"value": "the original contents"}
    monkeypatch.setattr(clipboard, "read_text", lambda: state["value"])
    monkeypatch.setattr(clipboard, "write_text",
                        lambda text: state.__setitem__("value", text) or True)

    outcome = gate.submit("screen.copy", {"text": "the rewritten version"}, SkillContext())
    assert outcome.status == gate.EXECUTED
    assert state["value"] == "the rewritten version"

    from app.actions import undo

    assert undo.undo_last().ok
    assert state["value"] == "the original contents"


def test_copying_nothing_is_refused(workspace, monkeypatch):
    monkeypatch.setattr(clipboard, "read_text", lambda: "")
    monkeypatch.setattr(clipboard, "write_text", lambda text: True)

    outcome = gate.submit("screen.copy", {"text": "   "}, SkillContext())

    assert outcome.status == gate.FAILED


# -- screen reading -------------------------------------------------------


def stub_screen(monkeypatch, lines: list[str], title: str = "Notepad"):
    monkeypatch.setattr(capture, "is_available", lambda: True)
    monkeypatch.setattr(
        capture, "read",
        lambda full_screen=False: capture.ScreenText(
            text="\n".join(lines), lines=lines, window_title=title,
            width=1920, height=1080, seconds=4.2,
        ),
    )


def test_reading_the_screen_discloses_the_capture(workspace, monkeypatch):
    """A capture the user didn't notice is indistinguishable from an unauthorised one."""
    stub_screen(monkeypatch, ["Invoice 2026-014", "Total: 1,240.00 EUR"])

    outcome = gate.submit("screen.read", {}, SkillContext())

    assert outcome.status == gate.EXECUTED
    assert "Captured" in outcome.message
    assert "Notepad" in outcome.message
    assert "wasn't saved" in outcome.message
    assert "1,240.00 EUR" in outcome.message


def test_a_screen_with_no_text_says_so(workspace, monkeypatch):
    stub_screen(monkeypatch, [])

    outcome = gate.submit("screen.read", {}, SkillContext())

    assert outcome.status == gate.EXECUTED
    assert "no readable text" in outcome.message


def test_missing_ocr_is_reported_clearly(workspace, monkeypatch):
    monkeypatch.setattr(capture, "is_available", lambda: False)

    outcome = gate.submit("screen.read", {}, SkillContext())

    assert outcome.status == gate.FAILED
    assert "isn't set up" in (outcome.error or "")


def test_no_active_window_is_reported_not_crashed(workspace, monkeypatch):
    monkeypatch.setattr(capture, "is_available", lambda: True)

    def none_active(full_screen=False):
        raise capture.ScreenUnavailable("There's no active window to read.")

    monkeypatch.setattr(capture, "read", none_active)

    outcome = gate.submit("screen.read", {}, SkillContext())

    assert outcome.status == gate.FAILED
    assert "no active window" in (outcome.error or "").lower()


# -- resource handling (REQ-31) -------------------------------------------


def test_the_ocr_engine_is_not_loaded_until_used(workspace):
    assert capture._engine is None


def test_an_idle_ocr_engine_is_released(workspace, monkeypatch):
    monkeypatch.setattr(capture, "_engine", object())
    monkeypatch.setattr(capture, "_last_used", 0.0)  # long ago on the monotonic clock

    assert capture.unload_if_idle() is True
    assert capture._engine is None


def test_a_recently_used_engine_is_kept(workspace, monkeypatch):
    import time

    monkeypatch.setattr(capture, "_engine", object())
    monkeypatch.setattr(capture, "_last_used", time.monotonic())

    assert capture.unload_if_idle() is False


# -- downscaling ----------------------------------------------------------


def test_large_captures_are_scaled_down_before_ocr():
    import numpy as np

    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    scaled, was_scaled = capture._downscale(image)

    assert was_scaled
    assert scaled.shape[1] == capture.MAX_OCR_WIDTH


def test_small_captures_are_left_alone():
    import numpy as np

    image = np.zeros((400, 600, 3), dtype=np.uint8)
    scaled, was_scaled = capture._downscale(image)

    assert not was_scaled
    assert scaled.shape == image.shape

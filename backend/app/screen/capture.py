"""Screen capture and text extraction — REQ-17, REQ-26, REQ-31.

Takes a picture of the active window (or the whole screen) and reads the text
out of it, so the assistant can answer about something the user is looking at
but hasn't got as text.

Three properties matter more than the mechanics:

* **Only on request.** There is no watcher, no polling, no background capture.
  A capture happens inside one skill call and nowhere else.
* **Never written to disk.** The image lives in memory for the length of the
  call; only the extracted text goes any further (REQ-26).
* **The active window by default.** It's what "explain this" almost always
  means, it's smaller so it's faster, and it avoids sweeping in whatever else
  happens to be on the other monitor.

OCR is local, CPU-only, and honestly slow — several seconds for a full window.
That is the cost of not shipping a cloud vision call, and it is paid only when
explicitly asked for.
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# Beyond this width the image is scaled down before OCR. Recognition holds up
# well and the time saved is substantial.
MAX_OCR_WIDTH = 1400
MAX_TEXT_CHARS = 12_000
UNLOAD_AFTER_SECONDS = 600


class ScreenUnavailable(Exception):
    """The screen could not be captured, or no text engine is available."""


@dataclass
class ScreenText:
    text: str
    lines: list[str] = field(default_factory=list)
    window_title: str = ""
    width: int = 0
    height: int = 0
    seconds: float = 0.0
    scaled: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "window": self.window_title,
            "lines": len(self.lines),
            "characters": len(self.text),
            "size": f"{self.width}x{self.height}",
            "seconds": round(self.seconds, 1),
        }


def _np() -> Any:
    import numpy

    return numpy


# -- capture ---------------------------------------------------------------


def active_window() -> tuple[dict[str, int], str]:
    """The foreground window's screen rectangle and title."""
    if os.name != "nt":
        raise ScreenUnavailable("Window capture is Windows-only in this build.")

    user32 = ctypes.windll.user32
    try:
        # Without this the rectangle is reported in scaled coordinates on a
        # high-DPI display and the grab lands in the wrong place.
        user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass

    handle = user32.GetForegroundWindow()
    if not handle:
        raise ScreenUnavailable("There's no active window to read.")

    rect = wintypes.RECT()
    if not user32.GetWindowRect(handle, ctypes.byref(rect)):
        raise ScreenUnavailable("Couldn't measure the active window.")

    buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(handle, buffer, 512)

    region = {
        "left": rect.left,
        "top": rect.top,
        "width": max(1, rect.right - rect.left),
        "height": max(1, rect.bottom - rect.top),
    }
    return region, buffer.value or ""


def grab(full_screen: bool = False) -> tuple[Any, str]:
    """Capture pixels. Returns an RGB array and a description of what was shot."""
    try:
        import mss
    except ImportError as exc:
        raise ScreenUnavailable("Screen capture isn't installed (missing 'mss').") from exc

    np = _np()
    if full_screen:
        with mss.MSS() as sct:
            # monitors[1] is the primary display; monitors[0] is every monitor
            # joined together, which is both slower and rarely what is meant.
            monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            shot = sct.grab(monitor)
        title = "the screen"
    else:
        region, title = active_window()
        with mss.MSS() as sct:
            shot = sct.grab(region)
        title = title or "the active window"

    image = np.array(shot)[:, :, :3][:, :, ::-1]  # BGRA -> RGB
    return np.ascontiguousarray(image), title


def _downscale(image: Any) -> tuple[Any, bool]:
    np = _np()
    height, width = image.shape[:2]
    if width <= MAX_OCR_WIDTH:
        return image, False
    scale = MAX_OCR_WIDTH / width
    new_w, new_h = int(width * scale), int(height * scale)
    rows = (np.arange(new_h) * (height / new_h)).astype(int)
    cols = (np.arange(new_w) * (width / new_w)).astype(int)
    return np.ascontiguousarray(image[rows][:, cols]), True


# -- OCR -------------------------------------------------------------------

_lock = threading.Lock()
_engine: Any = None
_last_used = 0.0


def is_available() -> bool:
    import importlib.util

    return (
        importlib.util.find_spec("mss") is not None
        and importlib.util.find_spec("rapidocr_onnxruntime") is not None
    )


def _ocr_engine() -> Any:
    """Load the OCR engine once, on first real use."""
    global _engine, _last_used

    with _lock:
        if _engine is not None:
            _last_used = time.monotonic()
            return _engine
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise ScreenUnavailable(
                "Reading text from the screen isn't installed "
                "(missing 'rapidocr-onnxruntime')."
            ) from exc
        log.info("loading OCR engine")
        _engine = RapidOCR()
        _last_used = time.monotonic()
        return _engine


def unload_if_idle() -> bool:
    """REQ-31 — don't hold the OCR models for a one-off screen read."""
    global _engine
    if _engine is None:
        return False
    if time.monotonic() - _last_used < UNLOAD_AFTER_SECONDS:
        return False
    with _lock:
        _engine = None
    log.info("released OCR engine")
    return True


def read(full_screen: bool = False) -> ScreenText:
    """Capture and read the text. The image is discarded when this returns."""
    started = time.monotonic()
    image, title = grab(full_screen=full_screen)
    height, width = image.shape[:2]

    prepared, scaled = _downscale(image)
    engine = _ocr_engine()

    try:
        result, _elapsed = engine(prepared)
    except Exception as exc:  # noqa: BLE001
        raise ScreenUnavailable(f"Couldn't read the screen: {exc}") from exc
    finally:
        # Drop the pixels as soon as they are no longer needed. They are never
        # written anywhere (REQ-17, REQ-26).
        del image, prepared

    lines = [str(entry[1]).strip() for entry in (result or []) if len(entry) > 1]
    lines = [line for line in lines if line]

    return ScreenText(
        text="\n".join(lines)[:MAX_TEXT_CHARS],
        lines=lines,
        window_title=title,
        width=width,
        height=height,
        seconds=time.monotonic() - started,
        scaled=scaled,
    )

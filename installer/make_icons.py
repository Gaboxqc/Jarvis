"""Generate the application icon set — REQ-29.

The icon shipped until now was a placeholder: a blue dot on a white square,
written to unblock packaging and never revisited. It is the first thing anyone
sees of this app, in the tray, the taskbar and the installer, and a placeholder
there reads as unfinished software.

This is a script rather than a folder of binaries so the mark can be changed by
editing numbers and re-running, and so anyone reading the repo can see what the
icon *is* instead of opening fifteen PNGs. Run it after changing anything here:

    python installer/make_icons.py

The mark is the brand logo -- icons/Logo.png, supplied by the owner -- set on a
rounded square in the app's own panel colour. An earlier version of this script
drew a ring and a dot, which was a stand-in until there was a real mark; it is
gone.

Two things are done to the logo rather than pasting it as-is. Its transparent
margins are trimmed, because the source is a tall portrait canvas and centring
it untrimmed would leave the bolt small and high. And it is fitted to a share of
the tile rather than a fixed size, so the same script still works if the logo is
ever replaced with one of different proportions.

Everything is composed at 1024 and downsampled with LANCZOS: generating each
size directly leaves visible stair-stepping on the diagonals, which this mark is
made almost entirely of.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ICONS = Path(__file__).resolve().parent.parent / "ui" / "src-tauri" / "icons"

MASTER = 1024

# ui/src/styles.css: --panel, --bg, --accent, --text
PANEL = (27, 30, 36, 255)
BACKDROP = (20, 22, 26, 255)
ACCENT = (106, 166, 255, 255)
BRIGHT = (231, 234, 240, 255)

# Windows tiles, the .ico members, and the macOS bundle.
PNG_SIZES = {
    "32x32.png": 32,
    "64x64.png": 64,
    "128x128.png": 128,
    "128x128@2x.png": 256,
    "icon.png": 512,
    "Square30x30Logo.png": 30,
    "Square44x44Logo.png": 44,
    "Square71x71Logo.png": 71,
    "Square89x89Logo.png": 89,
    "Square107x107Logo.png": 107,
    "Square142x142Logo.png": 142,
    "Square150x150Logo.png": 150,
    "Square284x284Logo.png": 284,
    "Square310x310Logo.png": 310,
    "StoreLogo.png": 50,
}

ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


LOGO = ICONS / "Logo.png"

# How much of the tile's width the mark occupies. Below about 0.5 it looks lost;
# above about 0.7 it collides with the rounded corners at small sizes.
LOGO_SCALE = 0.62


def trimmed_logo() -> Image.Image:
    """The brand mark with its transparent margins removed.

    The source is a tall canvas with the bolt inset. Pasting it whole would
    centre the *canvas* rather than the mark, leaving it small and sitting high
    on the tile.
    """
    logo = Image.open(LOGO).convert("RGBA")
    box = logo.getbbox()  # ignores fully transparent edges
    return logo.crop(box) if box else logo


def draw_master() -> Image.Image:
    """The icon at 1024, from which every other size is reduced."""
    image = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Rounded square. Windows already masks the taskbar icon, but the tray and
    # the installer show it raw, so the shape has to be in the artwork.
    radius = int(MASTER * 0.22)
    draw.rounded_rectangle([0, 0, MASTER - 1, MASTER - 1], radius=radius, fill=PANEL)

    # A gentle top-to-bottom darkening. Flat fill looks like a placeholder;
    # this is subtle enough to survive being scaled to 16 pixels and reads as
    # depth rather than as a gradient.
    gradient = Image.new("RGBA", (1, MASTER))
    for y in range(MASTER):
        blend = y / (MASTER - 1)
        gradient.putpixel((0, y), tuple(
            int(PANEL[i] + (BACKDROP[i] - PANEL[i]) * blend) for i in range(4)
        ))
    gradient = gradient.resize((MASTER, MASTER))
    mask = Image.new("L", (MASTER, MASTER), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, MASTER - 1, MASTER - 1], radius=radius, fill=255
    )
    image.paste(gradient, (0, 0), mask)
    draw = ImageDraw.Draw(image)

    mark = trimmed_logo()
    # Fit inside a square box while keeping the logo's own aspect ratio, so a
    # tall mark stays tall rather than being squashed into the tile.
    target = int(MASTER * LOGO_SCALE)
    ratio = min(target / mark.width, target / mark.height)
    mark = mark.resize(
        (max(1, int(mark.width * ratio)), max(1, int(mark.height * ratio))), Image.LANCZOS
    )

    image.paste(
        mark,
        ((MASTER - mark.width) // 2, (MASTER - mark.height) // 2),
        mark,  # its own alpha, so the rounded tile shows through around it
    )

    return image


def write_all() -> int:
    if not ICONS.exists():
        print(f"no icons directory at {ICONS}", file=sys.stderr)
        return 1

    master = draw_master()

    for name, size in PNG_SIZES.items():
        master.resize((size, size), Image.LANCZOS).save(ICONS / name)
    print(f"wrote {len(PNG_SIZES)} PNGs")

    # Pillow builds the .ico from the sizes given, so every member is a proper
    # downsample rather than the shell scaling one bitmap badly.
    master.save(ICONS / "icon.ico", sizes=[(s, s) for s in ICO_SIZES])
    print(f"wrote icon.ico with {len(ICO_SIZES)} sizes")

    # macOS is not a build target today, but the bundle config lists this file
    # and a stale one would be the old placeholder.
    try:
        master.save(ICONS / "icon.icns")
        print("wrote icon.icns")
    except Exception as exc:  # noqa: BLE001
        print(f"skipped icon.icns ({exc}); not a build target on this platform")

    return 0


if __name__ == "__main__":
    raise SystemExit(write_all())

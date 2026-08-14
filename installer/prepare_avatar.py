"""Prepare the Live2D avatar for shipping — REQ-29, REQ-31.

The model as supplied carries an 8192x8192 texture: 26 MB for something drawn
at roughly 300 pixels on screen. That is about 27x more resolution than can ever
be resolved, and it would cost 26 MB of installer, a slow first paint, and 256 MB
of GPU memory once uploaded as a texture.

This copies the model into ui/public/live2d and downscales the textures on the
way. The source in Alexia/ is never modified -- re-running this is always safe,
and the original stays available if a larger size is ever wanted.

    python installer/prepare_avatar.py

The Cubism Core runtime is deliberately not fetched here. It is proprietary,
distributed by Live2D under a licence the operator has to accept, and accepting
that on someone's behalf is not this script's business. Drop
`live2dcubismcore.min.js` into ui/public/live2d/ and the avatar starts working;
until then the app runs exactly as before and the avatar panel explains itself.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "Alexia" / "Alexia"
TARGET = ROOT / "ui" / "public" / "live2d"

# 2048 is the first size where the model is indistinguishable at any window this
# app opens, and it divides the source cleanly so the downsample stays sharp.
MAX_TEXTURE = 2048


def prepare() -> int:
    if not (SOURCE / "Alexia.model3.json").exists():
        print(f"no model at {SOURCE}", file=sys.stderr)
        return 1

    TARGET.mkdir(parents=True, exist_ok=True)
    model = json.loads((SOURCE / "Alexia.model3.json").read_text(encoding="utf-8"))
    files = model["FileReferences"]

    # Everything that is not a texture is copied verbatim: the .moc3 is a binary
    # the runtime parses, and the JSON files reference each other by name.
    plain = [files["Moc"], files.get("Physics"), files.get("DisplayInfo")]
    plain += [entry["File"] for entry in files.get("Expressions", [])]
    for group in files.get("Motions", {}).values():
        plain += [entry["File"] for entry in group]

    for name in filter(None, plain):
        source = SOURCE / name
        if not source.exists():
            print(f"  missing, skipped: {name}")
            continue
        destination = TARGET / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    print(f"copied {len(list(filter(None, plain)))} model files")

    saved = 0
    for name in files["Textures"]:
        source = SOURCE / name
        destination = TARGET / name
        destination.parent.mkdir(parents=True, exist_ok=True)

        with Image.open(source) as image:
            before = source.stat().st_size
            if max(image.size) > MAX_TEXTURE:
                ratio = MAX_TEXTURE / max(image.size)
                resized = image.resize(
                    (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))),
                    Image.LANCZOS,
                )
            else:
                resized = image.copy()
            # optimize=True costs a second and takes another chunk off a texture
            # that is mostly transparent.
            resized.save(destination, "PNG", optimize=True)

        after = destination.stat().st_size
        saved += before - after
        print(f"  {name}: {before / 1e6:.1f} MB -> {after / 1e6:.1f} MB")

    shutil.copy2(SOURCE / "Alexia.model3.json", TARGET / "Alexia.model3.json")
    print(f"\nsaved {saved / 1e6:.1f} MB")

    core = TARGET / "live2dcubismcore.min.js"
    if core.exists():
        print("cubism core: present")
    else:
        print(
            "cubism core: NOT present -- the avatar will explain itself and the\n"
            "             rest of the app is unaffected. Download it from\n"
            "             live2d.com and place it at\n"
            f"             {core}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(prepare())

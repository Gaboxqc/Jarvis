"""Freeze the voice cloning engine — REQ-4, REQ-29.

Built from .venv-xtts, not the backend's environment. That separation is the
whole point: torch brings its own numpy, transformers and numba, and installing
it beside the backend would upgrade numpy underneath onnxruntime and piper.

    .venv-xtts\Scripts\pyinstaller.exe installer\kai-xtts.spec --noconfirm

The result is large -- most of a gigabyte before compression -- which is why it
is fetched on demand instead of shipped in the installer. Anyone who never
clones a voice never downloads it.

console=False so no window flashes when the backend starts it. The process
speaks over the pipes it is given; see sidecar/xtts_main.py for why file
descriptor 1 is treated as protocol and everything else goes to a log.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR.parent
ENTRY = ROOT / "sidecar" / "xtts_main.py"

if not ENTRY.exists():
    raise SystemExit(f"no sidecar entry point at {ENTRY}")

datas, binaries, hidden = [], [], []

# TTS and its config stack ship JSON and model definitions as package data;
# without collect_all the frozen build imports and then cannot find them.
for package in (
    "TTS",
    "coqpit_config",
    "trainer",
    "librosa",
    "transformers",
    "tokenizers",
    "num2words",
    "inflect",
    "pysbd",
    "anyascii",
    "encodec",
    "einops",
):
    try:
        found_datas, found_binaries, found_hidden = collect_all(package)
    except Exception as exc:  # noqa: BLE001 - optional across TTS versions
        print(f"[spec] skipped {package}: {exc}")
        continue
    datas += found_datas
    binaries += found_binaries
    hidden += found_hidden

print(f"[spec] xtts bundle: {len(datas)} data entries, {len(hidden)} hidden imports")

analysis = Analysis(
    [str(ENTRY)],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hidden)),
    hookspath=[],
    runtime_hooks=[],
    # None of this is reachable from a synthesis request, and each costs
    # hundreds of megabytes in a bundle that is already too big.
    excludes=["tkinter", "matplotlib", "PySide6", "PyQt5", "pytest", "IPython", "tensorboard"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="kai-xtts",
    console=False,
    debug=False,
    strip=False,
    upx=False,
)

collected = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="kai-xtts",
)

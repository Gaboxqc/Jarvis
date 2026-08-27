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

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

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
    # Needed for audio IO on PyTorch 2.9+, and its absence surfaces as a
    # completely unrelated complaint about a GPT-2 class.
    "torchcodec",
):
    try:
        found_datas, found_binaries, found_hidden = collect_all(package)
    except Exception as exc:  # noqa: BLE001 - optional across TTS versions
        print(f"[spec] skipped {package}: {exc}")
        continue
    datas += found_datas
    binaries += found_binaries
    hidden += found_hidden

# Package *metadata*, not code. transformers and torchaudio ask
# importlib.metadata for version numbers to decide what is available, and a
# frozen bundle has no dist-info unless it is copied in deliberately. When it is
# missing the failure is spectacularly misleading:
#
#     ModuleNotFoundError: Could not import module 'GPT2PreTrainedModel'
#       <- PackageNotFoundError: No package metadata was found for torchcodec
#
# Nothing about the visible error mentions metadata, or torchcodec, and the
# named class is a red herring -- it cost three rebuilds spent collecting model
# families that were present all along.
for distribution in (
    "torch",
    "torchaudio",
    "torchcodec",
    "transformers",
    "tokenizers",
    "safetensors",
    "huggingface-hub",
    "numpy",
    "coqui-tts",
    "librosa",
    "soundfile",
):
    try:
        datas += copy_metadata(distribution)
    except Exception as exc:  # noqa: BLE001 - names vary between versions
        print(f"[spec] no metadata for {distribution}: {exc}")

# transformers resolves model classes lazily through a module map, so nothing
# static references them and PyInstaller collects none of them. The frozen
# engine then fails at load with
#
#     ModuleNotFoundError: Could not import module 'GPT2PreTrainedModel'
#
# These are the families TTS actually imports -- found by reading its imports
# rather than guessing, and named individually rather than collecting all of
# transformers.models, which is hundreds of architectures this never touches.
for family in ("gpt2", "bert", "encodec", "hubert", "wav2vec2", "auto"):
    try:
        hidden += collect_submodules(f"transformers.models.{family}")
    except Exception as exc:  # noqa: BLE001
        print(f"[spec] could not collect transformers.models.{family}: {exc}")

# Test fixtures are dead weight in a synthesis engine, and they are also the
# longest paths in the bundle -- sklearn's openml fixtures alone run to a
# hundred characters, which on Windows is most of the way to MAX_PATH before
# the install directory is even prepended.
before = len(datas)
datas = [
    entry for entry in datas
    if "/tests/" not in entry[0].replace("\\", "/").lower()
    and "/test/" not in entry[0].replace("\\", "/").lower()
]
print(f"[spec] dropped {before - len(datas)} test-data entries")

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

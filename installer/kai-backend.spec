# PyInstaller spec for the Kai backend sidecar — REQ-29, REQ-30.
#
# Produces a single self-contained backend executable that the desktop app
# spawns, so the user never installs Python, pip or any of this.
#
# Two things make this app harder to freeze than most:
#
# 1. Skills are discovered at runtime with pkgutil.walk_packages. PyInstaller's
#    static analysis cannot see them, so a naive build ships an assistant with
#    zero capabilities that reports no error. Every subpackage is collected
#    explicitly below.
# 2. Several dependencies carry data files they load at runtime -- OCR weights,
#    espeak phoneme tables, tokenizer assets. Missing those fails only when the
#    feature is first used, which is exactly when a user is least able to
#    diagnose it.
#
# Build with:  pyinstaller installer/kai-backend.spec --noconfirm

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

SPEC_DIR = Path(SPECPATH).resolve()
PROJECT = SPEC_DIR.parent
BACKEND = PROJECT / "backend"

# -- skill manifest -------------------------------------------------------
#
# Regenerated on every build so the frozen app can find skills without a
# filesystem to walk. Failing here fails the build, which is correct: shipping
# without it produces an assistant with no capabilities and no error.

# collect_submodules() runs before Analysis and resolves packages against the
# live sys.path, not against `pathex`. Without backend/ on it, every
# collect_submodules("app.skills.*") returns nothing -- quietly -- and the
# bundle ships without the subpackages. The frozen app then reports
# "No module named 'app.skills.knowledge'" for each one and loads a single
# skill. Cost three full rebuilds to find; do not remove.
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(SPEC_DIR))
import generate_manifest  # noqa: E402

_modules = generate_manifest.discover()
if not _modules:
    raise SystemExit("No skill modules found - refusing to build an empty assistant")
generate_manifest.write(_modules)
print(f"[spec] skill manifest: {len(_modules)} modules")

# -- dynamically discovered code -----------------------------------------

hidden = ["app.skills._manifest"]
for package in (
    "app.skills",
    "app.skills.knowledge",
    "app.skills.planning",
    "app.skills.system",
    "app.skills.comms",
    "app.connectors",
    "app.capture",
    "app.index",
    "app.screen",
    "app.voice",
    "app.brain",
    "app.actions",
    "app.memory",
    "app.scheduler",
):
    found = collect_submodules(package)
    # A package of the app's own code that collects nothing means the import
    # failed, and the build would silently omit it. Better to stop here than to
    # ship an assistant missing a quarter of its capabilities.
    if not found:
        raise SystemExit(
            f"collect_submodules('{package}') found nothing - "
            f"is {BACKEND} on sys.path?"
        )
    hidden += found

# uvicorn resolves these by string name at startup.
hidden += [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

# keyring picks its backend by entry point; without this the frozen build finds
# none and silently behaves as if no credential store exists (REQ-26).
hidden += collect_submodules("keyring.backends")

# Optional heavy features. Absent ones are skipped rather than failing the build,
# so a slimmer install is still possible.
for optional in (
    "faster_whisper",
    "piper",
    "openwakeword",
    "rapidocr_onnxruntime",
    "onnxruntime",
    "ctranslate2",
    "mss",
    "icalendar",
    "caldav",
    "ddgs",
    "pypdf",
    "docx",
):
    try:
        hidden += collect_submodules(optional)
    except Exception:
        pass

# -- runtime data files ---------------------------------------------------

datas = []
for package in (
    "rapidocr_onnxruntime",   # OCR weights and config
    "piper",                  # espeak-ng phoneme data
    "faster_whisper",         # tokenizer assets
    "openwakeword",           # feature extractors
    "onnxruntime",
    "ctranslate2",
    "pint",                   # unit definition files
    "certifi",
):
    try:
        datas += collect_data_files(package)
    except Exception:
        pass

# The example config ships so a fresh install has something to copy.
example = PROJECT / "kai.config.example.yaml"
if example.exists():
    datas.append((str(example), "."))

# -- build ----------------------------------------------------------------

analysis = Analysis(
    [str(BACKEND / "server.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=datas,
    hiddenimports=sorted(set(hidden)),
    hookspath=[],
    runtime_hooks=[],
    # Nothing here is used by the backend, and each pulls in tens of megabytes.
    excludes=["tkinter", "matplotlib", "PySide6", "PyQt5", "pytest", "IPython"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="kai-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # A console window would flash up every launch when spawned as a sidecar.
    console=False,
    disable_windowed_traceback=False,
)

# One-directory rather than one-file: a --onefile build unpacks several hundred
# megabytes to a temp folder on every launch, which turns startup into a
# multi-second stall and leaves debris behind if the app is killed.
collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="kai-backend",
)

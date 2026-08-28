"""Packaging — REQ-29, REQ-33.

These exist because packaging broke this app in a way nothing else caught. The
frozen build started, served every endpoint, answered questions, and reported
itself healthy — with one skill out of forty-eight. Skill discovery walks the
filesystem, and PyInstaller had put the modules in an archive.

So the guards are tested here: that discovery has a source that survives
freezing, that a build which loses its skills fails loudly, and that the
manifest generator can never write an empty list.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]
GENERATOR = PROJECT / "installer" / "generate_manifest.py"
SPEC = PROJECT / "installer" / "kai-backend.spec"
SERVER = PROJECT / "backend" / "server.py"


def load_generator():
    sys.path.insert(0, str(GENERATOR.parent))
    import generate_manifest

    return generate_manifest


# -- the manifest ---------------------------------------------------------


def test_the_generator_finds_every_skill_module():
    generator = load_generator()
    modules = generator.discover()

    # Every skill package must be represented; a missing one is a quarter of
    # the assistant silently absent from a packaged build.
    for expected in (
        "app.skills.memory_skills",
        "app.skills.knowledge.search",
        "app.skills.planning.reminders",
        "app.skills.system.files",
        "app.skills.comms.mail_skills",
        "app.skills.knowledge.capture_skills",
        "app.skills.knowledge.screen_skills",
    ):
        assert expected in modules, f"{expected} missing from the manifest"


def test_the_generator_excludes_non_skill_modules():
    generator = load_generator()
    modules = generator.discover()

    for excluded in (
        "app.skills.base",
        "app.skills.registry",
        "app.skills._manifest",
        "app.skills.system.paths",  # a helper, but harmless if present
    ):
        if excluded.endswith(("base", "registry", "_manifest")):
            assert excluded not in modules


def test_the_manifest_matches_what_the_registry_discovers(workspace):
    """The two paths must agree, or freezing changes behaviour."""
    from app.skills.registry import load_skills

    generator = load_generator()
    from_disk = set(generator.discover())
    # Test doubles injected by the fixture live outside the app package and are
    # not part of what ships.
    imported = {
        skill.__class__.__module__
        for skill in load_skills().values()
        if skill.__class__.__module__.startswith("app.")
    }

    # Every module that actually defines a skill is in the manifest.
    assert imported <= from_disk, f"missing from manifest: {imported - from_disk}"
    assert len(imported) >= 15, "suspiciously few skill modules"


def test_an_empty_manifest_is_refused(monkeypatch, tmp_path):
    """Writing an empty list would package an assistant with no capabilities."""
    generator = load_generator()
    monkeypatch.setattr(generator, "SKILLS", tmp_path)  # no .py files here

    assert generator.main() == 1


# -- the build guards -----------------------------------------------------


def test_the_spec_puts_the_backend_on_sys_path():
    """The bug that cost three rebuilds.

    collect_submodules() resolves against the live sys.path, not `pathex`.
    Without backend/ on it every app.skills.* collection returned nothing and
    the bundle shipped without the subpackages.
    """
    spec = SPEC.read_text(encoding="utf-8")

    assert "sys.path.insert(0, str(BACKEND))" in spec


def test_the_spec_refuses_a_package_that_collects_nothing():
    spec = SPEC.read_text(encoding="utf-8")

    assert "found nothing" in spec
    assert "raise SystemExit" in spec


def test_the_selftest_has_a_meaningful_threshold():
    from app.skills.registry import load_skills

    server = SERVER.read_text(encoding="utf-8")

    assert "MIN_EXPECTED_SKILLS" in server
    # The threshold must actually be reachable by the real skill set, and high
    # enough that losing a package trips it.
    import re

    threshold = int(re.search(r"MIN_EXPECTED_SKILLS = (\d+)", server).group(1))
    actual = len(load_skills())
    assert threshold <= actual, f"threshold {threshold} above the real count {actual}"
    assert threshold >= actual * 0.75, "threshold too low to catch a lost package"


def test_the_build_script_gates_on_the_selftest():
    """A broken bundle must not reach an installer."""
    script = (PROJECT / "installer" / "build.ps1").read_text(encoding="utf-8")

    assert "--selftest" in script
    assert "refusing to package a broken build" in script.lower()


def test_the_build_script_runs_the_tests_before_it_freezes_anything():
    """The self-test asks whether packaging kept the skills. That is a much
    narrower question than whether the app works, and for a long time it was the
    only one being asked here -- there was no CI either, so a release could ship
    from a red suite with nothing to say so."""
    script = (PROJECT / "installer" / "build.ps1").read_text(encoding="utf-8")

    assert "pytest" in script
    assert "refusing to build an installer" in script.lower()
    # Ahead of the freeze, not after it: a bundle built from failing code is
    # wasted work even when it packages correctly.
    assert script.index("pytest") < script.index("PyInstaller")


def test_continuous_integration_runs_everything_the_build_script_does():
    """The build script is one person's machine. This is the other guard."""
    workflow = (PROJECT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    for command in ("ruff check", "mypy", "pytest", "tsc --noEmit", "npm test", "cargo check"):
        assert command in workflow, f"CI does not run {command}"


def test_the_watchdog_warms_numpy_before_it_starts_its_thread():
    """The ordering that keeps the packaged backend able to start.

    Once a thread is blocked reading stdin -- which is how the sidecar notices
    its parent dying -- `import numpy` never returns. Measured in a child
    process with a 180-second ceiling:

        no thread, then import numpy               ->  completes
        import numpy, then start the thread        ->  completes
        start the thread, then import numpy        ->  never completes
        start the thread, wait, then import numpy  ->  never completes

    So the backend would never bind its port, or -- with numpy imported lazily,
    which was the first attempt at a fix -- would start healthy and then wedge
    on the first semantic search, which is worse.

    Ordering is the fix, and it belongs in watch_parent() because that is the
    function that creates the hazard.
    """
    server_source = SERVER.read_text(encoding="utf-8")

    warm = server_source.index("_warm_imports_that_deadlock_against_this_thread()")
    thread = server_source.index('threading.Thread(target=wait, name="parent-watch"')
    assert warm < thread, "numpy must be warmed before the watchdog thread exists"


def test_importing_the_app_does_not_pull_in_numpy():
    """Defence in depth for the deadlock above, and a lean startup besides.

    watch_parent() warming numpy is what makes the backend correct; this keeps
    numpy off `app.main`'s import graph anyway, so the CLI and the tests do not
    pay for it and so a future module-scope import is a conversation rather than
    a surprise. voice/tts.py and screen/capture.py already import it inside
    functions; index/embeddings.py now does too.
    """
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; import app.main; "
         "sys.exit(1 if 'numpy' in sys.modules else 0)"],
        cwd=str(PROJECT / "backend"),
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert result.returncode == 0, (
        "importing app.main pulled numpy into sys.modules. Something now imports "
        "it at module scope, and the packaged backend will hang on startup."
    )


# -- health reporting -----------------------------------------------------


def test_health_reports_not_ok_when_no_skills_loaded(workspace, monkeypatch):
    """The one signal anyone checks must not lie about a broken build."""
    from fastapi.testclient import TestClient

    from app import main as main_module

    monkeypatch.setattr(main_module, "catalog", lambda: [])

    with TestClient(main_module.app) as client:
        health = client.get("/health").json()

    assert health["ok"] is False
    assert health["skills"] == 0
    assert "No skills loaded" in (health["problem"] or "")


def test_health_reports_which_backend_is_running(workspace):
    """It used to report 0.1.0 forever, because that string was typed into the
    FastAPI constructor and never touched again. After an update, "which build
    is this" is the first question, and this endpoint could not answer it."""
    from fastapi.testclient import TestClient

    from app import __version__
    from app import main as main_module

    with TestClient(main_module.app) as client:
        health = client.get("/health").json()

    assert health["version"] == __version__
    assert health["version"] != "0.1.0"


def test_the_backend_version_is_one_publish_py_checks(workspace):
    """Six files carry the version now. A seventh that nothing checks is how
    they start disagreeing again."""
    sys.path.insert(0, str(PROJECT / "installer"))
    import publish

    from app import __version__

    checked = dict(publish.VERSION_FILES)
    assert "backend/app/__init__.py" in checked
    assert publish.agreed_version() == __version__


def test_health_is_ok_with_a_real_skill_set(workspace):
    from fastapi.testclient import TestClient

    from app import main as main_module

    with TestClient(main_module.app) as client:
        health = client.get("/health").json()

    assert health["ok"] is True
    assert health["skills"] > 40
    assert health["problem"] is None


# -- the entry point ------------------------------------------------------


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10"])
def test_the_server_refuses_to_listen_off_loopback(host):
    """REQ-26 — this service reaches files, mail and calendar."""
    result = subprocess.run(
        [sys.executable, str(SERVER), "--host", host],
        cwd=str(PROJECT / "backend"),
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 2
    assert "loopback-only" in result.stderr


# -- config discovery in an installed build (REQ-5, REQ-29) ---------------


def test_an_installed_build_seeds_a_config_it_can_actually_use(tmp_path, monkeypatch):
    """The packaged app has no source tree.

    Resolving the config relative to __file__ landed inside PyInstaller's
    archive, so the installed build found none and ran on defaults: no file
    roots, no connectors, no indexed folders. It worked and could not be
    configured, which is worse than refusing to start.
    """
    from app import settings

    data = tmp_path / "data"
    monkeypatch.setenv("KAI_DATA_DIR", str(data))
    monkeypatch.delenv("KAI_CONFIG", raising=False)
    # Pretend there is no source checkout, as in an installed build.
    monkeypatch.setattr(settings, "project_root", lambda: tmp_path / "nowhere")

    example = tmp_path / "example.yaml"
    example.write_text("persona:\n  name: Kai\n", encoding="utf-8")
    monkeypatch.setattr(settings, "_bundled_example", lambda: example)

    resolved = settings.config_path()

    assert resolved == data / "kai.config.yaml"
    assert resolved.exists(), "the config was not seeded"
    assert "Kai" in resolved.read_text(encoding="utf-8")


def test_a_source_checkout_still_wins(tmp_path, monkeypatch):
    from app import settings

    monkeypatch.delenv("KAI_CONFIG", raising=False)
    root = tmp_path / "src"
    root.mkdir()
    (root / "kai.config.yaml").write_text("persona:\n  name: Dev\n", encoding="utf-8")
    monkeypatch.setattr(settings, "project_root", lambda: root)

    assert settings.config_path() == root / "kai.config.yaml"


def test_only_one_installer_format_is_built():
    """Two installers for one application is a choice the user shouldn't face."""
    import json

    config = json.loads(
        (PROJECT / "ui" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )

    assert config["bundle"]["targets"] == ["nsis"]


def test_the_csp_stays_tight():
    """No script execution beyond the app's own files.

    This replaced a test asserting the opposite. The avatar failing in the
    packaged build was diagnosed as the CSP refusing to compile Cubism Core's
    WebAssembly, and 'wasm-unsafe-eval' was added to fix it. Serving the real
    built assets under both policies disproved that: with WebAssembly blocked
    outright, Core 6.0.1 still parses the .moc3 and reports all 222 drawables.
    The relaxation bought nothing and was reverted.

    What is asserted now is that it stays that way -- eval of any kind is a
    much bigger door than the one that needed opening.
    """
    import json

    config = json.loads(
        (PROJECT / "ui" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )
    csp = config["app"]["security"]["csp"]

    assert "default-src 'self'" in csp
    assert "unsafe-eval" not in csp
    # Inline scripts too: the built frontend has none, and allowing them is the
    # usual way a CSP stops meaning anything.
    assert "'unsafe-inline'" not in csp.replace("style-src 'self' 'unsafe-inline'", "")


# -- the origin the packaged app actually uses (REQ-26, REQ-27) -----------


@pytest.mark.parametrize(
    "origin,allowed",
    [
        # Windows WebView2 serves the packaged app from here. Omitting it meant
        # the installed app was CORS-blocked from its own backend on every
        # request and reported "backend unreachable" against a healthy backend.
        ("http://tauri.localhost", True),
        ("https://tauri.localhost", True),
        ("tauri://localhost", True),          # macOS and Linux
        ("http://localhost:5173", True),      # dev server
        ("http://127.0.0.1:8756", True),
        # Still refused: this API reaches the user's files, mail and calendar.
        ("https://evil.example.com", False),
        ("http://tauri.localhost.evil.com", False),
        ("http://192.168.1.10:5173", False),
    ],
)
def test_cors_allows_only_local_front_ends(workspace, origin, allowed):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        response = client.get("/health", headers={"Origin": origin})

    header = response.headers.get("access-control-allow-origin")
    assert (header == origin) is allowed, f"{origin} -> {header!r}"


def test_the_install_directory_is_not_the_data_directory():
    r"""NSIS per-user installs to %LOCALAPPDATA%\<productName>.

    With productName "Kai" that was exactly data_dir(), so the app was
    installed on top of the user's database and config -- and an uninstall
    would take their data with it.
    """
    import json

    from app.settings import data_dir

    config = json.loads(
        (PROJECT / "ui" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )

    assert config["productName"] != data_dir().name, (
        "the installer would unpack into the data directory"
    )


# -- surviving a damaged database (REQ-27) --------------------------------


def test_a_corrupt_database_is_quarantined_and_replaced(tmp_path, monkeypatch):
    """A corrupt file otherwise fails every open, forever.

    The scheduler thread then raises every few seconds, anything touching
    storage returns 500, and the app is wedged with no way out from inside it.
    Seen for real after a force-kill during a write.
    """

    from app import db

    broken = tmp_path / "kai.db"
    broken.write_bytes(b"SQLite format 3\x00" + b"\xde\xad\xbe\xef" * 4096)

    db.close_connection()
    db.set_db_path(broken)
    try:
        conn = db.connect()  # must not raise
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.query("SELECT COUNT(*) c FROM memory_facts")[0]["c"] == 0

        quarantined = list(tmp_path.glob("corrupt-*/kai.db"))
        assert quarantined, "the damaged file was destroyed instead of kept"
        assert quarantined[0].read_bytes()[:16] == b"SQLite format 3\x00"
    finally:
        db.close_connection()
        db.set_db_path(None)


def test_a_healthy_database_is_left_alone(tmp_path):
    from app import db

    good = tmp_path / "kai.db"
    db.close_connection()
    db.set_db_path(good)
    try:
        db.connect()
        db.execute("INSERT INTO memory_facts(id, text, category, created_at) "
                   "VALUES('x', 'a fact', 'fact', '2026-01-01T00:00:00+00:00')")
        db.close_connection()

        db.connect()
        assert db.query("SELECT COUNT(*) c FROM memory_facts")[0]["c"] == 1
        assert not list(tmp_path.glob("corrupt-*"))
    finally:
        db.close_connection()
        db.set_db_path(None)


# -- standard streams in a windowed build ---------------------------------


def test_missing_stdout_is_replaced_before_uvicorn_configures_logging():
    """A windowed build with nothing piping it has sys.stdout set to None.

    uvicorn's default log config builds a StreamHandler on stdout and stderr,
    and logging.dictConfig raises "Unable to configure formatter 'default'" on
    the None -- killing the backend before it binds the port, with no console
    to print the traceback to. The desktop app never hit this because
    tauri-plugin-shell pipes both streams; running the executable directly does.
    """
    import server

    original = sys.stdout, sys.stderr
    try:
        sys.stdout = None  # type: ignore[assignment]
        sys.stderr = None  # type: ignore[assignment]
        server.ensure_standard_streams()

        assert sys.stdout is not None and sys.stderr is not None
        # Has to be usable, not merely non-None.
        sys.stdout.write("")
        sys.stderr.write("")
    finally:
        for stream in (sys.stdout, sys.stderr):
            if stream is not None and stream not in original:
                stream.close()
        sys.stdout, sys.stderr = original


def test_existing_streams_are_left_alone():
    """Replacing a working stream would send the sidecar's logs to nowhere."""
    import server

    original = sys.stdout
    server.ensure_standard_streams()
    assert sys.stdout is original


# -- the icon (REQ-29) -----------------------------------------------------


def test_every_icon_the_bundle_references_exists_and_is_the_right_size():
    """A missing icon fails the Tauri build; a wrong-sized one ships blurred.

    Both are the kind of thing nobody notices until the installer is in front
    of someone else.
    """
    import json

    from PIL import Image

    config = json.loads((PROJECT / "ui" / "src-tauri" / "tauri.conf.json").read_text("utf-8"))
    icons = PROJECT / "ui" / "src-tauri"

    for relative in config["bundle"]["icon"]:
        path = icons / relative
        assert path.exists(), f"{relative} is referenced by the bundle but missing"

        if path.suffix == ".png":
            # "128x128@2x.png" is 256; the rest state their size in the name.
            name = path.stem
            expected = int(name.split("x")[0]) * (2 if name.endswith("@2x") else 1)
            assert Image.open(path).size == (expected, expected), f"{relative} is the wrong size"


def test_the_icon_is_not_the_placeholder():
    """The first build shipped a blue dot on white, written to unblock
    packaging and nearly shipped to a user.

    The real mark is drawn on the app's own dark panel colour, so a white
    background means the placeholder came back.
    """
    from PIL import Image

    icon = Image.open(PROJECT / "ui" / "src-tauri" / "icons" / "128x128.png").convert("RGB")
    corner = icon.getpixel((4, 4))

    assert sum(corner) < 200, f"the corner is {corner}; the icon looks like the placeholder again"


def test_the_icon_generator_runs():
    """It is a script so the mark can be changed by editing numbers.

    A generator that has rotted is worse than none: the next person edits it,
    sees nothing change, and hand-edits the PNGs instead.
    """
    generator = PROJECT / "installer" / "make_icons.py"
    assert generator.exists()

    result = subprocess.run(
        [sys.executable, str(generator)], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr


# -- the updater signing key (REQ-26, REQ-29) -----------------------------


def test_no_private_signing_key_is_in_the_repository():
    """The one secret in this project that cannot be rotated quietly.

    The updater private key is what proves an update came from us. Anyone
    holding it can push signed code to every install, and every install trusts
    it implicitly because the matching public key is compiled in. Committing it
    once means it is in the history forever.
    """
    # Two things make the naive version of this test useless, and both were
    # found by planting a real key and watching it pass:
    #
    #   the key file is base64-encoded as a whole, so searching the raw text
    #       for the header never matches anything
    #   the header says "rsign", not "minisign" -- Tauri's signer writes rsign
    #       secret keys and minisign public ones
    #
    # A guard that cannot fire is worse than none, because it is believed. This
    # one spent a while in a third way of not firing: it walked the filesystem
    # and skipped a hardcoded list of directory names containing ".venv", while
    # the XTTS environment that sidecar/requirements.txt tells you to build is
    # called ".venv-xtts" -- 43,853 files and 1.4GB of PyTorch, every one of
    # them read as text and fed to a base64 decoder. The test did not fail, it
    # simply never finished, and took the whole suite with it.
    #
    # `git ls-files` fixes that by being the right question. Only a tracked file
    # can leak a key, the list is the index rather than the disk, and no future
    # build directory can defeat it by having a name nobody added to a set.
    import base64

    # Assembled rather than written out, or this file matches itself.
    markers = ("rsign encrypted " + "secret key", "minisign encrypted " + "secret key")

    def looks_like_a_private_key(text: str) -> bool:
        if any(marker in text for marker in markers):
            return True
        stripped = "".join(text.split())
        if len(stripped) < 32:
            return False
        try:
            decoded = base64.b64decode(stripped, validate=True).decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001 - not base64, nothing more to check
            return False
        return any(marker in decoded for marker in markers)

    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=PROJECT, capture_output=True, check=True, text=True,
    )
    tracked = [name for name in listing.stdout.split("\0") if name]
    # If this is ever zero the test has stopped checking anything, and would
    # pass for exactly that reason.
    assert tracked, "git ls-files returned nothing; this guard is not running"

    offenders = []

    for name in tracked:
        path = PROJECT / name
        if not path.is_file():  # deleted but still in the index
            continue
        if path.suffix in {".png", ".ico", ".icns", ".exe", ".dll", ".pyd", ".wav", ".pyc",
                           ".moc3", ".jpg", ".gif"}:
            continue
        try:
            if looks_like_a_private_key(path.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(name)
        except OSError:
            continue

    assert not offenders, f"private signing key material found in: {offenders}"


def test_the_public_key_is_configured_and_is_not_a_private_one():
    """The public half must be present, or updates cannot be verified at all."""
    import json

    config = json.loads((PROJECT / "ui" / "src-tauri" / "tauri.conf.json").read_text("utf-8"))
    updater = config["plugins"]["updater"]

    assert updater["pubkey"], "no public key: the updater would accept nothing"
    assert updater["endpoints"], "no endpoint: there is nowhere to check"

    import base64

    decoded = base64.b64decode(updater["pubkey"]).decode("utf-8", "ignore")
    assert "public key" in decoded, "that does not look like a minisign public key"
    assert "secret key" not in decoded, "a PRIVATE key is configured as the public one"


def test_updater_artifacts_are_produced():
    """Tauri v2 only emits the .sig files the updater needs when asked.

    Without this the release would carry an installer nobody can verify, and
    the failure appears on the user's machine rather than at build time.
    """
    import json

    config = json.loads((PROJECT / "ui" / "src-tauri" / "tauri.conf.json").read_text("utf-8"))
    assert config["bundle"].get("createUpdaterArtifacts") is True

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
    import sqlite3

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

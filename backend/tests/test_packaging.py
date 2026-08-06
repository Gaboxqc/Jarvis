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

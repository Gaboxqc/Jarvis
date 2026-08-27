"""Publishing a release — REQ-30.

The first three releases of this app were all unusable as updates: every one
marked pre-release, none carrying a signature, none carrying a manifest, and
the URL the app polls returning 404 throughout. Nothing surfaced it, because
from inside the app "no update available" and "the endpoint is broken" look
exactly the same.

What is tested here is the refusing, not the uploading. Publishing itself is a
call to `gh`; the value is in the checks that run first, because each one
corresponds to a way a release can look complete and help nobody.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "installer"))

import publish  # noqa: E402


@pytest.fixture
def fake_tree(tmp_path, monkeypatch):
    """A miniature repo with the five version files and a bundle directory."""

    def write(version: str) -> None:
        (tmp_path / "ui" / "src-tauri").mkdir(parents=True, exist_ok=True)
        (tmp_path / "ui" / "package.json").write_text(
            json.dumps({"version": version}), encoding="utf-8"
        )
        (tmp_path / "ui" / "package-lock.json").write_text(
            json.dumps({"version": version}), encoding="utf-8"
        )
        (tmp_path / "ui" / "src-tauri" / "Cargo.toml").write_text(
            f'[package]\nname = "kai"\nversion = "{version}"\n', encoding="utf-8"
        )
        (tmp_path / "ui" / "src-tauri" / "tauri.conf.json").write_text(
            json.dumps({"version": version}), encoding="utf-8"
        )
        (tmp_path / "ui" / "src-tauri" / "Cargo.lock").write_text(
            f'[[package]]\nname = "kai"\nversion = "{version}"\n', encoding="utf-8"
        )

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    monkeypatch.setattr(publish, "ROOT", tmp_path)
    monkeypatch.setattr(publish, "BUNDLE", bundle)
    write("1.2.3")
    return tmp_path, bundle, write


def test_agreeing_versions_are_accepted(fake_tree):
    assert publish.agreed_version() == "1.2.3"


def test_disagreeing_versions_are_refused(fake_tree):
    """The updater compares the manifest against the version compiled in.

    A mismatch means it either never offers the update or offers it forever.
    """
    root, _, _ = fake_tree
    (root / "ui" / "package.json").write_text(
        json.dumps({"version": "9.9.9"}), encoding="utf-8"
    )

    with pytest.raises(publish.PublishError, match="disagree"):
        publish.agreed_version()


def test_a_missing_installer_is_refused(fake_tree):
    with pytest.raises(publish.PublishError, match="no installer"):
        publish.artifacts("1.2.3")


def test_a_missing_signature_is_refused(fake_tree):
    """An unsigned release looks fine and every install rejects it."""
    _, bundle, _ = fake_tree
    (bundle / "Kai Assistant_1.2.3_x64-setup.exe").write_bytes(b"MZ")

    with pytest.raises(publish.PublishError, match="no signature"):
        publish.artifacts("1.2.3")


def test_the_manifest_carries_the_signature_itself_not_a_path(fake_tree):
    """A path here produces a manifest that parses and never verifies."""
    _, bundle, _ = fake_tree
    installer = bundle / "Kai Assistant_1.2.3_x64-setup.exe"
    installer.write_bytes(b"MZ")
    signature = bundle / "Kai Assistant_1.2.3_x64-setup.exe.sig"
    signature.write_text("dW50cnVzdGVkIGNvbW1lbnQ6IHNpZ25hdHVyZQ==\n", encoding="utf-8")

    built = publish.manifest("1.2.3", "notes", installer, signature)
    entry = built["platforms"]["windows-x86_64"]

    assert entry["signature"] == "dW50cnVzdGVkIGNvbW1lbnQ6IHNpZ25hdHVyZQ=="
    assert not entry["signature"].endswith(".sig")


def test_the_manifest_url_points_at_the_tag_not_at_latest(fake_tree):
    """`latest` moves, and a signature is made for one build -- not for
    whatever happens to be newest when someone updates."""
    _, bundle, _ = fake_tree
    installer = bundle / "Kai Assistant_1.2.3_x64-setup.exe"
    installer.write_bytes(b"MZ")
    signature = bundle / "Kai Assistant_1.2.3_x64-setup.exe.sig"
    signature.write_text("sig", encoding="utf-8")

    url = publish.manifest("1.2.3", "n", installer, signature)["platforms"]["windows-x86_64"]["url"]

    assert "/download/v1.2.3/" in url
    assert "/latest/" not in url
    # GitHub replaces spaces in asset names with dots.
    assert " " not in url

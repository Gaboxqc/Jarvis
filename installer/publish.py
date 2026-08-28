"""Publish a build as a GitHub release — REQ-30.

Updates are only real if three files reach the same release: the installer, its
signature, and a manifest pointing at both. Doing that by hand is how the first
three releases ended up unusable -- all marked pre-release, none carrying a
`.sig`, none carrying a manifest, and the endpoint the app checks returning 404
the entire time. Nothing in the app could say so, because a missing update and
an unreachable one look identical from inside.

So this is one command, and it refuses rather than publishing something that
cannot work:

    .venv\\Scripts\\python installer\\publish.py --notes "what changed"

What it will not do:

- publish when the five version files disagree, because the updater compares
  the manifest against the version compiled into the app, and a mismatch means
  it either never offers the update or offers it forever
- publish without a `.sig`, because every install refuses an unsigned update
  and the release would look fine while helping nobody
- mark a release latest without the manifest attached, since `latest` is the
  only thing the app ever asks for

`--prerelease` exists but says what it costs: GitHub excludes pre-releases from
`releases/latest`, which is the exact URL the updater reads.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "ui" / "src-tauri" / "target" / "release" / "bundle" / "nsis"
REPO = "Gaboxqc/Jarvis"

# Every file that carries the version, and how to find it in each.
VERSION_FILES: list[tuple[str, str]] = [
    ("ui/package.json", r'"version":\s*"([^"]+)"'),
    ("ui/package-lock.json", r'"version":\s*"([^"]+)"'),
    ("ui/src-tauri/Cargo.toml", r'^version\s*=\s*"([^"]+)"'),
    ("ui/src-tauri/tauri.conf.json", r'"version":\s*"([^"]+)"'),
]


class PublishError(Exception):
    """Something that must be fixed before a release can work."""


def declared_versions() -> dict[str, str]:
    found: dict[str, str] = {}
    for relative, pattern in VERSION_FILES:
        text = (ROOT / relative).read_text(encoding="utf-8")
        match = re.search(pattern, text, re.M)
        if not match:
            raise PublishError(f"no version found in {relative}")
        found[relative] = match.group(1)

    lock = (ROOT / "ui" / "src-tauri" / "Cargo.lock").read_text(encoding="utf-8")
    match = re.search(r'name = "kai"\nversion = "([^"]+)"', lock)
    if not match:
        raise PublishError("no kai version found in Cargo.lock")
    found["ui/src-tauri/Cargo.lock"] = match.group(1)
    return found


def agreed_version() -> str:
    versions = declared_versions()
    distinct = set(versions.values())
    if len(distinct) != 1:
        detail = "\n".join(f"  {name}: {value}" for name, value in versions.items())
        raise PublishError(f"the version files disagree:\n{detail}")
    return distinct.pop()


def artifacts(version: str) -> tuple[Path, Path]:
    installer = BUNDLE / f"Kai Assistant_{version}_x64-setup.exe"
    signature = BUNDLE / f"Kai Assistant_{version}_x64-setup.exe.sig"

    if not installer.exists():
        raise PublishError(f"no installer for {version} at {installer}")
    if not signature.exists():
        raise PublishError(
            f"no signature beside {installer.name}. The build ran without the "
            "updater key, so every existing install would refuse this update."
        )
    return installer, signature


def manifest(version: str, notes: str, installer: Path, signature: Path) -> dict:
    """The document the app fetches to decide whether an update exists.

    The url is the tag's own download path, not the `latest` alias: the alias
    moves, and an install that resolved it later would fetch a different build
    than the signature in this file was made for.
    """
    return {
        "version": version,
        "notes": notes,
        "pub_date": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "platforms": {
            "windows-x86_64": {
                "signature": signature.read_text(encoding="utf-8").strip(),
                "url": (
                    f"https://github.com/{REPO}/releases/download/"
                    f"v{version}/{installer.name.replace(' ', '.')}"
                ),
            }
        },
    }


def run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise PublishError(f"{' '.join(command[:3])} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def release_exists(tag: str) -> bool:
    result = subprocess.run(
        ["gh", "release", "view", tag, "--repo", REPO],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def publish(notes: str, prerelease: bool, replace: bool, extra: list[Path]) -> int:
    version = agreed_version()
    tag = f"v{version}"
    installer, signature = artifacts(version)

    manifest_path = BUNDLE / "latest.json"
    manifest_path.write_text(
        json.dumps(manifest(version, notes, installer, signature), indent=2),
        encoding="utf-8",
    )

    print(f"version    : {version}")
    print(f"installer  : {installer.name} ({installer.stat().st_size / 1e6:.0f} MB)")
    print("signature  : present")
    print(f"manifest   : {manifest_path}")

    if release_exists(tag):
        if not replace:
            raise PublishError(
                f"{tag} already exists. Bump the version, or pass --replace to "
                "overwrite its assets."
            )
        print(f"replacing assets on the existing {tag}")
        for asset in (installer, signature, manifest_path, *extra):
            subprocess.run(
                ["gh", "release", "delete-asset", tag, asset.name, "--repo", REPO, "--yes"],
                capture_output=True,
                text=True,
            )
        run([
            "gh", "release", "upload", tag,
            str(installer), str(signature), str(manifest_path),
            *[str(path) for path in extra],
            "--repo", REPO, "--clobber",
        ])
    else:
        command = [
            "gh", "release", "create", tag,
            str(installer), str(signature), str(manifest_path),
            *[str(path) for path in extra],
            "--repo", REPO,
            "--title", f"Kai {version}",
            "--notes", notes,
        ]
        # `latest` is the only URL the updater ever asks for, and GitHub leaves
        # pre-releases out of it.
        command.append("--prerelease" if prerelease else "--latest")
        run(command)

    print(f"\npublished {tag}")
    if prerelease:
        print(
            "  marked pre-release, so releases/latest still does not point here "
            "and no install will be offered this update."
        )
    else:
        print(f"  https://github.com/{REPO}/releases/tag/{tag}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish a Kai build as a GitHub release")
    parser.add_argument("--notes", required=True, help="What changed, in a sentence.")
    parser.add_argument("--prerelease", action="store_true")
    parser.add_argument("--replace", action="store_true", help="Overwrite an existing tag's assets.")
    parser.add_argument(
        "--asset",
        action="append",
        default=[],
        help="Extra file to attach, such as the voice engine bundle.",
    )
    args = parser.parse_args(argv)

    try:
        return publish(
            args.notes,
            args.prerelease,
            args.replace,
            [Path(path) for path in args.asset],
        )
    except PublishError as exc:
        print(f"refusing to publish: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

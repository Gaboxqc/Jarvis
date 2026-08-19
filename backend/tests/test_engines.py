"""Fetching the optional cloning engine — REQ-4, REQ-26.

This downloads an executable over the network and then runs it, so most of what
is asserted here is about refusing bad input rather than accepting good input.
The happy path is one test; the rest are the ways it must fail.

Nothing here touches the network. The real archive is hundreds of megabytes,
and a suite that pulls it is a suite nobody runs -- so `install_xtts` takes an
opener, and these build their own zips in a tmp dir.
"""

from __future__ import annotations

import hashlib
import io
import zipfile

import pytest

from app.voice import engines


def _archive(names: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, payload in names.items():
            bundle.writestr(name, payload)
    return buffer.getvalue()


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture(autouse=True)
def _isolated(workspace):
    """Every test gets its own data dir, so none of them see a real install."""
    yield
    engines.remove_xtts()


def test_a_verified_archive_installs(workspace):
    payload = _archive({engines.XTTS_ENTRY: b"MZ fake executable"})

    result = engines.install_xtts(
        url="https://example.invalid/x.zip",
        sha256=_sha(payload),
        opener=lambda _: payload,
    )

    assert result["installed"] is True
    assert engines.xtts_installed() is True
    assert engines.progress()["state"] == "installed"


def test_a_tampered_archive_is_refused(workspace):
    """The checksum is the only thing standing between a DNS answer and code
    execution on this machine."""
    payload = _archive({engines.XTTS_ENTRY: b"MZ not what was published"})

    with pytest.raises(engines.EngineError, match="checksum"):
        engines.install_xtts(
            url="https://example.invalid/x.zip",
            sha256=_sha(b"something else entirely"),
            opener=lambda _: payload,
        )

    assert engines.xtts_installed() is False


def test_an_unpublished_checksum_is_refused_rather_than_skipped(workspace):
    """An empty expected hash must not become a check that always passes."""
    payload = _archive({engines.XTTS_ENTRY: b"MZ fake executable"})

    with pytest.raises(engines.EngineError, match="cannot be verified"):
        engines.install_xtts(
            url="https://example.invalid/x.zip",
            sha256="",
            opener=lambda _: payload,
        )

    assert engines.xtts_installed() is False


def test_an_archive_without_the_executable_is_refused(workspace):
    payload = _archive({"readme.txt": b"nothing useful here"})

    with pytest.raises(engines.EngineError, match="contained no"):
        engines.install_xtts(
            url="https://example.invalid/x.zip",
            sha256=_sha(payload),
            opener=lambda _: payload,
        )

    assert engines.xtts_installed() is False


def test_an_archive_cannot_write_outside_its_folder(workspace):
    """Zip members are attacker-controlled paths until proven otherwise."""
    payload = _archive({"../../escaped.txt": b"owned"})

    with pytest.raises(engines.EngineError, match="outside"):
        engines.install_xtts(
            url="https://example.invalid/x.zip",
            sha256=_sha(payload),
            opener=lambda _: payload,
        )

    assert not (engines.engines_dir().parent.parent / "escaped.txt").exists()


def test_a_failed_download_leaves_the_previous_engine_alone(workspace):
    """An interrupted upgrade must not take away the engine that worked."""
    good = _archive({engines.XTTS_ENTRY: b"MZ the working one"})
    engines.install_xtts(
        url="https://example.invalid/x.zip", sha256=_sha(good), opener=lambda _: good
    )

    def explode(_: str) -> bytes:
        raise OSError("connection reset")

    with pytest.raises(engines.EngineError, match="download failed"):
        engines.install_xtts(
            url="https://example.invalid/x.zip", sha256="whatever", opener=explode
        )

    assert engines.xtts_installed() is True
    assert engines.xtts_executable().read_bytes() == b"MZ the working one"


def test_removing_reclaims_the_space(workspace):
    payload = _archive({engines.XTTS_ENTRY: b"MZ fake executable"})
    engines.install_xtts(
        url="https://example.invalid/x.zip",
        sha256=_sha(payload),
        opener=lambda _: payload,
    )

    assert engines.remove_xtts() is True
    assert engines.xtts_installed() is False
    # Removing something already gone is not an error worth raising.
    assert engines.remove_xtts() is False

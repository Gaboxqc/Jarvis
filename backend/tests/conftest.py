from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, settings  # noqa: E402
from app.skills import registry  # noqa: E402
from app.skills.base import Skill, SkillParam, SkillResult  # noqa: E402

# What the gated test double has actually done. Assertions read this instead of
# a real store, so the gate's invariants stay independent of any product
# decision about which real skills deserve a confirmation prompt.
performed: list[str] = []


class GatedTestSkill(Skill):
    """A stand-in for 'an action worth interrupting someone for'."""

    name = "test.gated"
    description = "Test double for a gated, reversible action."
    parameters = (SkillParam("label", "string", "Something to record."),)
    consequential = True
    reversible = True

    def preview(self, args):
        return f"Do the gated thing with \"{args['label']}\""

    def run(self, args, ctx):
        performed.append(args["label"])
        return SkillResult(
            ok=True,
            message=f"Did {args['label']}",
            undo_payload={"label": args["label"]},
        )

    def undo(self, undo_payload):
        label = undo_payload["label"]
        if label in performed:
            performed.remove(label)
        return SkillResult(ok=True, message=f"Took back {label}")


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated data dir, database and config for each test."""
    data = tmp_path / "data"
    sandbox = tmp_path / "sandbox"
    docs = tmp_path / "docs"
    data.mkdir()
    sandbox.mkdir()
    docs.mkdir()

    config = {
        "documents": {
            "indexed_folders": [str(docs)],
            "max_file_mb": 5,
            "rescan_minutes": 15,
            # Tests must not depend on whether the laptop happens to be plugged in.
            "pause_on_battery": False,
        },
        "persona": {"name": "Kai", "verbosity": "terse", "idle_timeout_minutes": 30},
        "brain": {"provider": "ollama", "model": "llama3"},
        "privacy": {"allow_web_search": True, "allow_live_data": True},
        "actions": {"pre_approved": [], "confirmation_ttl_minutes": 10},
        "system": {"allowed_roots": [str(sandbox)]},
        "skills": {"disabled": []},
    }
    config_file = tmp_path / "kai.config.yaml"
    config_file.write_text(yaml.safe_dump(config), encoding="utf-8")

    monkeypatch.setenv("KAI_DATA_DIR", str(data))
    monkeypatch.setenv("KAI_CONFIG", str(config_file))
    settings.reset_config_cache()
    db.set_db_path(data / "test.db")
    registry.reset()
    registry.load_skills()[GatedTestSkill.name] = GatedTestSkill()
    performed.clear()

    from app import focus
    from app.capture import session as capture
    from app.index import scanner

    scanner.reset_state()
    focus.reset()
    capture.reset()

    yield sandbox

    db.close_connection()
    db.set_db_path(None)
    settings.reset_config_cache()
    registry.reset()
    scanner.reset_state()
    focus.reset()
    capture.reset()


@pytest.fixture
def docs_folder(workspace: Path, tmp_path: Path) -> Path:
    """The folder wired into documents.indexed_folders for this test."""
    return tmp_path / "docs"


@pytest.fixture
def config_file() -> Path:
    return Path(os.environ["KAI_CONFIG"])


def minimal_pdf(pages: list[list[str]]) -> bytes:
    """Build a small, spec-valid PDF with real text operators.

    Written by hand rather than pulled in as a test dependency: PDF is the format
    document search exists for, so its extraction path deserves coverage against
    an actual PDF rather than a mock that would pass whatever pypdf did.
    """
    objects: list[bytes] = []
    page_ids = [3 + i * 2 for i in range(len(pages))]
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)

    objects.append(b"<</Type/Catalog/Pages 2 0 R>>")
    objects.append(
        f"<</Type/Pages/Kids[{kids}]/Count {len(pages)}>>".encode()
    )

    font_id = 3 + len(pages) * 2
    for index, lines in enumerate(pages):
        content = "BT /F1 14 Tf 72 720 Td 18 TL\n"
        for line in lines:
            escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            content += f"({escaped}) Tj T*\n"
        content += "ET"
        body = content.encode("latin-1")
        objects.append(
            f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents {4 + index * 2} 0 R"
            f"/Resources<</Font<</F1 {font_id} 0 R>>>>>>".encode()
        )
        objects.append(b"<</Length " + str(len(body)).encode() + b">>stream\n" + body + b"\nendstream")

    objects.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj".encode() + body + b"endobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer<</Size {len(objects) + 1}/Root 1 0 R>>\nstartxref\n{xref_at}\n%%EOF"
    ).encode()
    return bytes(out)


def make_files(folder: Path, names: list[str]) -> list[Path]:
    created = []
    for name in names:
        path = folder / name
        path.write_text(f"content of {name}", encoding="utf-8")
        created.append(path)
    return created

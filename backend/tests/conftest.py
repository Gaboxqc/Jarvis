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
    data.mkdir()
    sandbox.mkdir()

    config = {
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

    yield sandbox

    db.close_connection()
    db.set_db_path(None)
    settings.reset_config_cache()
    registry.reset()


@pytest.fixture
def config_file() -> Path:
    return Path(os.environ["KAI_CONFIG"])


def make_files(folder: Path, names: list[str]) -> list[Path]:
    created = []
    for name in names:
        path = folder / name
        path.write_text(f"content of {name}", encoding="utf-8")
        created.append(path)
    return created

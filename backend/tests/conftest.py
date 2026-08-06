from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, settings  # noqa: E402
from app.skills import registry  # noqa: E402


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
    registry.load_skills()

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

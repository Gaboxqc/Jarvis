"""Configuration loading — REQ-5 (persona), REQ-26 (privacy), REQ-24 (pre-approvals).

The config file is the single human-editable source of truth. It is re-read when its
mtime changes, so edits apply on the next turn without a restart (REQ-5).
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_NAME = "kai.config.yaml"


def project_root() -> Path:
    # backend/app/settings.py -> backend/app -> backend -> <root>
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """Where all local user data lives (REQ-26 — one place, one thing to delete)."""
    override = os.environ.get("KAI_DATA_DIR")
    if override:
        path = Path(override)
    elif os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        path = Path(base) / "Kai"
    else:
        path = Path.home() / ".local" / "share" / "kai"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    override = os.environ.get("KAI_CONFIG")
    if override:
        return Path(override)
    return project_root() / DEFAULT_CONFIG_NAME


@dataclass(frozen=True)
class Persona:
    name: str = "Kai"
    tone_description: str = "Direct and warm."
    verbosity: str = "terse"
    address_style: str = ""
    language: str = "en"
    idle_timeout_minutes: int = 30


@dataclass(frozen=True)
class BrainSettings:
    provider: str = "ollama"
    model: str = "llama3"
    ollama_host: str = "http://localhost:11434"
    temperature: float = 0.4
    timeout_seconds: int = 120


@dataclass(frozen=True)
class PrivacySettings:
    allow_web_search: bool = True
    allow_live_data: bool = True
    allow_cloud_llm: bool = False


@dataclass(frozen=True)
class ActionSettings:
    pre_approved: tuple[str, ...] = ()
    confirmation_ttl_minutes: int = 10


@dataclass(frozen=True)
class SystemSettings:
    allowed_roots: tuple[Path, ...] = ()
    distracting_apps: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentSettings:
    """REQ-16 — which folders are searchable, and REQ-31 — when to back off."""

    indexed_folders: tuple[Path, ...] = ()
    max_file_mb: int = 25
    rescan_minutes: int = 15
    pause_on_battery: bool = True


@dataclass(frozen=True)
class VoiceSettings:
    enabled: bool = False
    voice_id: str = "en_US-amy-medium"


@dataclass(frozen=True)
class Config:
    persona: Persona
    voice: VoiceSettings
    brain: BrainSettings
    privacy: PrivacySettings
    actions: ActionSettings
    system: SystemSettings
    documents: DocumentSettings
    disabled_skills: tuple[str, ...] = ()
    source_path: Path | None = None


def _expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(p))).resolve()


def _build(raw: dict[str, Any], source: Path | None) -> Config:
    persona_raw = raw.get("persona") or {}
    voice_raw = raw.get("voice") or {}
    brain_raw = raw.get("brain") or {}
    privacy_raw = raw.get("privacy") or {}
    actions_raw = raw.get("actions") or {}
    skills_raw = raw.get("skills") or {}
    system_raw = raw.get("system") or {}
    documents_raw = raw.get("documents") or {}

    def _paths(entries: Any) -> tuple[Path, ...]:
        resolved: list[Path] = []
        for entry in entries or []:
            try:
                resolved.append(_expand(str(entry)))
            except (OSError, ValueError):
                # A bad path must not take the whole assistant down (REQ-27).
                continue
        return tuple(resolved)

    roots = list(_paths(system_raw.get("allowed_roots")))

    return Config(
        persona=Persona(
            name=persona_raw.get("name", "Kai"),
            tone_description=(persona_raw.get("tone_description") or "Direct and warm.").strip(),
            verbosity=persona_raw.get("verbosity", "terse"),
            address_style=persona_raw.get("address_style") or "",
            language=persona_raw.get("language", "en"),
            idle_timeout_minutes=int(persona_raw.get("idle_timeout_minutes", 30)),
        ),
        voice=VoiceSettings(
            enabled=bool(voice_raw.get("enabled", False)),
            voice_id=voice_raw.get("voice_id", "en_US-amy-medium"),
        ),
        brain=BrainSettings(
            provider=brain_raw.get("provider", "ollama"),
            model=brain_raw.get("model", "llama3"),
            ollama_host=brain_raw.get("ollama_host", "http://localhost:11434"),
            temperature=float(brain_raw.get("temperature", 0.4)),
            timeout_seconds=int(brain_raw.get("timeout_seconds", 120)),
        ),
        privacy=PrivacySettings(
            allow_web_search=bool(privacy_raw.get("allow_web_search", True)),
            allow_live_data=bool(privacy_raw.get("allow_live_data", True)),
            allow_cloud_llm=bool(privacy_raw.get("allow_cloud_llm", False)),
        ),
        actions=ActionSettings(
            pre_approved=tuple(actions_raw.get("pre_approved") or ()),
            confirmation_ttl_minutes=int(actions_raw.get("confirmation_ttl_minutes", 10)),
        ),
        system=SystemSettings(
            allowed_roots=tuple(roots),
            distracting_apps=tuple(system_raw.get("distracting_apps") or ()),
        ),
        documents=DocumentSettings(
            indexed_folders=_paths(documents_raw.get("indexed_folders")),
            max_file_mb=int(documents_raw.get("max_file_mb", 25)),
            rescan_minutes=int(documents_raw.get("rescan_minutes", 15)),
            pause_on_battery=bool(documents_raw.get("pause_on_battery", True)),
        ),
        disabled_skills=tuple(skills_raw.get("disabled") or ()),
        source_path=source,
    )


_lock = threading.Lock()
_cached: Config | None = None
_cached_mtime: float | None = None


def load_config(force: bool = False) -> Config:
    """Return the current config, re-reading the file if it changed on disk (REQ-5)."""
    global _cached, _cached_mtime

    path = config_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None

    with _lock:
        if not force and _cached is not None and mtime == _cached_mtime:
            return _cached

        raw: dict[str, Any] = {}
        if mtime is not None:
            try:
                loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    raw = loaded
            except (OSError, yaml.YAMLError):
                # A malformed config falls back to defaults rather than refusing
                # to start — the assistant stays usable (REQ-27).
                raw = {}

        _cached = _build(raw, path if mtime is not None else None)
        _cached_mtime = mtime
        return _cached


def reset_config_cache() -> None:
    """Test hook."""
    global _cached, _cached_mtime
    with _lock:
        _cached = None
        _cached_mtime = None

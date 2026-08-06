"""Skill discovery — REQ-33.

Walks the skills package at startup, instantiates every Skill subclass it finds,
and validates the contract. The router reads this registry and nothing else, so a
new skill is available the moment its module exists.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from typing import Any, Iterator

from ..settings import load_config
from .base import Skill, SkillError

log = logging.getLogger(__name__)

_registry: dict[str, Skill] = {}
_loaded = False

# Why modules were skipped. A skill module that fails to import is logged and
# skipped so one broken skill cannot stop the assistant (REQ-27) -- but in a
# packaged build nobody reads that log, and the result is an assistant quietly
# missing capabilities. Keeping the reasons lets --selftest report them.
_load_errors: list[tuple[str, str]] = []


def _iter_modules() -> Iterator[str]:
    """Every skill module, from source or from a frozen bundle.

    `walk_packages` enumerates the filesystem. Once PyInstaller has packed the
    app, the modules live in an archive and the walk returns nothing — the
    bundle *contains* every skill and the registry finds none of them, which
    presents as an assistant with no capabilities that reports itself healthy.

    So the frozen loader's table of contents is consulted as well, and the two
    sources are merged rather than chosen between: a partially-frozen layout
    (some modules on disk beside the executable) then still works.
    """
    seen: set[str] = set()
    prefix = f"{__package__}."
    skip = {"base", "registry"}

    def emit(name: str) -> Iterator[str]:
        if name in seen or not name.startswith(prefix):
            return
        if name.rsplit(".", 1)[-1] in skip:
            return
        seen.add(name)
        yield name

    try:
        package = importlib.import_module(__package__)
        for info in pkgutil.walk_packages(package.__path__, prefix=prefix):
            yield from emit(info.name)
    except Exception:  # noqa: BLE001 — frozen builds may have no usable __path__
        log.debug("filesystem walk unavailable", exc_info=True)

    for name in _manifest_module_names():
        yield from emit(name)


def _manifest_module_names() -> list[str]:
    """The build-time manifest, which is how frozen builds find their skills.

    Reading PyInstaller's own module table was tried first and abandoned: the
    attribute is private and had moved between versions, so the build produced
    an assistant with one skill and no error. The list is now computed at build
    time by installer/generate_manifest.py, from the same filesystem walk used
    from source, and shipped as code.

    Absent from a source checkout, which is fine — the walk above already found
    everything.
    """
    try:
        from ._manifest import MODULES
    except Exception:  # noqa: BLE001 — no manifest is the normal source case
        return []
    return [str(name) for name in MODULES]


def _validate(skill: Skill) -> None:
    if not skill.name:
        raise SkillError(f"{type(skill).__name__} has no name")
    if not skill.description:
        raise SkillError(f"Skill '{skill.name}' has no description; the router needs it")

    # A skill that claims reversibility but cannot undo is worse than one that
    # admits it is permanent — it makes the confirmation prompt lie (REQ-25).
    if skill.reversible and type(skill).undo is Skill.undo:
        raise SkillError(f"Skill '{skill.name}' is reversible but does not implement undo()")

    # A consequential skill with the default preview cannot tell the user what
    # they are approving, which defeats the gate (REQ-24).
    if skill.consequential and type(skill).preview is Skill.preview:
        raise SkillError(f"Skill '{skill.name}' is consequential but has no preview()")


def load_skills(force: bool = False) -> dict[str, Skill]:
    global _loaded
    if _loaded and not force:
        return _registry

    _registry.clear()
    _load_errors.clear()
    for module_name in _iter_modules():
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # a broken skill must not stop the assistant (REQ-27)
            log.exception("Skill module %s failed to import; skipping", module_name)
            _load_errors.append((module_name, f"{type(exc).__name__}: {exc}"))
            continue

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, Skill) or obj is Skill:
                continue
            if obj.__module__ != module_name:  # imported, not defined here
                continue
            try:
                skill = obj()
                _validate(skill)
            except Exception as exc:
                log.exception("Skill %s failed validation; skipping", obj.__name__)
                _load_errors.append((f"{module_name}.{obj.__name__}", f"{type(exc).__name__}: {exc}"))
                continue
            if skill.name in _registry:
                log.warning("Duplicate skill name '%s'; keeping the first", skill.name)
                continue
            _registry[skill.name] = skill

    _loaded = True
    return _registry


def enabled_skills() -> dict[str, Skill]:
    """Skills minus the ones the user turned off, minus ones privacy forbids."""
    config = load_config()
    disabled = set(config.disabled_skills)
    result: dict[str, Skill] = {}
    for name, skill in load_skills().items():
        if name in disabled:
            continue
        if not _privacy_allows(skill, config):
            continue
        result[name] = skill
    return result


def _privacy_allows(skill: Skill, config: Any) -> bool:
    """A skill whose egress switch is off is not offered to the router at all.

    Refusing here rather than at call time means the assistant never proposes
    something it is not permitted to do (REQ-26).
    """
    for requirement in skill.requires:
        if requirement == "web_search" and not config.privacy.allow_web_search:
            return False
        if requirement == "live_data" and not config.privacy.allow_live_data:
            return False
    return True


def get_skill(name: str) -> Skill | None:
    return enabled_skills().get(name)


def catalog() -> list[dict[str, Any]]:
    return [skill.to_catalog_entry() for skill in enabled_skills().values()]


def load_errors() -> list[tuple[str, str]]:
    """Modules and classes that were skipped, and why."""
    return list(_load_errors)


def discovered_module_names() -> list[str]:
    """What discovery found, before importing. Diagnostic only."""
    return list(_iter_modules())


def reset() -> None:
    """Test hook."""
    global _loaded
    _registry.clear()
    _load_errors.clear()
    _loaded = False

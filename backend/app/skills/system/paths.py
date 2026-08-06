"""Path safety — REQ-21, REQ-26.

Every filesystem skill resolves user-supplied paths through here. The rule is a
allowlist, not a denylist: a path is refused unless it resolves inside one of the
roots in `system.allowed_roots`.

Resolution happens before the containment check, so `..` traversal, symlinks and
8.3 short names all collapse to their real target first. An LLM produced the
argument being checked, so this is treated as untrusted input.
"""

from __future__ import annotations

import os
from pathlib import Path

from ...settings import load_config
from ..base import SkillError


def allowed_roots() -> list[Path]:
    return [Path(p) for p in load_config().system.allowed_roots]


def describe_roots() -> str:
    roots = allowed_roots()
    if not roots:
        return "no folders are currently allowed"
    return ", ".join(str(r) for r in roots)


def resolve_allowed(raw: str, *, must_exist: bool = True) -> Path:
    """Resolve a user-supplied path and confirm it sits inside an allowed root."""
    if not raw or not str(raw).strip():
        raise SkillError("No folder was given.")

    expanded = os.path.expandvars(os.path.expanduser(str(raw).strip().strip('"')))

    roots = allowed_roots()
    if not roots:
        raise SkillError(
            "No folders are allowed for file operations yet. "
            "Add them under system.allowed_roots in kai.config.yaml."
        )

    try:
        candidate = Path(expanded)
        # People say "my Downloads folder", and the model passes "Downloads".
        # Resolving that against the process working directory produces
        # <install dir>/Downloads, which is both wrong and confusing to be told
        # about. A bare name is matched against the allowed roots and the home
        # folder instead. Containment is still enforced below, so this widens
        # what can be *named*, never what can be reached.
        if not candidate.is_absolute():
            candidate = _resolve_relative(expanded, roots) or candidate
        candidate = candidate.resolve()
    except (OSError, ValueError) as exc:
        raise SkillError(f"'{raw}' isn't a usable path.") from exc

    if not any(_is_within(candidate, root) for root in roots):
        raise SkillError(
            f"'{candidate}' is outside the folders I'm allowed to touch "
            f"({describe_roots()}). Add it to system.allowed_roots if you want that."
        )

    if must_exist and not candidate.exists():
        raise SkillError(f"'{candidate}' doesn't exist.")

    return candidate


def _resolve_relative(name: str, roots: list[Path]) -> Path | None:
    """Interpret a bare folder name against the allowed roots, then home.

    A root whose own name matches wins first, so "Downloads" means the allowed
    Downloads root rather than a stray subfolder that happens to share the name.
    Ambiguity resolves to nothing: better to say the folder wasn't found than to
    silently reorganise the wrong one.
    """
    cleaned = name.strip().strip("/\\")
    if not cleaned:
        return None

    lowered = cleaned.lower()
    for root in roots:
        if root.name.lower() == lowered:
            return root

    matches = [root / cleaned for root in roots if (root / cleaned).exists()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return None

    from_home = Path.home() / cleaned
    return from_home if from_home.exists() else None


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        resolved_root = root.resolve()
    except (OSError, ValueError):
        return False
    try:
        candidate.relative_to(resolved_root)
        return True
    except ValueError:
        return False


def is_allowed(candidate: Path) -> bool:
    try:
        resolved = candidate.resolve()
    except (OSError, ValueError):
        return False
    return any(_is_within(resolved, root) for root in allowed_roots())

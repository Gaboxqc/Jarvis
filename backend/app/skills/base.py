"""The skill contract — REQ-33.

Every capability Kai has is a Skill. The router never learns about skills
individually; it reads the registry. Adding a capability means adding a class,
not editing the router.

Two flags carry the whole trust model:

    consequential -> the Action Gate must get explicit confirmation first (REQ-24)
    reversible    -> the skill can undo itself from its undo_payload (REQ-25)

`reversible` is a promise. If you set it True you must implement `undo()`, and
`run()` must return an undo_payload sufficient to reverse the change. The
registry enforces the first half of that at import time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["routine", "consequential"]


class SkillError(Exception):
    """Raised by a skill for an expected failure.

    The message reaches the user through the persona, so write it as a plain
    statement of what went wrong — never a stack trace fragment (REQ-27).
    """


@dataclass
class SkillParam:
    name: str
    type: str  # "string" | "integer" | "number" | "boolean" | "array"
    description: str
    required: bool = True
    default: Any = None
    enum: tuple[str, ...] | None = None


@dataclass
class SkillContext:
    """What a skill is allowed to know about the turn it is running in."""

    session_id: str = "default"
    config: Any = None
    user_text: str = ""


@dataclass
class SkillResult:
    """What a skill hands back.

    `message` is a plain factual sentence. The brain rewrites it in the persona's
    voice — skills never try to sound like the assistant themselves.
    """

    ok: bool = True
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    undo_payload: dict[str, Any] | None = None


class Skill:
    name: str = ""
    description: str = ""
    parameters: tuple[SkillParam, ...] = ()
    consequential: bool = False
    reversible: bool = False
    # Skills that need the network declare which privacy switch governs them
    # so the gate can refuse before any request is made (REQ-26).
    requires: tuple[str, ...] = ()

    def preview(self, args: dict[str, Any]) -> str:
        """One line naming exactly what will happen, with targets and counts.

        Consequential skills must override this. A preview like "organize files"
        is useless — "Move 47 files in C:\\Users\\Gabox\\Downloads into 6
        subfolders by type" is what lets someone say no (REQ-24).
        """
        return f"Run {self.name}"

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        raise NotImplementedError

    def undo(self, undo_payload: dict[str, Any]) -> SkillResult:
        raise NotImplementedError(f"{self.name} declared reversible but has no undo()")

    # -- helpers ---------------------------------------------------------

    def validate(self, args: dict[str, Any]) -> dict[str, Any]:
        """Coerce and check args against the declared parameters.

        The router is an LLM; it will send strings where integers belong and omit
        optionals. Normalising here keeps that mess out of every skill body.
        """
        cleaned: dict[str, Any] = {}
        for param in self.parameters:
            if param.name in args and args[param.name] is not None:
                cleaned[param.name] = _coerce(args[param.name], param)
            elif param.required:
                raise SkillError(f"'{param.name}' is required for {self.name}")
            elif param.default is not None:
                cleaned[param.name] = param.default
        return cleaned

    def to_catalog_entry(self) -> dict[str, Any]:
        """The form the router sees."""
        return {
            "name": self.name,
            "description": self.description,
            "consequential": self.consequential,
            "parameters": {
                p.name: {
                    "type": p.type,
                    "description": p.description,
                    "required": p.required,
                    **({"enum": list(p.enum)} if p.enum else {}),
                }
                for p in self.parameters
            },
        }


def _coerce(value: Any, param: SkillParam) -> Any:
    try:
        if param.type == "integer":
            return int(value)
        if param.type == "number":
            return float(value)
        if param.type == "boolean":
            if isinstance(value, str):
                return value.strip().lower() in {"true", "yes", "1", "on"}
            return bool(value)
        if param.type == "array":
            if isinstance(value, str):
                return [part.strip() for part in value.split(",") if part.strip()]
            return list(value)
        return str(value)
    except (TypeError, ValueError) as exc:
        raise SkillError(f"'{param.name}' should be a {param.type}") from exc

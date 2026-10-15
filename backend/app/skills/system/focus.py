"""Focus sessions — REQ-23.

Severity is decided per call, and it is decided honestly: starting a session
that will close three running apps is gated, because unsaved work is at stake.
Starting one when none of those apps are running closes nothing, so it just
starts. Same skill, different stakes.
"""

from __future__ import annotations

from typing import Any

from ... import focus
from ...settings import load_config
from ..base import Severity, Skill, SkillContext, SkillError, SkillParam, SkillResult
from .apps import matching_processes

DEFAULT_MINUTES = 25


def processes_to_close() -> list[Any]:
    """The running processes a focus session would terminate right now.

    A module function rather than a method on the skill, because the question
    has two askers: the skill, which needs it to decide severity and to write
    its preview, and GET /focus, which needs it to name the apps before the UI
    offers a button. That second caller used to reach through
    `StartFocusSkill._to_close()` -- the API layer calling a private method on
    a skill class, which is the kind of coupling that makes the skill contract
    meaningless.
    """
    found: list[Any] = []
    for name in load_config().system.distracting_apps:
        found.extend(matching_processes(name))
    return found


def would_close() -> list[str]:
    """Their names, deduplicated and sorted. What a person is shown."""
    return sorted({(process.info.get("name") or "?") for process in processes_to_close()})


class StartFocusSkill(Skill):
    name = "system.start_focus"
    description = (
        "Start a focus session for a number of minutes. Closes the apps configured as "
        "distracting, holds reminders until the session ends, and stops background "
        "indexing. Direct questions are still answered during a session."
    )
    parameters = (
        SkillParam("minutes", "integer", f"How long (default {DEFAULT_MINUTES}).",
                   required=False, default=DEFAULT_MINUTES),
    )
    consequential = True
    reversible = False

    def severity(self, args: dict[str, Any]) -> Severity:
        # Only worth interrupting for if something will actually be closed.
        return "consequential" if processes_to_close() else "routine"

    def preview(self, args: dict[str, Any]) -> str:
        minutes = int(args.get("minutes", DEFAULT_MINUTES) or DEFAULT_MINUTES)
        running = processes_to_close()
        if not running:
            return f"Start a {minutes} minute focus session."
        names = sorted({(p.info.get("name") or "?") for p in running})
        return (
            f"Start a {minutes} minute focus session and close {len(running)} running "
            f"process(es): {', '.join(names)}. Unsaved work in them will be lost."
        )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        if focus.is_active():
            raise SkillError(f"A focus session is already running. {focus.state().describe()}")

        minutes = int(args.get("minutes", DEFAULT_MINUTES) or DEFAULT_MINUTES)
        if minutes < 1:
            raise SkillError("A focus session needs to be at least a minute.")

        closed: list[str] = []
        for process in processes_to_close():
            try:
                name = process.info.get("name") or "?"
                process.terminate()
                closed.append(name)
            except Exception:  # noqa: BLE001 — a process that won't die isn't fatal
                continue

        focus.start(minutes, tuple(sorted(set(closed))))

        detail = f" Closed {', '.join(sorted(set(closed)))}." if closed else ""
        return SkillResult(
            ok=True,
            message=(
                f"Focus session started for {minutes} minutes.{detail} "
                "Reminders will hold until it ends."
            ),
            data={"minutes": minutes, "closed": sorted(set(closed))},
        )


class EndFocusSkill(Skill):
    name = "system.end_focus"
    description = (
        "End the current focus session early, or report whether one is running. "
        "Any reminders held during the session are delivered straight after."
    )
    parameters = ()

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        if not focus.is_active():
            return SkillResult(ok=True, message="No focus session is running.",
                               data={"active": False})
        focus.end()
        return SkillResult(
            ok=True,
            message="Focus session ended. Anything held while it ran will come through now.",
            data={"active": False},
        )


class FocusStatusSkill(Skill):
    name = "system.focus_status"
    description = "Report whether a focus session is running and how long is left."
    parameters = ()

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        state = focus.state()
        return SkillResult(
            ok=True,
            message=state.describe(),
            data={"active": state.active, "minutes_left": state.minutes_left},
        )

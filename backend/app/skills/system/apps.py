"""App and system control — REQ-22.

`launch_app` is routine: opening something is cheap and obvious to reverse.
`close_app` is consequential and honestly declared irreversible — Kai cannot
know whether there was unsaved work in there, so it asks, and the preview says
recovery is not possible.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..base import Severity, Skill, SkillContext, SkillError, SkillParam, SkillResult

IS_WINDOWS = os.name == "nt"

# Things people say vs. what actually launches.
ALIASES: dict[str, str] = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "paint": "mspaint.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "files": "explorer.exe",
    "task manager": "taskmgr.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "powershell": "powershell.exe",
    "terminal": "wt.exe",
    "settings": "ms-settings:",
    "browser": "https://",
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
    "firefox": "firefox.exe",
    "code": "code.exe",
    "vs code": "code.exe",
    "spotify": "spotify.exe",
    "discord": "discord.exe",
    "steam": "steam.exe",
}

_VK = {"mute": 0xAD, "down": 0xAE, "up": 0xAF}


def _start_menu_dirs() -> list[Path]:
    candidates = []
    for env in ("APPDATA", "PROGRAMDATA"):
        base = os.environ.get(env)
        if base:
            candidates.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return [c for c in candidates if c.is_dir()]


def _find_shortcut(name: str) -> Path | None:
    needle = name.lower().strip()
    best: tuple[int, Path] | None = None
    for directory in _start_menu_dirs():
        for shortcut in directory.rglob("*.lnk"):
            stem = shortcut.stem.lower()
            if stem == needle:
                return shortcut
            if needle in stem:
                score = len(stem) - len(needle)
                if best is None or score < best[0]:
                    best = (score, shortcut)
    return best[1] if best else None


def _resolve_target(name: str) -> str | None:
    key = name.lower().strip()
    if key in ALIASES:
        return ALIASES[key]
    if shutil.which(name):
        return name
    if shutil.which(f"{name}.exe"):
        return f"{name}.exe"
    shortcut = _find_shortcut(name)
    return str(shortcut) if shortcut else None


def _running_processes() -> list[Any]:
    try:
        import psutil
    except ImportError as exc:  # pragma: no cover
        raise SkillError("Process control isn't available (psutil not installed).") from exc
    return list(psutil.process_iter(["pid", "name", "exe"]))


def _matching_processes(name: str) -> list[Any]:
    needle = name.lower().replace(".exe", "").strip()
    matches = []
    for proc in _running_processes():
        try:
            proc_name = (proc.info.get("name") or "").lower().replace(".exe", "")
        except Exception:  # noqa: BLE001
            continue
        if proc_name == needle or (needle and needle in proc_name):
            matches.append(proc)
    return matches


class LaunchAppSkill(Skill):
    name = "system.launch_app"
    description = "Open an application by name, e.g. 'open Spotify', 'launch notepad'."
    parameters = (SkillParam("app", "string", "The app name as a person would say it."),)

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        app = str(args["app"]).strip()
        target = _resolve_target(app)
        if target is None:
            suggestion = _closest_installed(app)
            hint = f" Did you mean {suggestion}?" if suggestion else ""
            raise SkillError(f"I couldn't find an app called '{app}'.{hint}")

        try:
            if IS_WINDOWS:
                os.startfile(target)  # noqa: S606 — resolved from an allowlist/Start Menu
            else:
                subprocess.Popen([target], start_new_session=True)
        except OSError as exc:
            raise SkillError(f"'{app}' wouldn't start: {exc}") from exc

        return SkillResult(ok=True, message=f"Opened {app}.", data={"target": target})


class CloseAppSkill(Skill):
    name = "system.close_app"
    description = "Close a running application by name."
    parameters = (SkillParam("app", "string", "The app to close."),)
    consequential = True
    reversible = False

    def preview(self, args: dict[str, Any]) -> str:
        app = str(args.get("app", ""))
        matches = _matching_processes(app)
        if not matches:
            return f"Close {app} — but nothing by that name is running right now."
        names = sorted({(p.info.get("name") or "?") for p in matches})
        return (
            f"Close {len(matches)} running process(es): {', '.join(names)}. "
            "Unsaved work in them will be lost, and I can't undo this."
        )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        app = str(args["app"]).strip()
        matches = _matching_processes(app)
        if not matches:
            raise SkillError(f"'{app}' doesn't appear to be running.")

        closed, failed = 0, 0
        for proc in matches:
            try:
                proc.terminate()
                closed += 1
            except Exception:  # noqa: BLE001
                failed += 1

        message = f"Closed {closed} process(es) for {app}."
        if failed:
            message += f" {failed} refused to close (they may need admin rights)."
        return SkillResult(ok=True, message=message, data={"closed": closed, "failed": failed})


class SystemControlSkill(Skill):
    name = "system.control"
    description = (
        "Basic machine controls: volume up/down/mute, lock the screen, sleep, "
        "or report what is currently running."
    )
    parameters = (
        SkillParam(
            "action", "string", "What to do.",
            enum=("volume_up", "volume_down", "mute", "lock", "sleep", "list_running"),
        ),
        SkillParam("steps", "integer", "How many volume steps (default 4).",
                   required=False, default=4),
    )

    # Declared consequential so the class *may* gate, but severity() decides per
    # call. Locking and sleeping interrupt whatever the user is doing; changing
    # the volume does not, and asking about it would be absurd.
    consequential = True
    reversible = False

    _GATED = {"lock", "sleep"}

    def severity(self, args: dict[str, Any]) -> Severity:
        return "consequential" if str(args.get("action", "")) in self._GATED else "routine"

    def preview(self, args: dict[str, Any]) -> str:
        action = str(args.get("action", ""))
        if action == "lock":
            return "Lock the screen now."
        if action == "sleep":
            return "Put the machine to sleep now."
        return f"Run {action}."

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        action = str(args["action"])
        steps = int(args.get("steps", 4) or 4)

        if action in {"volume_up", "volume_down", "mute"}:
            return self._volume(action, steps)
        if action == "lock":
            return self._lock()
        if action == "sleep":
            return self._sleep()
        if action == "list_running":
            return self._list_running()
        raise SkillError(f"'{action}' isn't something I can do.")

    @staticmethod
    def _volume(action: str, steps: int) -> SkillResult:
        if not IS_WINDOWS:
            raise SkillError("Volume control is Windows-only in this build.")
        key = _VK["mute"] if action == "mute" else _VK["up" if action == "volume_up" else "down"]
        presses = 1 if action == "mute" else max(1, min(steps, 20))
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        for _ in range(presses):
            user32.keybd_event(key, 0, 0, 0)
            user32.keybd_event(key, 0, 2, 0)
        label = {"mute": "Toggled mute.", "volume_up": f"Volume up {presses} steps.",
                 "volume_down": f"Volume down {presses} steps."}[action]
        return SkillResult(ok=True, message=label)

    @staticmethod
    def _lock() -> SkillResult:
        if not IS_WINDOWS:
            raise SkillError("Screen lock is Windows-only in this build.")
        ok = ctypes.windll.user32.LockWorkStation()  # type: ignore[attr-defined]
        if not ok:
            raise SkillError("Windows refused the lock request.")
        return SkillResult(ok=True, message="Locked.")

    @staticmethod
    def _sleep() -> SkillResult:
        if not IS_WINDOWS:
            raise SkillError("Sleep is Windows-only in this build.")
        try:
            subprocess.run(
                ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                check=True, timeout=10,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise SkillError(f"Couldn't put the machine to sleep: {exc}") from exc
        return SkillResult(ok=True, message="Going to sleep.")

    @staticmethod
    def _list_running() -> SkillResult:
        seen: dict[str, int] = {}
        for proc in _running_processes():
            name = proc.info.get("name") or "?"
            seen[name] = seen.get(name, 0) + 1
        top = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)[:20]
        lines = [f"{name} ×{count}" if count > 1 else name for name, count in top]
        return SkillResult(
            ok=True,
            message=f"{len(seen)} distinct processes. Most instances:\n" + "\n".join(lines),
            data={"processes": dict(top)},
        )


def _closest_installed(name: str) -> str | None:
    needle = name.lower().strip()
    if not needle:
        return None
    for alias in ALIASES:
        if needle in alias or alias in needle:
            return alias
    shortcut = _find_shortcut(needle[:4]) if len(needle) >= 4 else None
    return shortcut.stem if shortcut else None

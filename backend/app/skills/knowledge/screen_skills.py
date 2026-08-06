"""Screen and clipboard assistance — REQ-17.

These skills only *fetch* the context. Explaining, summarising, translating,
extracting and rewriting are then done by the brain from the text they return —
so every verb REQ-17 lists works without a skill per verb, and a new one needs
no code at all.

Each capture is announced in the reply, because a capture the user didn't
notice is indistinguishable from one they didn't authorise.
"""

from __future__ import annotations

from typing import Any

from ...screen import capture, clipboard
from ..base import Skill, SkillContext, SkillError, SkillParam, SkillResult


class ReadClipboardSkill(Skill):
    name = "screen.clipboard"
    description = (
        "Read what the user has copied, so you can explain, summarise, translate, "
        "rewrite or extract from it. Use whenever they refer to 'this', 'what I "
        "copied', or paste-like phrasing without providing the text."
    )
    parameters = ()

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        if not clipboard.is_supported():
            raise SkillError("Clipboard access is Windows-only in this build.")

        try:
            text = clipboard.read_text()
        except clipboard.ClipboardUnavailable as exc:
            raise SkillError(str(exc)) from exc

        if not text.strip():
            raise SkillError(
                "The clipboard is empty, or holds something that isn't text."
            )

        # The read is disclosed in the reply, per REQ-17.
        return SkillResult(
            ok=True,
            message=f"Read from the clipboard ({clipboard.describe(text)}):\n\n{text}",
            data={"text": text, "characters": len(text)},
        )


class ReadScreenSkill(Skill):
    name = "screen.read"
    description = (
        "Read the text currently on screen when the user asks about something they "
        "are looking at and haven't given you as text. Reads the active window by "
        "default. This takes several seconds, so prefer screen.clipboard when the "
        "text could simply have been copied."
    )
    parameters = (
        SkillParam("full_screen", "boolean", "Read the whole screen instead of the "
                   "active window.", required=False, default=False),
    )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        if not capture.is_available():
            raise SkillError(
                "Reading the screen isn't set up on this machine "
                "(missing 'mss' or 'rapidocr-onnxruntime')."
            )

        full_screen = bool(args.get("full_screen", False))
        try:
            result = capture.read(full_screen=full_screen)
        except capture.ScreenUnavailable as exc:
            raise SkillError(str(exc)) from exc

        if result.is_empty:
            return SkillResult(
                ok=True,
                message=(
                    f"I took a picture of {result.window_title} but found no readable "
                    "text in it."
                ),
                data=result.to_dict(),
            )

        where = "the screen" if full_screen else f"\"{result.window_title}\""
        return SkillResult(
            ok=True,
            # Says a capture happened, what of, and how much came back (REQ-17).
            message=(
                f"Captured {where} and read {len(result.lines)} lines of text "
                f"({result.seconds:.0f}s). The image wasn't saved.\n\n{result.text}"
            ),
            data=result.to_dict(),
        )


class CopyToClipboardSkill(Skill):
    name = "screen.copy"
    description = (
        "Put text on the user's clipboard so they can paste it. Use after rewriting "
        "or drafting something when they ask for it to be copied."
    )
    parameters = (
        SkillParam("text", "string", "The exact text to place on the clipboard."),
    )
    reversible = True

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        text = str(args["text"])
        if not text.strip():
            raise SkillError("There was nothing to copy.")

        # Keep what was there so the write can be taken back — replacing someone's
        # clipboard without a way to restore it loses whatever they were holding.
        try:
            previous = clipboard.read_text()
            clipboard.write_text(text)
        except clipboard.ClipboardUnavailable as exc:
            raise SkillError(str(exc)) from exc

        return SkillResult(
            ok=True,
            message=f"Copied to the clipboard ({clipboard.describe(text)}).",
            data={"characters": len(text)},
            undo_payload={"previous": previous},
        )

    def undo(self, undo_payload: dict[str, Any]) -> SkillResult:
        previous = str(undo_payload.get("previous", ""))
        if not previous:
            return SkillResult(ok=False, message="There was nothing on the clipboard before.")
        try:
            clipboard.write_text(previous)
        except clipboard.ClipboardUnavailable as exc:
            return SkillResult(ok=False, message=str(exc))
        return SkillResult(ok=True, message="Put the previous clipboard contents back.")

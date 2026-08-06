"""Calendar skills — REQ-8, REQ-24, REQ-27.

Reads are routine. Writes are gated, and the preview states the *resolved*
date and time rather than the phrase the user said — "Thursday at 3" is the one
thing they cannot check without being told what it turned into.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from ...connectors import base as connectors
from ...connectors import calendar as cal
from ..base import Skill, SkillContext, SkillError, SkillParam, SkillResult
from . import timeparse

DEFAULT_DURATION_MINUTES = 60


TODAY_WORDS = {
    "", "today", "hoy", "this morning", "this afternoon", "this evening",
    "tonight", "now", "later", "later today", "rest of the day", "esta tarde",
}
TOMORROW_WORDS = {"tomorrow", "mañana", "manana", "tomorrow morning", "tomorrow afternoon"}


def resolve_day(phrase: str) -> date:
    """Turn a day phrase into a date, or raise with something the user can act on.

    "this afternoon" and "tonight" name a part of today, not a different day —
    handling them here keeps two skills from each getting it subtly different.
    """
    cleaned = (phrase or "").strip().lower()
    today = datetime.now().astimezone().date()

    if cleaned in TODAY_WORDS:
        return today
    if cleaned in TOMORROW_WORDS:
        return today + timedelta(days=1)
    if cleaned in {"yesterday", "ayer"}:
        return today - timedelta(days=1)

    parsed = timeparse.parse(cleaned)
    if parsed is None:
        raise SkillError(
            f"I couldn't work out which day '{phrase}' is. Try 'today', 'tomorrow', "
            "a weekday, or a date like 2026-03-14."
        )
    return parsed.when.astimezone().date()


def _gather(since: datetime, until: datetime) -> tuple[list[cal.Event], list[str]]:
    """Read every configured calendar, collecting failures rather than raising.

    One unreachable calendar must not hide the events in the others (REQ-27).
    """
    events: list[cal.Event] = []
    problems: list[str] = []
    for config in connectors.require("calendar"):
        try:
            events.extend(cal.events_between(config, since, until))
        except connectors.ConnectorError as exc:
            problems.append(f"{config.label}: {exc}")
    events.sort(key=lambda e: e.start)
    return events, problems


class AgendaSkill(Skill):
    name = "calendar.agenda"
    description = (
        "What is on the user's calendar - today, tomorrow, a named day, or the next "
        "few days. Use for 'what's on today', 'what's next', 'am I busy Friday'."
    )
    parameters = (
        SkillParam("when", "string", "A day phrase like 'today', 'tomorrow', 'friday'.",
                   required=False, default="today"),
        SkillParam("days", "integer", "How many days to cover (default 1).",
                   required=False, default=1),
    )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        phrase = str(args.get("when", "today") or "today").strip().lower()
        days = max(1, min(int(args.get("days", 1) or 1), 14))

        start_day = resolve_day(phrase)

        since, _ = cal.day_bounds(start_day)
        _, until = cal.day_bounds(start_day + timedelta(days=days - 1))

        events, problems = _gather(since, until)
        label = "today" if phrase in {"today", ""} else phrase

        if not events:
            message = f"Nothing on the calendar for {label}."
        else:
            lines = [f"  {e.describe(with_date=days > 1)}" for e in events]
            message = f"{len(events)} event(s) {label}:\n" + "\n".join(lines)

        # A failed source is named, not silently dropped (REQ-27).
        if problems:
            message += "\n(Couldn't read: " + "; ".join(problems) + ")"

        return SkillResult(
            ok=True,
            message=message,
            data={"events": [e.to_dict() for e in events], "problems": problems},
        )


class FindFreeTimeSkill(Skill):
    name = "calendar.find_free_time"
    description = (
        "Find gaps in the user's day. Use for 'am I free Thursday afternoon' or "
        "'when could I fit in an hour this week'."
    )
    parameters = (
        SkillParam("when", "string", "The day to look at.", required=False, default="today"),
        SkillParam("minutes", "integer", "Minimum gap in minutes (default 30).",
                   required=False, default=30),
    )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        phrase = str(args.get("when", "today") or "today").strip().lower()
        minimum = max(15, int(args.get("minutes", 30) or 30))

        day = resolve_day(phrase)

        since, until = cal.day_bounds(day)
        events, problems = _gather(since, until)

        # Only offer waking hours; "you're free at 4am" is not a useful answer.
        local = datetime.now().astimezone().tzinfo
        work_start = datetime.combine(day, datetime.min.time()).replace(hour=8, tzinfo=local)
        work_end = datetime.combine(day, datetime.min.time()).replace(hour=20, tzinfo=local)
        slots = cal.free_slots(
            events, work_start.astimezone(timezone.utc), work_end.astimezone(timezone.utc), minimum
        )

        if not slots:
            return SkillResult(ok=True, message=f"No free gaps of {minimum}+ minutes {phrase}.",
                               data={"slots": []})

        lines = [
            f"  {a.astimezone().strftime('%H:%M')} - {b.astimezone().strftime('%H:%M')} "
            f"({int((b - a).total_seconds() // 60)} min)"
            for a, b in slots
        ]
        message = f"Free {phrase}:\n" + "\n".join(lines)
        if problems:
            message += "\n(Couldn't read: " + "; ".join(problems) + ")"
        return SkillResult(
            ok=True, message=message,
            data={"slots": [[a.isoformat(), b.isoformat()] for a, b in slots]},
        )


class CreateEventSkill(Skill):
    name = "calendar.create_event"
    description = (
        "Add an event to the calendar. Pass the user's own time phrase through "
        "unchanged - do not resolve the date yourself."
    )
    parameters = (
        SkillParam("title", "string", "What the event is called."),
        SkillParam("when", "string", "The time phrase exactly as the user said it."),
        SkillParam("minutes", "integer", f"Length (default {DEFAULT_DURATION_MINUTES}).",
                   required=False, default=DEFAULT_DURATION_MINUTES),
        SkillParam("location", "string", "Optional location.", required=False),
        SkillParam("calendar", "string", "Which calendar, if more than one.", required=False),
    )
    consequential = True
    reversible = True

    @staticmethod
    def _resolve(args: dict[str, Any]) -> tuple[datetime, datetime, str]:
        phrase = str(args.get("when", "")).strip()
        parsed = timeparse.parse(phrase)
        if parsed is None:
            raise SkillError(
                f"I couldn't work out when '{phrase}' is. Try 'Thursday at 3pm' "
                "or '2026-03-14 at 09:00'."
            )
        minutes = max(5, int(args.get("minutes", DEFAULT_DURATION_MINUTES)
                             or DEFAULT_DURATION_MINUTES))
        start = parsed.when
        return start, start + timedelta(minutes=minutes), phrase

    def preview(self, args: dict[str, Any]) -> str:
        start, end, _ = self._resolve(args)
        target = connectors.find("calendar", str(args.get("calendar", "") or ""))
        where = f" at {args['location']}" if args.get("location") else ""
        # REQ-8: the resolved date and time, not the phrase that produced them.
        return (
            f"Add \"{args.get('title', '')}\"{where} to the {target.label} calendar on "
            f"{start.astimezone().strftime('%A %d %B at %H:%M')}, ending "
            f"{end.astimezone().strftime('%H:%M')}."
        )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        start, end, _ = self._resolve(args)
        target = connectors.find("calendar", str(args.get("calendar", "") or ""))
        event = cal.create_event(
            target,
            summary=str(args["title"]),
            start=start,
            end=end,
            location=str(args.get("location", "") or ""),
        )
        return SkillResult(
            ok=True,
            message=f"Added \"{event.summary}\" on "
                    f"{start.astimezone().strftime('%A %d %B at %H:%M')}.",
            data=event.to_dict(),
            undo_payload={"calendar": target.label, "uid": event.uid},
        )

    def undo(self, undo_payload: dict[str, Any]) -> SkillResult:
        uid = str(undo_payload.get("uid", ""))
        if not uid:
            return SkillResult(ok=False, message="I don't have the event's id to remove it.")
        target = connectors.find("calendar", str(undo_payload.get("calendar", "")))
        removed = cal.delete_event(target, uid)
        return SkillResult(
            ok=removed,
            message="Removed the event again." if removed else "I couldn't find it to remove.",
        )


class CancelEventSkill(Skill):
    name = "calendar.cancel_event"
    description = "Cancel an event on the calendar, matched by its title."
    parameters = (
        SkillParam("title", "string", "Words from the event title."),
        SkillParam("when", "string", "Which day it is on.", required=False, default="today"),
    )
    consequential = True
    reversible = False

    @staticmethod
    def _matches(args: dict[str, Any]) -> list[cal.Event]:
        phrase = str(args.get("when", "today") or "today").strip().lower()
        try:
            day = resolve_day(phrase)
        except SkillError:
            day = datetime.now().astimezone().date()

        since, until = cal.day_bounds(day)
        events, _ = _gather(since, until)
        needle = str(args.get("title", "")).lower()
        return [e for e in events if needle in e.summary.lower()]

    def preview(self, args: dict[str, Any]) -> str:
        matches = self._matches(args)
        if not matches:
            return f"Cancel \"{args.get('title', '')}\" - but nothing matches on that day."
        listed = "; ".join(e.describe(with_date=True) for e in matches[:5])
        return (
            f"Cancel {len(matches)} event(s): {listed}. "
            "This can't be undone from here."
        )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        matches = self._matches(args)
        if not matches:
            raise SkillError(f"I couldn't find an event matching '{args.get('title')}'.")
        if len(matches) > 1:
            listed = "; ".join(e.summary for e in matches[:5])
            raise SkillError(f"That matches {len(matches)} events ({listed}). Which one?")

        event = matches[0]
        target = connectors.find("calendar", event.calendar)
        if not cal.delete_event(target, event.uid):
            raise SkillError("The calendar wouldn't let me remove that event.")
        return SkillResult(ok=True, message=f"Cancelled \"{event.summary}\".",
                           data=event.to_dict())

"""Natural-language time parsing — REQ-9.

Deliberately narrow and deterministic rather than clever. It covers the phrasings
people actually use for reminders, and returns `None` for anything it is not sure
about so the caller can ask instead of scheduling something for the wrong day.

Every parse result is echoed back to the user as a resolved absolute time before
it is accepted — that is the requirement, and it is also the safety net for the
cases this parser reads differently than a person would.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

UNIT_SECONDS = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
    "day": 86400, "days": 86400,
    "week": 604800, "weeks": 604800,
}


@dataclass
class ParsedTime:
    when: datetime
    recurrence: dict[str, Any] | None = None

    def describe(self) -> str:
        stamp = self.when.strftime("%A %d %B at %H:%M")
        if self.recurrence:
            return f"{stamp}, then {describe_recurrence(self.recurrence)}"
        return stamp


def describe_recurrence(recurrence: dict[str, Any]) -> str:
    kind = recurrence.get("type")
    time_part = recurrence.get("time", "")
    if kind == "daily":
        return f"every day at {time_part}"
    if kind == "weekdays":
        return f"every weekday at {time_part}"
    if kind == "weekly":
        day = [k for k, v in WEEKDAYS.items() if v == recurrence.get("weekday") and len(k) > 3]
        return f"every {day[0] if day else 'week'} at {time_part}"
    if kind == "interval":
        return f"every {_humanize_seconds(int(recurrence.get('seconds', 0)))}"
    return "on a schedule"


def _humanize_seconds(seconds: int) -> str:
    for label, size in (("week", 604800), ("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds % size == 0 and seconds >= size:
            count = seconds // size
            return f"{count} {label}{'s' if count != 1 else ''}"
    return f"{seconds} seconds"


def parse(text: str, *, now: datetime | None = None) -> ParsedTime | None:
    """Parse a time phrase. Returns None when the phrasing is not understood."""
    if not text:
        return None
    now = now or datetime.now().astimezone()
    lowered = text.strip().lower()

    for parser in (_parse_recurring, _parse_relative, _parse_named_day, _parse_weekday,
                   _parse_absolute, _parse_bare_time):
        result = parser(lowered, now)
        if result is not None:
            return result
    return None


# -- recurring -------------------------------------------------------------


def _parse_recurring(text: str, now: datetime) -> ParsedTime | None:
    if not text.startswith(("every ", "each ")):
        return None
    body = text.split(" ", 1)[1].strip()

    # "every 30 minutes"
    interval = re.match(r"^(\d+)\s+(\w+)$", body)
    if interval and interval.group(2) in UNIT_SECONDS:
        seconds = int(interval.group(1)) * UNIT_SECONDS[interval.group(2)]
        if seconds < 30:
            return None
        return ParsedTime(now + timedelta(seconds=seconds), {"type": "interval", "seconds": seconds})

    time_of_day = _extract_time(body) or (9, 0)
    hour, minute = time_of_day

    if body.startswith(("day", "morning")):
        return ParsedTime(_next_at(now, hour, minute),
                          {"type": "daily", "time": f"{hour:02d}:{minute:02d}"})

    if "weekday" in body or "work day" in body or "workday" in body:
        target = _next_at(now, hour, minute)
        while target.weekday() > 4:
            target += timedelta(days=1)
        return ParsedTime(target, {"type": "weekdays", "time": f"{hour:02d}:{minute:02d}"})

    for name, index in WEEKDAYS.items():
        if re.search(rf"\b{name}\b", body):
            return ParsedTime(
                _next_weekday(now, index, hour, minute),
                {"type": "weekly", "weekday": index, "time": f"{hour:02d}:{minute:02d}"},
            )
    return None


# -- one-off ---------------------------------------------------------------


def _parse_relative(text: str, now: datetime) -> ParsedTime | None:
    match = re.match(r"^(?:in|after)\s+(\d+(?:\.\d+)?)\s*(\w+)", text)
    if not match:
        # "in an hour", "in a minute"
        worded = re.match(r"^(?:in|after)\s+an?\s+(\w+)", text)
        if worded and worded.group(1) in UNIT_SECONDS:
            return ParsedTime(now + timedelta(seconds=UNIT_SECONDS[worded.group(1)]))
        return None

    unit = match.group(2)
    if unit not in UNIT_SECONDS:
        return None
    seconds = float(match.group(1)) * UNIT_SECONDS[unit]
    return ParsedTime(now + timedelta(seconds=seconds))


def _parse_named_day(text: str, now: datetime) -> ParsedTime | None:
    if text.startswith("tomorrow"):
        hour, minute = _extract_time(text) or (9, 0)
        target = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return ParsedTime(target)
    if text.startswith("tonight"):
        hour, minute = _extract_time(text) or (20, 0)
        return ParsedTime(now.replace(hour=hour, minute=minute, second=0, microsecond=0))
    if text.startswith("today"):
        time_of_day = _extract_time(text)
        if time_of_day is None:
            return None
        hour, minute = time_of_day
        return ParsedTime(now.replace(hour=hour, minute=minute, second=0, microsecond=0))
    return None


def _parse_weekday(text: str, now: datetime) -> ParsedTime | None:
    stripped = re.sub(r"^(next|on|this)\s+", "", text)
    for name, index in WEEKDAYS.items():
        if re.match(rf"^{name}\b", stripped):
            hour, minute = _extract_time(stripped) or (9, 0)
            force_next = text.startswith("next")
            return ParsedTime(_next_weekday(now, index, hour, minute, force_next=force_next))
    return None


def _parse_absolute(text: str, now: datetime) -> ParsedTime | None:
    match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if not match:
        return None
    hour, minute = _extract_time(text) or (9, 0)
    try:
        target = datetime(
            int(match.group(1)), int(match.group(2)), int(match.group(3)),
            hour, minute, tzinfo=now.tzinfo,
        )
    except ValueError:
        return None
    return ParsedTime(target)


def _parse_bare_time(text: str, now: datetime) -> ParsedTime | None:
    """'at 6pm' / '18:30' on its own means the next time that clock reading occurs."""
    if not re.match(r"^(at\s+)?\d{1,2}([:.]\d{2})?\s*(am|pm)?$", text.strip()):
        return None
    time_of_day = _extract_time(text)
    if time_of_day is None:
        return None
    return ParsedTime(_next_at(now, *time_of_day))


# -- helpers ---------------------------------------------------------------


def _extract_time(text: str) -> tuple[int, int] | None:
    match = re.search(r"\b(\d{1,2})[:.](\d{2})\s*(am|pm)?\b", text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        hour = _apply_meridiem(hour, match.group(3))
        return (hour, minute) if 0 <= hour < 24 and 0 <= minute < 60 else None

    match = re.search(r"\b(\d{1,2})\s*(am|pm)\b", text)
    if match:
        hour = _apply_meridiem(int(match.group(1)), match.group(2))
        return (hour, 0) if 0 <= hour < 24 else None

    match = re.search(r"\bat\s+(\d{1,2})\b", text)
    if match:
        hour = int(match.group(1))
        # "at 6" almost always means the evening; "at 11" means the morning.
        if hour <= 7:
            hour += 12
        return (hour, 0) if hour < 24 else None
    return None


def _apply_meridiem(hour: int, meridiem: str | None) -> int:
    if meridiem == "pm" and hour < 12:
        return hour + 12
    if meridiem == "am" and hour == 12:
        return 0
    return hour


def _next_at(now: datetime, hour: int, minute: int) -> datetime:
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def _next_weekday(now: datetime, weekday: int, hour: int, minute: int,
                  *, force_next: bool = False) -> datetime:
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days_ahead = (weekday - target.weekday()) % 7
    if days_ahead == 0 and (target <= now or force_next):
        days_ahead = 7
    elif force_next and days_ahead < 7:
        days_ahead += 0 if days_ahead else 7
    return target + timedelta(days=days_ahead)


def next_occurrence(recurrence: dict[str, Any], after: datetime) -> datetime | None:
    """Where a recurring item fires next, given it just fired at `after`."""
    kind = recurrence.get("type")

    if kind == "interval":
        seconds = int(recurrence.get("seconds", 0))
        return after + timedelta(seconds=seconds) if seconds > 0 else None

    raw_time = str(recurrence.get("time", "09:00"))
    try:
        hour, minute = (int(part) for part in raw_time.split(":"))
    except ValueError:
        hour, minute = 9, 0

    if kind == "daily":
        return _next_at(after, hour, minute)
    if kind == "weekdays":
        target = _next_at(after, hour, minute)
        while target.weekday() > 4:
            target += timedelta(days=1)
        return target
    if kind == "weekly":
        return _next_weekday(after, int(recurrence.get("weekday", 0)), hour, minute, force_next=True)
    return None

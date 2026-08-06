"""Calendar access — REQ-8, REQ-27.

Two providers, chosen for how easily a person can actually connect them:

* **ics** — a local `.ics` file or a subscription URL. Google, Outlook, Apple and
  Fastmail all publish one, so this works for almost everybody with a copy and a
  paste. Read-only, by nature.
* **caldav** — a real two-way connection for servers that accept an
  app-specific password (Fastmail, Nextcloud, iCloud). Reads and writes.

Google Calendar's own API is deliberately not used. It needs an OAuth client
secret and a browser consent dance, which drags the whole "free, single-step
install" property down for one provider that already publishes ICS.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from .base import AuthFailed, ConnectorConfig, ConnectorError

log = logging.getLogger(__name__)

FETCH_TIMEOUT = 15.0


@dataclass
class Event:
    uid: str
    summary: str
    start: datetime
    end: datetime | None = None
    location: str = ""
    all_day: bool = False
    calendar: str = ""

    @property
    def duration_minutes(self) -> int:
        if self.end is None:
            return 0
        return max(0, int((self.end - self.start).total_seconds() // 60))

    def describe(self, *, with_date: bool = False) -> str:
        local_start = self.start.astimezone()
        if self.all_day:
            when = local_start.strftime("%a %d %b") if with_date else "all day"
            return f"{when} - {self.summary}" if with_date else f"{self.summary} (all day)"
        stamp = local_start.strftime("%a %d %b %H:%M") if with_date else local_start.strftime("%H:%M")
        tail = f" ({self.duration_minutes} min)" if self.duration_minutes else ""
        where = f" - {self.location}" if self.location else ""
        return f"{stamp} {self.summary}{tail}{where}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "summary": self.summary,
            "start": self.start.isoformat(),
            "end": self.end.isoformat() if self.end else None,
            "location": self.location,
            "all_day": self.all_day,
            "calendar": self.calendar,
        }


def _as_datetime(value: Any) -> tuple[datetime, bool]:
    """Normalise an icalendar date or datetime to aware UTC, flagging all-day."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return value.astimezone(timezone.utc), False
    if isinstance(value, date):
        local = datetime.combine(value, time.min).replace(
            tzinfo=datetime.now().astimezone().tzinfo
        )
        return local.astimezone(timezone.utc), True
    raise ConnectorError(f"Unrecognised date value: {value!r}")


# -- ICS -------------------------------------------------------------------


def _load_ics_text(config: ConnectorConfig) -> str:
    target = config.url.strip()
    if not target:
        raise ConnectorError(f"Calendar '{config.label}' has no url or file path.")

    if target.startswith(("http://", "https://", "webcal://")):
        import httpx

        url = target.replace("webcal://", "https://", 1)
        try:
            response = httpx.get(url, timeout=FETCH_TIMEOUT, follow_redirects=True)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"Couldn't fetch calendar '{config.label}': {exc}") from exc
        return response.text

    path = Path(target).expanduser()
    if not path.exists():
        raise ConnectorError(f"Calendar file '{path}' doesn't exist.")
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ConnectorError(f"Couldn't read '{path}': {exc}") from exc


def _parse_ics(text: str, *, label: str, since: datetime, until: datetime) -> list[Event]:
    try:
        from icalendar import Calendar as ICalendar
    except ImportError as exc:
        raise ConnectorError("The 'icalendar' package isn't installed.") from exc

    try:
        calendar = ICalendar.from_ical(text)
    except Exception as exc:  # noqa: BLE001
        raise ConnectorError(f"Calendar '{label}' isn't valid iCalendar data: {exc}") from exc

    events: list[Event] = []
    for component in calendar.walk("VEVENT"):
        try:
            start_raw = component.get("DTSTART")
            if start_raw is None:
                continue
            start, all_day = _as_datetime(start_raw.dt)

            end = None
            end_raw = component.get("DTEND")
            if end_raw is not None:
                end, _ = _as_datetime(end_raw.dt)

            if start > until or (end or start) < since:
                continue

            events.append(
                Event(
                    uid=str(component.get("UID", "")),
                    summary=str(component.get("SUMMARY", "(no title)")),
                    start=start,
                    end=end,
                    location=str(component.get("LOCATION", "") or ""),
                    all_day=all_day,
                    calendar=label,
                )
            )
        except Exception:  # noqa: BLE001 — one bad event must not lose the calendar
            log.debug("skipping unparseable event in %s", label, exc_info=True)
            continue

    return events


# -- CalDAV ----------------------------------------------------------------


def _caldav_client(config: ConnectorConfig) -> Any:
    try:
        import caldav
    except ImportError as exc:
        raise ConnectorError("The 'caldav' package isn't installed.") from exc

    try:
        client = caldav.DAVClient(
            url=config.url, username=config.username, password=config.secret()
        )
        return client.principal()
    except AuthFailed:
        raise
    except Exception as exc:  # noqa: BLE001
        message = str(exc).lower()
        if "401" in message or "unauthor" in message or "forbidden" in message:
            raise AuthFailed(
                f"The password for '{config.label}' was rejected. "
                f"Run: /connect calendar {config.label}"
            ) from exc
        raise ConnectorError(f"Couldn't reach calendar '{config.label}': {exc}") from exc


def _caldav_events(
    config: ConnectorConfig, since: datetime, until: datetime
) -> list[Event]:
    principal = _caldav_client(config)
    events: list[Event] = []
    try:
        for calendar in principal.calendars():
            for found in calendar.search(start=since, end=until, event=True, expand=True):
                events.extend(
                    _parse_ics(found.data, label=config.label, since=since, until=until)
                )
    except Exception as exc:  # noqa: BLE001
        raise ConnectorError(f"Couldn't read calendar '{config.label}': {exc}") from exc
    return events


# -- public ----------------------------------------------------------------


def events_between(
    config: ConnectorConfig, since: datetime, until: datetime
) -> list[Event]:
    if config.provider == "ics":
        return _parse_ics(
            _load_ics_text(config), label=config.label, since=since, until=until
        )
    if config.provider == "caldav":
        return _caldav_events(config, since, until)
    raise ConnectorError(f"Unknown calendar provider '{config.provider}'.")


def create_event(
    config: ConnectorConfig,
    *,
    summary: str,
    start: datetime,
    end: datetime,
    location: str = "",
) -> Event:
    if config.provider != "caldav" or not config.writable:
        raise ConnectorError(
            f"Calendar '{config.label}' is read-only. "
            "Writing needs a CalDAV connector with writable: true."
        )

    principal = _caldav_client(config)
    try:
        calendars = principal.calendars()
        if not calendars:
            raise ConnectorError(f"No writable calendar found on '{config.label}'.")
        created = calendars[0].save_event(
            dtstart=start, dtend=end, summary=summary, location=location or None
        )
    except ConnectorError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ConnectorError(f"Couldn't create the event: {exc}") from exc

    uid = ""
    try:
        uid = str(created.icalendar_component.get("UID", ""))
    except Exception:  # noqa: BLE001
        pass

    return Event(
        uid=uid, summary=summary, start=start, end=end, location=location, calendar=config.label
    )


def delete_event(config: ConnectorConfig, uid: str) -> bool:
    if config.provider != "caldav" or not config.writable:
        raise ConnectorError(f"Calendar '{config.label}' is read-only.")

    principal = _caldav_client(config)
    try:
        for calendar in principal.calendars():
            for found in calendar.events():
                component = found.icalendar_component
                if str(component.get("UID", "")) == uid:
                    found.delete()
                    return True
    except Exception as exc:  # noqa: BLE001
        raise ConnectorError(f"Couldn't cancel the event: {exc}") from exc
    return False


def day_bounds(day: date | None = None) -> tuple[datetime, datetime]:
    day = day or datetime.now().astimezone().date()
    local = datetime.now().astimezone().tzinfo
    start = datetime.combine(day, time.min).replace(tzinfo=local)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def free_slots(
    events: list[Event], since: datetime, until: datetime, minimum_minutes: int = 30
) -> list[tuple[datetime, datetime]]:
    """Gaps between events, for "am I free Thursday afternoon" (REQ-8)."""
    busy = sorted(
        (e for e in events if not e.all_day and e.end), key=lambda e: e.start
    )
    slots: list[tuple[datetime, datetime]] = []
    cursor = since

    for event in busy:
        if event.start > cursor:
            gap = int((event.start - cursor).total_seconds() // 60)
            if gap >= minimum_minutes:
                slots.append((cursor, event.start))
        cursor = max(cursor, event.end or cursor)

    if until > cursor:
        gap = int((until - cursor).total_seconds() // 60)
        if gap >= minimum_minutes:
            slots.append((cursor, until))
    return slots

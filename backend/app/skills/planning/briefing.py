"""Daily briefing — REQ-11, REQ-27.

Sections are gathered in parallel with a per-source timeout, because a briefing
is only useful if it arrives promptly and one slow IMAP server should not hold
up the calendar.

A section whose source fails is replaced by a single honest line rather than
dropped. Silently omitting it would be worse than useless: the user would read
"nothing needs your attention" and believe it.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ... import db
from ...connectors import base as connectors
from ...connectors import calendar as cal
from ...connectors import mail
from ...scheduler import store as sched_store
from ...settings import load_config
from ..base import Skill, SkillContext, SkillParam, SkillResult

log = logging.getLogger(__name__)

SECTION_TIMEOUT = 12.0
DEFAULT_SECTIONS = ("calendar", "reminders", "tasks", "mail")


@dataclass
class Section:
    name: str
    lines: list[str]
    error: str = ""

    def render(self) -> str:
        if self.error:
            # Named, not omitted (REQ-27).
            return f"{self.name.title()}: couldn't check ({self.error})"
        if not self.lines:
            return ""
        return f"{self.name.title()}:\n" + "\n".join(f"  {line}" for line in self.lines)


def _calendar_section() -> Section:
    try:
        since, until = cal.day_bounds()
    except Exception as exc:  # noqa: BLE001
        return Section("calendar", [], str(exc))

    lines: list[str] = []
    problems: list[str] = []
    try:
        configs = connectors.require("calendar")
    except connectors.NotConfigured:
        return Section("calendar", [])  # not set up is not a failure

    for config in configs:
        try:
            for event in cal.events_between(config, since, until):
                lines.append(event.describe())
        except connectors.ConnectorError as exc:
            problems.append(f"{config.label}: {exc}")

    section = Section("calendar", sorted(lines))
    if problems and not lines:
        section.error = "; ".join(problems)
    elif problems:
        section.lines.append(f"(couldn't read {', '.join(problems)})")
    if not section.lines and not section.error:
        section.lines = ["nothing scheduled"]
    return section


def _reminders_section() -> Section:
    try:
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(days=1)
        items = [
            item for item in sched_store.active_items()
            if item.next_fire_at and item.next_fire_at <= horizon
        ]
        if not items:
            return Section("reminders", [])
        return Section("reminders", [item.describe() for item in items[:10]])
    except Exception as exc:  # noqa: BLE001
        return Section("reminders", [], str(exc))


def _tasks_section() -> Section:
    try:
        rows = db.query(
            "SELECT text, due FROM tasks WHERE done = 0 "
            "ORDER BY COALESCE(due, '9999') ASC, created_at DESC LIMIT 8"
        )
        if not rows:
            return Section("tasks", [])
        today = datetime.now().astimezone().date().isoformat()
        lines = []
        for row in rows:
            overdue = " (overdue)" if row["due"] and row["due"] < today else ""
            due = f" - due {row['due']}" if row["due"] else ""
            lines.append(f"{row['text']}{due}{overdue}")
        return Section("tasks", lines)
    except Exception as exc:  # noqa: BLE001
        return Section("tasks", [], str(exc))


def _mail_section() -> Section:
    try:
        configs = connectors.require("mail")
    except connectors.NotConfigured:
        return Section("mail", [])

    lines: list[str] = []
    problems: list[str] = []
    for config in configs:
        try:
            messages = mail.fetch_unread(config, limit=25)
            needs_reply = [m for m in messages if m.probably_needs_reply]
            if not messages:
                continue
            lines.append(f"{len(messages)} unread in {config.label}"
                         + (f", {len(needs_reply)} may need a reply" if needs_reply else ""))
            lines += [f"  {m.describe()}" for m in needs_reply[:5]]
        except connectors.ConnectorError as exc:
            problems.append(f"{config.label}: {exc}")

    section = Section("mail", lines)
    if problems and not lines:
        section.error = "; ".join(problems)
    elif problems:
        section.lines.append(f"(couldn't read {', '.join(problems)})")
    return section


BUILDERS: dict[str, Callable[[], Section]] = {
    "calendar": _calendar_section,
    "reminders": _reminders_section,
    "tasks": _tasks_section,
    "mail": _mail_section,
}


def build(sections: tuple[str, ...] = DEFAULT_SECTIONS) -> list[Section]:
    """Fan out, with a hard per-section timeout."""
    wanted = [name for name in sections if name in BUILDERS]
    results: dict[str, Section] = {}

    with ThreadPoolExecutor(max_workers=max(1, len(wanted))) as pool:
        futures = {pool.submit(BUILDERS[name]): name for name in wanted}
        for future, name in futures.items():
            try:
                results[name] = future.result(timeout=SECTION_TIMEOUT)
            except FutureTimeout:
                results[name] = Section(name, [], f"timed out after {int(SECTION_TIMEOUT)}s")
            except Exception as exc:  # noqa: BLE001
                log.exception("briefing section %s failed", name)
                results[name] = Section(name, [], str(exc))

    # Preserve the configured order, not whichever thread finished first.
    return [results[name] for name in wanted if name in results]


class BriefingSkill(Skill):
    name = "planning.briefing"
    description = (
        "The user's catch-up for the day: calendar, due reminders, open tasks and "
        "unread mail. Use for 'what's my day look like', 'brief me', 'catch me up'."
    )
    parameters = (
        SkillParam("sections", "array", "Which sections, in order. Defaults to all.",
                   required=False),
    )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        requested = args.get("sections") or list(_configured_sections())
        sections = build(tuple(str(s).lower() for s in requested))

        rendered = [section.render() for section in sections]
        body = "\n\n".join(part for part in rendered if part)

        stamp = datetime.now().astimezone().strftime("%A %d %B")
        if not body:
            body = "Nothing on the calendar, nothing due, and no unread mail."

        return SkillResult(
            ok=True,
            message=f"{stamp}\n\n{body}",
            data={
                "sections": [
                    {"name": s.name, "lines": s.lines, "error": s.error} for s in sections
                ]
            },
        )


def _configured_sections() -> tuple[str, ...]:
    raw = getattr(load_config(), "connectors", {}) or {}
    configured = raw.get("briefing_sections")
    if isinstance(configured, list) and configured:
        return tuple(str(s).lower() for s in configured)
    return DEFAULT_SECTIONS

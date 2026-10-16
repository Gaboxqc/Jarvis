"""Taking your data with you — REQ-26.

`wipe_all_local_data()` has been the only control over the record since the
beginning: all of it, or none of it. REQ-26 asks for local data *control*, and
for a long time the only direction that control pointed was destruction. You
could delete everything you had said and done; you could not read it.

So this is the other half. One zip, everything the database holds about the
person using it, in formats that open without this app:

    memory.json          facts you asked to be remembered
    tasks.json           tasks and notes
    tasks.md             the same, as the plain-Markdown mirror already kept
    reminders.json       scheduled items, routines and shortcuts
    conversations.json   what was said, per session
    actions.json         every side effect, with its preview and outcome
    transcripts/*.md     one file per recorded meeting, with its summary
    documents.json       which files were indexed -- paths, not contents
    config.yaml          the settings, with connectors stripped
    README.md            what each file is

What is deliberately not in it
------------------------------

**Secrets.** `credentials.fetch` is never called here, and the `connectors`
section is removed from the exported config rather than emptied, so there is
no field left to wonder about. A calendar's "secret address in iCal format" is
a credential in its own right and it lives in that section.

**The document index itself.** `documents.json` lists what was indexed -- the
paths, sizes and chunk counts -- and not the extracted text. The text is a
copy of the user's own files, which they already have; putting a second copy
inside an export turns "here is your data" into a duplicate of a Documents
folder, and a 3,000-chunk index into tens of megabytes of zip.

**The API token.** It authenticates this install. It is not a record of
anything and exporting it only spreads it.

**Embedding vectors.** Derived, enormous, and meaningless outside the model
that produced them.

Written to a temporary file and streamed, rather than built in memory: a year
of transcripts is the one table with no bound on its size, and an endpoint that
holds the whole export in RAM to hand it over is an endpoint that fails on
exactly the install that most wants it.
"""

from __future__ import annotations

import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import __version__, db
from .settings import config_path, data_dir

log = logging.getLogger(__name__)

# Sections of the config that must never leave the machine in an export.
# `connectors` holds account details whose values are credentials in their own
# right -- an iCal "secret address" grants read access to a whole calendar to
# anyone holding the URL.
REDACTED_CONFIG_SECTIONS = ("connectors",)


def _rows(sql: str) -> list[dict[str, Any]]:
    return [dict(row) for row in db.query(sql)]


def _json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n").encode()


def _memory() -> list[dict[str, Any]]:
    return _rows("SELECT id, text, category, created_at, last_used_at FROM memory_facts")


def _tasks() -> list[dict[str, Any]]:
    return _rows(
        "SELECT id, text, kind, tags, due, done, created_at, completed_at "
        "FROM tasks ORDER BY created_at"
    )


def _reminders() -> list[dict[str, Any]]:
    return _rows(
        "SELECT id, kind, label, payload, next_fire_at, recurrence, created_at, "
        "last_fired_at, active FROM scheduled_items ORDER BY created_at"
    )


def _conversations() -> list[dict[str, Any]]:
    return _rows(
        "SELECT id, session_id, role, text, ts FROM conversation_turns ORDER BY ts, rowid"
    )


def _actions() -> list[dict[str, Any]]:
    # `params` carries the arguments an action ran with -- a subject line, a
    # list of paths. That is exactly the record worth having, and exactly why
    # the retention window exists; it belongs in an export of your own data.
    return _rows(
        "SELECT id, batch_id, skill_name, params, severity, preview, status, "
        "result, error, created_at, executed_at FROM action_records ORDER BY created_at"
    )


def _documents() -> list[dict[str, Any]]:
    """What was indexed. Paths and counts, never the extracted text."""
    return _rows(
        "SELECT path, title, size, chunk_count, indexed_at, error "
        "FROM indexed_documents ORDER BY path"
    )


def _transcripts() -> list[dict[str, Any]]:
    return _rows(
        "SELECT id, label, started_at, ended_at, duration_seconds, text, summary "
        "FROM transcripts ORDER BY started_at"
    )


def _transcript_markdown(row: dict[str, Any]) -> str:
    """One meeting, readable without this app.

    Markdown rather than JSON for these alone: a transcript is prose someone
    may want to read, and the summary, decisions and action items are a
    document rather than a record.
    """
    lines = [f"# {row.get('label') or 'Meeting'}", ""]
    started = row.get("started_at")
    if started:
        lines += [f"Recorded {started}.", ""]

    summary = db.loads(row.get("summary"), None)
    if isinstance(summary, dict):
        if summary.get("summary"):
            lines += ["## Summary", "", str(summary["summary"]), ""]
        for heading, key in (("Decisions", "decisions"), ("Action items", "actions")):
            entries = summary.get(key) or []
            if entries:
                lines += [f"## {heading}", ""]
                lines += [f"- {entry}" for entry in entries]
                lines.append("")

    lines += ["## Transcript", "", str(row.get("text") or "")]
    return "\n".join(lines).rstrip() + "\n"


def _config() -> str:
    """The settings, with the sections that carry credentials removed.

    Removed rather than emptied: `connectors: {}` leaves a reader wondering
    what was in it, and leaves a future field able to slip through because the
    section is still being copied.
    """
    path = config_path()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return f"# the config file could not be read when this export was made: {exc}\n"

    if not isinstance(raw, dict):
        return "# the config file held nothing readable when this export was made\n"

    for section in REDACTED_CONFIG_SECTIONS:
        raw.pop(section, None)
    header = (
        "# Exported from Kai. The connectors section is deliberately absent:\n"
        "# account details are credentials in their own right, and passwords\n"
        "# live in the OS credential store, never here.\n"
    )
    return header + yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)


def _readme(counts: dict[str, int], stamp: str) -> str:
    lines = [
        "# Your Kai data",
        "",
        f"Exported {stamp} by Kai {__version__}.",
        "",
        "Everything the assistant had stored about you, in formats that open",
        "without it. JSON is UTF-8; the Markdown files are plain text.",
        "",
        "| File | What it is | Entries |",
        "| --- | --- | --- |",
        f"| memory.json | Facts you asked to be remembered | {counts['memory']} |",
        f"| tasks.json | Tasks and notes | {counts['tasks']} |",
        "| tasks.md | The same, as the Markdown mirror | — |",
        f"| reminders.json | Reminders, routines and shortcuts | {counts['reminders']} |",
        f"| conversations.json | What was said, per session | {counts['conversations']} |",
        f"| actions.json | Every side effect, with its outcome | {counts['actions']} |",
        f"| transcripts/ | One file per recorded meeting | {counts['transcripts']} |",
        f"| documents.json | Which files were indexed | {counts['documents']} |",
        "| config.yaml | Your settings, connectors removed | — |",
        "",
        "## What is not here, on purpose",
        "",
        "- **Passwords and account details.** They live in the Windows",
        "  Credential Manager, not in Kai, and nothing here reads them. A",
        "  calendar's secret iCal address is a credential too, so the whole",
        "  connectors section is left out of config.yaml.",
        "- **The text of your documents.** documents.json lists what was",
        "  indexed -- paths and counts. The text is a copy of your own files,",
        "  which you already have.",
        "- **Search vectors.** Derived data, large, and meaningless outside",
        "  the model that produced them.",
        "",
        "Deleting this export deletes nothing in Kai. To remove the data from",
        "the app itself, use Delete everything in Settings.",
        "",
    ]
    return "\n".join(lines)


def write_export(target: Path) -> dict[str, Any]:
    """Build the zip at `target`. Returns what went into it."""
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    memory = _memory()
    tasks = _tasks()
    reminders = _reminders()
    conversations = _conversations()
    actions = _actions()
    documents = _documents()
    transcripts = _transcripts()

    counts = {
        "memory": len(memory),
        "tasks": len(tasks),
        "reminders": len(reminders),
        "conversations": len(conversations),
        "actions": len(actions),
        "documents": len(documents),
        "transcripts": len(transcripts),
    }

    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.md", _readme(counts, stamp))
        archive.writestr("memory.json", _json(memory))
        archive.writestr("tasks.json", _json(tasks))
        archive.writestr("reminders.json", _json(reminders))
        archive.writestr("conversations.json", _json(conversations))
        archive.writestr("actions.json", _json(actions))
        archive.writestr("documents.json", _json(documents))
        archive.writestr("config.yaml", _config())

        mirror = data_dir() / "tasks.md"
        if mirror.exists():
            try:
                archive.writestr("tasks.md", mirror.read_text(encoding="utf-8"))
            except OSError as exc:
                log.warning("could not include the task mirror: %s", exc)

        for row in transcripts:
            name = _safe_name(str(row.get("label") or "meeting"), str(row.get("id")))
            archive.writestr(f"transcripts/{name}.md", _transcript_markdown(row))

    return {"created_at": stamp, "bytes": target.stat().st_size, "counts": counts}


def _safe_name(label: str, identifier: str) -> str:
    """A filename from a label the user chose.

    A meeting called "Q3 / planning" must not become a directory, and two
    called the same thing must not overwrite each other -- hence the id.
    """
    cleaned = "".join(
        character if character.isalnum() or character in " -_" else "-"
        for character in label
    ).strip()
    cleaned = " ".join(cleaned.split())[:60] or "meeting"
    return f"{cleaned}-{identifier[:8]}"


def suggested_filename() -> str:
    return f"kai-export-{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}.zip"


def describe() -> dict[str, Any]:
    """What an export would contain, without building one.

    So the button can say "4 facts, 112 turns, 3 meetings" before it is
    pressed, rather than offering a download of an unknown thing.
    """
    return {
        "counts": {
            "memory": len(_memory()),
            "tasks": len(_tasks()),
            "reminders": len(_reminders()),
            "conversations": len(_conversations()),
            "actions": len(_actions()),
            "documents": len(_documents()),
            "transcripts": len(_transcripts()),
        },
        "excludes": [
            "passwords and account details",
            "the text of your documents",
            "search vectors",
        ],
    }

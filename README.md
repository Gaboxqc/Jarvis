# Kai — personal desktop assistant

A conversational assistant that runs on your own machine, remembers what matters,
and can actually change things on your PC — without ever doing so behind your back.

Built from the SDD in `assistant/` (`requirements.md`, `design.md`, `tasks.md`).
Requirement IDs (`REQ-N`) are referenced throughout the source.

## What works today

Phases 0–6 of `tasks.md`, text-first:

| Area | Capability |
|---|---|
| Conversation | Persona-driven replies, rolling session memory, idle reset |
| Memory | Durable facts you approve, reviewable and individually deletable |
| Planning | Reminders, timers, recurrence, missed-reminder replay, tasks/notes |
| Knowledge | Web search with sources, exact arithmetic, units, currency, time zones |
| Documents | Q&A over your own PDFs, Word files and notes, with file + page citations |
| Files | Search by name, date or content; safe folder organization, full batch undo |
| System | Launch/close apps, volume, lock, sleep, focus sessions |
| Trust | Action Gate, action history, undo, pre-approvals, one-shot data wipe |

Not built yet: voice in/out (Phase 7), calendar and mail connectors (Phase 8),
screen/clipboard assistance (T6.6), meeting capture (Phase 9), Tauri UI
(Phase 10), installer (Phase 11).

**Document search runs on SQLite FTS5**, which ships with Python — no vector
database, no embedding model download, and it works on a fresh install. Semantic
retrieval can be layered in behind `backend/app/index/store.py` later without
touching anything above it.

## The one design rule worth knowing

Nothing performs a side effect except through the **Action Gate**
(`backend/app/actions/gate.py`). It classifies every action as routine or
consequential, and for consequential ones it shows you exactly what will happen —
with real names and counts — then waits.

It is deliberately quiet. Only three things ask: closing an app (unsaved work),
locking or sleeping the machine, and organizing a folder (bulk file moves).
Everything else — storing a memory, setting a reminder, changing volume,
searching, calculating — just runs and tells you what it did, and stays
undoable. Severity is decided per call, so `system.control` asks about *sleep*
and not about *volume*. Prompting on everything trains people to click yes
without reading, which is worse than not asking.

Approval is bound to a single action id. Confirming one action never authorizes
another, even an identical one a second later. `gate.confirm()` takes an id, not
a "yes", so there is no call shape that could approve something you were not
shown. That invariant is pinned by tests in `backend/tests/test_action_gate.py`.

Everything executed is journalled, and anything reversible can be undone —
one folder-organize run undoes as one operation, not 47.

## Running it

Requires [Ollama](https://ollama.com) with a model pulled:

```bash
ollama pull llama3
```

Set up and start the terminal client:

```bash
python -m venv .venv && .venv/Scripts/python.exe -m pip install -r backend/requirements.txt
```

```bash
cd backend && ../.venv/Scripts/python.exe -m app.cli
```

Or run the API instead:

```bash
cd backend && ../.venv/Scripts/python.exe -m uvicorn app.main:app --port 8756
```

Tests:

```bash
cd backend && ../.venv/Scripts/python.exe -m pytest
```

## Configuring it

Everything lives in `kai.config.yaml`, re-read on the next turn — no restart:

- **persona** — name, tone, verbosity, language, idle timeout
- **privacy** — per-feature switches for anything that leaves the machine
- **actions.pre_approved** — skills allowed to skip the confirmation prompt
- **documents.indexed_folders** — folders searchable by content; empty disables it
- **system.allowed_roots** — the only folders file skills may touch, ever
- **skills.disabled** — turn individual capabilities off

A skill whose privacy switch is off is withdrawn from the router entirely, so the
assistant never proposes something it is not permitted to do.

## CLI commands

`/skills` `/memory` `/history` `/pending` `/reminders` `/docs` `/reindex` `/focus`
`/undo` `/health` `/wipe` `/quit`

These work whether or not the model is reachable.

## Adding a capability

Drop a `Skill` subclass anywhere under `backend/app/skills/`. The registry finds
it at startup and the router picks it up — no router changes. Declare
`consequential = True` if it needs confirmation (then you must implement
`preview()`), and `reversible = True` if it can undo itself (then you must
implement `undo()`). The registry enforces both at import time.

## Where your data lives

`%LOCALAPPDATA%\Kai\kai.db`, plus a plain-Markdown mirror of your tasks. Nothing
else, nowhere else. `/wipe` (or `POST /privacy/wipe`) removes all of it.

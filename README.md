# Kai — personal desktop assistant

A conversational assistant that runs on your own machine, remembers what matters,
and can actually change things on your PC — without ever doing so behind your back.

Built from the SDD in `assistant/` (`requirements.md`, `design.md`, `tasks.md`).
Requirement IDs (`REQ-N`) are referenced throughout the source.

## What works today

All eleven phases of `tasks.md`:

| Area | Capability |
|---|---|
| Conversation | Persona-driven replies, rolling session memory, idle reset |
| Memory | Durable facts you approve, reviewable and individually deletable |
| Planning | Reminders, timers, recurrence, missed-reminder replay, tasks/notes |
| Knowledge | Web search with sources, exact arithmetic, units, currency, time zones |
| Documents | Q&A over your own PDFs, Word files and notes, with file + page citations |
| Files | Search by name, date or content; safe folder organization, full batch undo |
| System | Launch/close apps, volume, lock, sleep, focus sessions |
| Voice | Local speech in and out, wake word, confidence-gated so it asks rather than guesses |
| Calendar | Agenda, free slots, create/cancel events (ICS or CalDAV) |
| Mail | Unread triage split by what needs a reply, search, draft, send |
| Briefing | One catch-up covering calendar, reminders, tasks and mail |
| Meetings | Record and transcribe locally, then summary, decisions and action items |
| Screen | Explain, translate or rewrite what you've copied or what's on screen |
| Trust | Action Gate, action history, undo, pre-approvals, one-shot data wipe |
| Desktop UI | Chat with inline confirmations, memory review, action history, privacy screen |

## Building the installer

```bash
powershell -File installer/build.ps1
```

Freezes the backend with PyInstaller, checks the result, then bundles it with
the UI into an MSI and an NSIS installer.

The check between those steps is not ceremony. Skills are discovered at runtime,
so freezing can drop every one of them while leaving an app that starts, serves
and answers questions — broken in the only way that matters and indistinguishable
from working. That happened on the first build: 1 skill out of 48. The build now
runs `kai-backend.exe --selftest` and refuses to package anything that fails it.

**Ollama is the one prerequisite that isn't bundled** — it's a separate installer
and gigabytes of model. The app detects it and says so, with the command to fix
it, rather than reporting a generic failure.

## The desktop UI

```bash
cd backend && ../.venv/Scripts/python.exe -m uvicorn app.main:app --port 8756
```

```bash
cd ui && npm install && npm run dev
```

Then open http://127.0.0.1:5173. Four sections — chat, memory, history,
settings — switchable with Ctrl+1…4.

For the native window instead (tray icon, and Ctrl+Alt+K to summon it without
changing the focused window):

```bash
cd ui && npm run tauri dev
```

The UI talks to the same API the CLI drives, so it cannot acquire capabilities
the terminal lacks, and the Action Gate sits in front of exactly the same
actions. Confirmations appear inline: **Go ahead** sends the action id the
backend issued, never a bare yes.

## Screen and clipboard

`/clip` reads what you've copied; `/screen` reads the active window (`/screen
full` for the whole display). Or just ask — "what does this mean, I just copied
it" routes to the clipboard on its own.

Reading the clipboard is instant and needs no dependency. Reading the *screen*
runs local OCR and takes several seconds, which is the cost of not sending your
display to a cloud vision API. Both happen only when asked — there is no
watcher, no polling — the capture is announced in the reply, and the image is
never written anywhere.

## Recording a meeting

`/record standup` starts, `/record stop` summarises. Transcription is local —
audio never leaves the machine, and only the text is stored. `/meetings` lists
what has been recorded; each is individually deletable.

**It records your microphone only.** Capturing the other participants on a video
call was built and removed: `soundcard` can do WASAPI loopback, but it binds its
COM apartment to the importing thread and any prior `sounddevice` use in the
process — a single `/listen` is enough — makes it terminate the interpreter with
no traceback. Subprocess isolation didn't work either. The findings are written
up in `backend/app/capture/recorder.py` so it isn't re-attempted blind. For an
in-person meeting or dictation it's complete; for a remote call it captures your
side, and it says so before recording rather than after.

## Connecting an account

Connectors are optional — everything above works without them. Add the account
to `kai.config.yaml`, then:

```bash
cd backend && ../.venv/Scripts/python.exe -m app.cli
```

Run `/connect mail gmail`. **You type the password at the prompt**; it goes
straight into Windows Credential Manager and is never written to the config
file, never logged, and never echoed. `/accounts` shows what is connected.

Calendars work read-only from any iCal URL (Google: Settings → your calendar →
"Secret address in iCal format"), or two-way over CalDAV. Mail is plain IMAP —
with 2FA you need an app password, not your normal one.

## Voice

Off by default. Turn it on in `kai.config.yaml`, then download the models
(~210MB, one explicit command — nothing is fetched silently):

```bash
cd backend && ../.venv/Scripts/python.exe -m app.cli
```

Then `/voice setup`, and `/listen` to talk. `/speak hello` tests the voice.

Everything runs locally — faster-whisper for recognition, Piper for speech.
Audio never leaves the machine. Wake word is opt-in on top of voice, because it
means an always-open microphone; without it, use `/listen` as push-to-talk.

Models live in `%LOCALAPPDATA%\Kai\models` and are removable with
`DELETE /voice/models`.

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

Copy the template, then edit your copy:

```bash
cp kai.config.example.yaml kai.config.yaml
```

`kai.config.yaml` is gitignored and must stay that way. It holds no passwords —
those go in the OS credential store via `/connect` — but some connector values
are credentials in their own right. A calendar's "secret address in iCal format"
grants read access to anyone holding the URL, with no login.

Settings, re-read on the next turn — no restart:

- **persona** — name, tone, verbosity, language, idle timeout
- **privacy** — per-feature switches for anything that leaves the machine
- **actions.pre_approved** — skills allowed to skip the confirmation prompt
- **documents.indexed_folders** — folders searchable by content; empty disables it
- **system.allowed_roots** — the only folders file skills may touch, ever
- **skills.disabled** — turn individual capabilities off

A skill whose privacy switch is off is withdrawn from the router entirely, so the
assistant never proposes something it is not permitted to do.

## CLI commands

`/skills` `/memory` `/history` `/pending` `/reminders` `/docs` `/reindex`
`/brief` `/accounts` `/connect` `/voice` `/listen` `/speak` `/record` `/meetings`
`/clip` `/screen` `/focus` `/undo` `/health` `/wipe` `/quit`

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

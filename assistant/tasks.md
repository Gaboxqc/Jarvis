# Implementation Tasks — Project "Kai" (Personal Desktop Assistant)

Each task references the requirement(s) it satisfies (`REQ-N`) and the design component it implements, per `design.md` §8. Work top to bottom — later phases assume earlier ones are functional.

**Sequencing principle:** get to a *usable text assistant with a trustworthy action layer* as early as possible. Voice, connectors, and packaging are amplifiers on top of something that already earns its place. In particular, the Action Gate (Phase 3) lands **before** any skill that can move a file or write to a calendar — retrofitting a confirmation layer onto skills that already bypass it is the single most expensive mistake available here.

**Status.** `x` shipped, `~` shipped in a different shape than described with the
reason on the line, and an empty box means not built. Every state was set by
reading the code rather than by trusting the README; where the two disagree,
this file is the one that was checked.

The `~` rows are decisions, not backlog. Four of them are the interesting
reading in this document: FTS5 instead of embeddings, a thread instead of
APScheduler, polling instead of a filesystem watcher, and a microphone instead
of WASAPI loopback. Each has its reasoning in the source beside the code.

## Phase 0 — Project setup

- [x] **T0.1** Scaffold repo per `design.md` §7 (backend/, ui/, kai.config.yaml, installer/). — *Requirements: REQ-30*
- [x] **T0.2** FastAPI backend skeleton with a `/turn` endpoint that echoes input. — *Design: Brain / Orchestrator*
- [x] **T0.3** Tauri + React skeleton: tray icon, global hotkey that opens a chat window without stealing focus from the active app, keyboard-only operable. — *Requirements: REQ-1, REQ-28 · Design: Input Router, UI*

## Phase 1 — Text brain, persona, memory

- [x] **T1.1** Define `PersonaConfig` schema and loader from `kai.config.yaml`; hot-reload on change. — *Requirements: REQ-5*
- [x] **T1.2** Integrate Ollama; build system-prompt construction from `PersonaConfig`, defaulting to the terse "answer first" style. — *Requirements: REQ-5*
- [x] **T1.3** Implement short-term memory (rolling window + idle-timeout reset) and reference resolution against the last skill result. — *Requirements: REQ-6*
- [x] **T1.4** Implement `MemoryFact` store: detect durable facts, offer to remember, write on confirm, apply on retrieval. — *Requirements: REQ-7*
- [x] **T1.5** Build the memory review UI (list, edit, delete individually, delete all). — *Requirements: REQ-7, REQ-26*
- [x] **T1.6** Wire persona + both memory tiers into `/turn`; validate multi-turn quality from the chat window. — *Requirements: REQ-5, REQ-6, REQ-7*

## Phase 2 — Skill registry and knowledge skills

- [x] **T2.1** Implement the Skill Registry: skill interface (name, description, JSON-schema params, handler, `consequential` flag, `reversible` flag), startup discovery, per-skill enable/disable in settings. — *Requirements: REQ-33*
- [x] **T2.2** Implement skill routing in the Brain (function-calling model via Ollama, or Groq/Gemini free tier) — the router reads the registry and is never edited per skill. — *Requirements: REQ-33, REQ-15*
- [x] **T2.3** Implement `web_search` skill with timeout, source capture, and error handling. — *Requirements: REQ-15, REQ-27*
- [~] **T2.4** Implement `utilities` skills: unit/currency conversion, date-time math, time zones, calculation, definitions, translation — with as-of timestamps on live-data lookups. — *Requirements: REQ-18*
  - Calculation, units, currency and time zones shipped. Definitions and standalone translation did not: the model answers both directly, and a skill that wraps a prompt is a skill that can only be worse at it. Translation of text you have in front of you is `screen.read` / `screen.copy`.
- [x] **T2.5** Add uniform graceful-failure messaging for every skill call, per `design.md` §5; assert the brain never substitutes a guess for a failed call. — *Requirements: REQ-27*

## Phase 3 — Action Gate and undo (do this before Phase 4)

- [x] **T3.1** Implement the Action Gate: severity classification from the skill's `consequential` flag, human-readable preview generation naming targets and counts, confirmation round-trip through the UI. — *Requirements: REQ-24*
- [x] **T3.2** Enforce single-action scoping — approval never carries to the next action; add tests that specifically try to make it leak. — *Requirements: REQ-24*
- [x] **T3.3** Implement pre-approved action types: settings screen to grant, list, and revoke. — *Requirements: REQ-24*
- [x] **T3.4** Implement the Undo Journal: `ActionRecord` with `batch_id` grouping and inverse-operation payloads. — *Requirements: REQ-25*
- [x] **T3.5** Build the action history UI with per-action and per-batch undo; surface `reversible: false` at the confirmation step, not after. — *Requirements: REQ-25*

## Phase 4 — System skills

- [x] **T4.1** Implement `file_search` (name, type, date; content once Phase 6 lands) returning ranked candidates with distinguishing detail. — *Requirements: REQ-20*
- [x] **T4.2** Implement `organize_files`: preview-then-confirm, sort by type/date, moves only, deletions routed to recycle bin via `send2trash`, whole run undoable as one batch. — *Requirements: REQ-21, REQ-24, REQ-25*
- [x] **T4.3** Implement `launch_app` / `close_app`, with closest-match suggestion on miss and confirmation when closing an app with unsaved state. — *Requirements: REQ-22, REQ-24, REQ-27*
- [~] **T4.4** Implement system control: volume, audio output device, lock, sleep, screenshot, Wi-Fi toggle. — *Requirements: REQ-22*
  - Volume, lock, sleep and list-running shipped. Wi-Fi toggle, audio output device and screenshot did not.
- [ ] **T4.5** Implement user-defined named shortcuts for multi-step sequences. — *Requirements: REQ-22*
  - Not built. Depends on T5.5, which is the same machinery.
- [x] **T4.6** Implement `focus_session`: close/minimize configured apps, suppress notifications and the assistant's own proactive output, interval (work/break) patterns, early exit, end-of-session notification and restore. — *Requirements: REQ-23*

## Phase 5 — Scheduling, tasks, routines

- [~] **T5.1** Integrate APScheduler with a persistent jobstore. — *Requirements: REQ-9*
  - Not APScheduler. A thread and a SQLite table, because persistence was the only thing APScheduler was wanted for and the table already existed -- see scheduler/service.py.
- [x] **T5.2** Implement reminders/timers/alarms with natural-language time parsing, resolved-time confirmation, recurrence, list/edit/cancel. — *Requirements: REQ-9*
- [x] **T5.3** Deliver due items as desktop notifications (plus speech once Phase 7 lands); replay missed items on next launch stating their original due time. — *Requirements: REQ-9*
- [x] **T5.4** Implement the task/note store with tags, search, complete, delete — SQLite plus a mirrored plain-Markdown file. — *Requirements: REQ-10*
- [ ] **T5.5** Implement routines: trigger + action list, approval of consequential actions at creation time, re-prompt on edit, list/disable/delete, outcomes written to action history. — *Requirements: REQ-12, REQ-24, REQ-25*
  - **Not built.** `KIND_ROUTINE` is reserved in scheduler/store.py and filtered out of reminder listings; nothing creates one. The largest unbuilt requirement, and the design work for the hard part -- approving a routine's consequential actions at creation time -- is already done.

## Phase 6 — Document index and screen context

- [~] **T6.1** Implement text extraction for PDF, DOCX, TXT, MD; chunking; embedding via Ollama `nomic-embed-text`; storage in SQLite FTS5 + `sqlite-vec`. — *Requirements: REQ-16*
  - SQLite FTS5, no embeddings and no sqlite-vec. Retrieval works on a fresh install with no model download; the trade is that search is lexical, so a question that shares no words with the document finds nothing. Semantic retrieval layers in behind index/search.py without touching anything above it.
- [~] **T6.2** Implement `watchdog`-based incremental re-indexing at low process priority; pause during focus sessions and on battery. — *Requirements: REQ-16, REQ-31*
  - Polled rather than watchdog-driven: size+mtime per file makes a rescan cheap, and a filesystem watcher over a Documents folder is a second thing to keep alive. Pausing on battery and during focus sessions shipped.
- [x] **T6.3** Implement the `documents` skill: retrieve, answer, and cite source file + page/section. — *Requirements: REQ-16*
- [x] **T6.4** Build indexed-folder settings: add/remove folders, show what's indexed, clear the index. — *Requirements: REQ-16, REQ-26*
- [x] **T6.5** Extend `file_search` with content matching from the index. — *Requirements: REQ-20*
- [x] **T6.6** Implement screen/clipboard assistance: explicit invocation only, visible capture indicator, discard after the turn unless saved. — *Requirements: REQ-17, REQ-26*

## Phase 7 — Voice in and out

- [x] **T7.1** Integrate faster-whisper as a local STT service; expose `/transcribe`. — *Requirements: REQ-3, REQ-26*
- [x] **T7.2** Integrate Piper with bundled selectable voices; expose `/speak`; mute toggle that disables nothing else. — *Requirements: REQ-4*
- [x] **T7.3** Connect STT → Brain → TTS into one round-trip voice loop on push-to-talk. — *Requirements: REQ-1, REQ-3, REQ-4*
- [x] **T7.4** Add low-confidence STT handling ("ask the user to repeat"). — *Requirements: REQ-3*
- [x] **T7.5** Integrate openWakeWord + Silero VAD for hands-free activation; configurable wake phrase; fully disableable; assert no audio is transcribed or transmitted pre-wake. — *Requirements: REQ-2, REQ-26*
- [x] **T7.6** Keep push-to-talk as the automatic fallback if the wake word engine fails to load. — *Requirements: REQ-2*
- [x] **T7.7** *(Optional)* Add Coqui XTTS-v2 voice cloning from a user-supplied clip, model kept local. — *Requirements: REQ-4, REQ-26*

## Phase 8 — Connectors: calendar, mail, briefing

- [x] **T8.1** Implement the credential layer over `keyring`; config stores references only, never secrets. — *Requirements: REQ-26*
- [x] **T8.2** Implement the calendar connector (CalDAV / Google Calendar / local ICS) with read scope: "what's next", "am I free Thursday". — *Requirements: REQ-8*
- [x] **T8.3** Add calendar write (create/move/cancel) behind the Action Gate, confirming the resolved date/time/title. — *Requirements: REQ-8, REQ-24*
- [x] **T8.4** Implement the mail connector (IMAP) and the triage skill: unread grouped by needs-reply, senders and subjects named, thread summarization. — *Requirements: REQ-13*
- [x] **T8.5** Add mail actions (mark read, archive, flag) behind the Action Gate. — *Requirements: REQ-13, REQ-24*
- [x] **T8.6** Implement drafting and conversational iteration on a draft; sending requires explicit per-message confirmation and never inherits an earlier approval. — *Requirements: REQ-14, REQ-24*
- [x] **T8.7** Implement the daily briefing: parallel fan-out with per-source timeouts, configurable sections and order, failed sources reported as a line rather than dropped. — *Requirements: REQ-11, REQ-27*
- [x] **T8.8** Add connector-not-configured and auth-expired handling: explain, offer setup/re-auth, keep everything local working. — *Requirements: REQ-8, REQ-13, REQ-27*

## Phase 9 — Meeting capture

- [~] **T9.1** Implement the capture service: mic + WASAPI loopback, chunked local transcription, explicit start/stop, persistent recording indicator. — *Requirements: REQ-19, REQ-26*
  - Microphone only. WASAPI loopback was built and removed: `soundcard` binds its COM apartment to the importing thread, and any prior `sounddevice` use in the process terminates the interpreter with no traceback. Subprocess isolation did not help either. Written up in capture/recorder.py so it is not re-attempted blind, and the app says so before recording rather than after.
- [x] **T9.2** Summarize a finished session into summary, decisions, and action items; offer to save action items as tasks. — *Requirements: REQ-19, REQ-10*
- [x] **T9.3** Store transcripts locally with per-transcript delete. — *Requirements: REQ-19, REQ-26*

## Phase 10 — Presence, accessibility, footprint

- [x] **T10.1** Implement the default state indicator (idle / listening / thinking / speaking) in the tray and chat window. — *Requirements: REQ-32*
- [x] **T10.2** Confirm the presence layer consumes only state + audio + optional emotion tag, with no coupling to brain or skill internals. — *Requirements: REQ-32*
- [x] **T10.3** *(Optional)* Add the animated character overlay as an off-by-default setting, driven by audio RMS and the emotion tag. — *Requirements: REQ-32*
- [x] **T10.4** Accessibility pass: full keyboard operation, full text-only operation with audio disabled, OS text scaling and high-contrast support. — *Requirements: REQ-28*
- [x] **T10.5** Add English + Spanish across conversation, STT, and TTS, selectable in settings. — *Requirements: REQ-28*
- [x] **T10.6** Measure and tune idle CPU/RAM; lazy-load and unload heavy models; verify indexing yields under load. — *Requirements: REQ-31*
- [x] **T10.7** Build the privacy settings screen: per-feature egress toggles, cloud-LLM disclosure, and the single "delete all local data" action. — *Requirements: REQ-26*

## Phase 11 — Packaging and installer

- [x] **T11.1** Bundle the FastAPI backend as a PyInstaller sidecar binary. — *Requirements: REQ-29, REQ-30*
- [x] **T11.2** Configure the Tauri bundler to produce a single installer including the sidecar, default config, and bundled voices. — *Requirements: REQ-29*
- [x] **T11.3** Build first-run setup: language, voice on/off, hotkey, optional connectors — fully skippable, and the app is useful if skipped. — *Requirements: REQ-29*
- [x] **T11.4** Verify a clean install on a machine with no Python/Ollama; document any unbundlable prerequisite clearly in-app. — *Requirements: REQ-29, REQ-30*
- [ ] **T11.5** Verify free-tier cap handling end to end: force a cap, confirm fallback to the local model and that the user is told what changed. — *Requirements: REQ-27, REQ-30*
  - Nothing to test yet: `allow_cloud_llm` is off and there is no cloud path. It becomes real the first time one is added.
- [ ] **T11.6** End-to-end regression: install → briefing → ask a web question → ask a document question → set a reminder → organize a folder → undo it → draft an email and decline to send → focus session → all with voice on and again with voice off. — *Requirements: REQ-1 through REQ-33 (full regression)*
  - A manual pass, and not one anything here can claim on your behalf.

---

## Traceability check

Every REQ-1 through REQ-33 appears in at least one task above; every task cites at least one REQ. If a future requirement is added to `requirements.md`, add its matching row to `design.md` §8 first, then a task here — that keeps the chain requirement → design → task unbroken in both directions.

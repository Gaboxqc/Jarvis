# Design Document — Project "Kai" (Personal Desktop Assistant)

References requirement IDs from `requirements.md` (REQ-N) throughout for traceability.

## 1. Architecture overview

Kai is a set of independently swappable services orchestrated by one FastAPI process (the "Brain service"), with a Tauri + React front end for the chat window, tray, and state indicator.

```
        ┌─ hotkey / text input ─┐
Mic → Wake Word/VAD → STT ──────┴──→ Brain (LLM + skill router)
                                        │
                        ┌───────────────┼────────────────┐
                        ▼               ▼                ▼
                 Context Providers   Skill Registry   Action Gate ──→ Executor
                  · persona           · knowledge      · classify
                  · short-term mem    · planning       · confirm (REQ-24)
                  · long-term mem     · comms          · journal  (REQ-25)
                  · doc index         · system
                        │
                        ▼
                  Reply ──→ TTS (local) ──→ Presence Layer (state / audio / emotion)
                        └──→ Chat transcript
```

Three ideas hold the design together:

1. **One skill router, many skills.** Every capability — calendar, reminders, search, documents, file ops, system control — is registered as a skill with a schema. The brain's only job per turn is: answer directly, or pick skills and then answer. Adding a capability means registering a skill, not editing the router (REQ-33).
2. **Every side effect passes through the Action Gate.** Nothing writes to the calendar, the mailbox, or the filesystem without going through one component that classifies the action, asks for confirmation when it's consequential, and journals it for undo (REQ-24, REQ-25). This is the difference between an assistant people trust and one they turn off.
3. **The Presence Layer consumes only state + audio + an optional emotion tag.** It knows nothing about the LLM, skills, or actions — so it can be a 40px tray dot or a full animated character without touching anything else (REQ-32).

## 2. Components

| Component | Responsibility | Technology (free) | Requirements covered |
|---|---|---|---|
| Input Router | Hotkey, text box, voice — one entry point | Tauri global shortcut + FastAPI `/turn` | REQ-1, REQ-28 |
| Wake Word & VAD Listener | Detects wake phrase, trims silence | openWakeWord + Silero VAD | REQ-2, REQ-26 |
| STT Service | Local speech-to-text | faster-whisper (base/small, int8) | REQ-3, REQ-19, REQ-26 |
| Brain / Orchestrator | Persona, context assembly, skill routing, reply synthesis | FastAPI + Ollama (local) or Groq/Gemini free tier | REQ-5, REQ-6, REQ-15, REQ-27, REQ-33 |
| Persona Config | Human-editable personality + style | YAML config + settings UI | REQ-5, REQ-28 |
| Short-term Memory | Rolling conversation window, reference resolution | In-process ring buffer + SQLite | REQ-6 |
| Long-term Memory | Durable user facts and preferences | SQLite table + review/edit UI | REQ-7, REQ-26 |
| Document Index | Local text extraction, chunking, embedding, retrieval | `watchdog` + pypdf/python-docx + Ollama `nomic-embed-text` + SQLite FTS5 & `sqlite-vec` | REQ-16, REQ-20, REQ-26, REQ-31 |
| Scheduler | Reminders, alarms, routines, briefing trigger | APScheduler + SQLAlchemy jobstore (survives restart) | REQ-9, REQ-11, REQ-12 |
| Connector Layer | Calendar and mail access, credential handling | CalDAV / Google Calendar API / local ICS; IMAP+SMTP; `keyring` for secrets | REQ-8, REQ-13, REQ-14, REQ-26 |
| **Skill Registry** | Declares every capability the brain can invoke | Python entry-point plugins, JSON-schema per skill | REQ-33 |
| ↳ Knowledge skills | web search, document Q&A, screen/clipboard, utilities | duckduckgo-search / Tavily; `mss` + RapidOCR; `pint`, `babel`, exchange-rate API | REQ-15, REQ-16, REQ-17, REQ-18 |
| ↳ Planning skills | calendar, reminders, tasks/notes, briefing, routines | Connector Layer + Scheduler + local task store | REQ-8–REQ-12 |
| ↳ Comms skills | triage, summarize thread, draft, send | Connector Layer | REQ-13, REQ-14 |
| ↳ System skills | file search, organize, app/system control, focus | `psutil`, `subprocess`, `pywin32`, `send2trash`, Everything SDK (optional) | REQ-20–REQ-23 |
| **Action Gate** | Classify routine vs. consequential, confirm, journal | Middleware in front of the Executor | REQ-24, REQ-25 |
| Action Executor | Performs the approved action | Python action handlers | REQ-8–REQ-14, REQ-20–REQ-23 |
| Undo Journal | Inverse operations for reversible actions | SQLite journal + batch grouping | REQ-25 |
| Capture Service | Meeting/long-audio transcription session | faster-whisper streaming + WASAPI loopback | REQ-19, REQ-26 |
| TTS Service | Local speech output | Piper (bundled voices); optional Coqui XTTS-v2 for cloning | REQ-4, REQ-26 |
| Presence Layer | State indicator; optional character overlay | Tauri + React; audio-RMS driven when overlay enabled | REQ-32, REQ-31 |
| Settings & Privacy UI | Persona, connectors, indexed folders, memory review, data wipe | React settings screens | REQ-5, REQ-7, REQ-16, REQ-24, REQ-26 |
| Installer/Packaging | Single-step install + first-run setup | Tauri bundler + PyInstaller sidecar | REQ-29, REQ-30 |

## 3. Data models

**PersonaConfig** (REQ-5, REQ-28)
```
name: string
tone_description: string          # free-text, injected into system prompt
verbosity: "terse" | "normal" | "chatty"
address_style: string             # how it refers to the user
language: string                  # "en", "es"
voice_enabled: bool               # REQ-4
voice_id: string                  # bundled voice or cloned model id
idle_timeout_seconds: int         # REQ-6
```

**ConversationTurn** (REQ-6)
```
role: "user" | "assistant"
text: string
timestamp: datetime
skill_calls: SkillCall[]
emotion_tag: string | null        # REQ-32, optional presentation hint
```

**MemoryFact** (REQ-7)
```
id: uuid
text: string                      # "project folder is D:/work/clients"
category: "preference" | "fact" | "shortcut" | "person"
source_turn_id: uuid
created_at: datetime
last_used_at: datetime | null
```

**SkillCall** (REQ-33, REQ-27)
```
skill_name: string                # e.g. "calendar.create_event"
args: object
result: object | null
error: string | null              # REQ-27 — never silently swallowed
duration_ms: int
```

**ActionRequest** (REQ-24)
```
id: uuid
skill_name: string
params: object
severity: "routine" | "consequential"
reversible: bool                  # REQ-25 — surfaced at the confirmation step
preview: string                   # human-readable "here's exactly what I'll do"
```

**ActionRecord** (REQ-25)
```
id: uuid
batch_id: uuid                    # groups one organize-run into a single undo
request: ActionRequest
status: "confirmed" | "executed" | "failed" | "undone" | "declined"
undo_payload: object | null       # inverse operation, e.g. list of move-backs
executed_at: datetime
```

**ScheduledItem** (REQ-9, REQ-12)
```
id: uuid
kind: "reminder" | "timer" | "alarm" | "routine"
trigger: cron | interval | datetime
payload: object                   # message, or list of ActionRequests for routines
recurrence: string | null
pre_approved_actions: uuid[]      # REQ-12/REQ-24 — approved at creation time
last_fired_at: datetime | null
```

**TaskItem / NoteItem** (REQ-10)
```
id: uuid
text: string
tags: string[]
due: datetime | null
done: bool
created_at: datetime
```
Persisted to SQLite **and** mirrored to a plain Markdown file so the user's data is never trapped in the app.

**DocumentChunk** (REQ-16, REQ-20)
```
doc_path: string
page_or_section: string | null
text: string
embedding: vector
indexed_at: datetime
```

**ConnectorConfig** (REQ-8, REQ-13, REQ-26)
```
kind: "calendar" | "mail"
provider: "caldav" | "google" | "ics" | "imap"
account_label: string
credential_ref: string            # OS credential store key — never the secret itself
enabled: bool
scopes: string[]                  # read | write
```

## 4. Interaction flows

**4.1 Conversational turn** (REQ-1–REQ-7)
Input arrives (hotkey, text, or wake word → VAD → STT) → Brain assembles context: persona + rolling window + relevant long-term memories → Brain answers directly, or selects skills → reply is rendered as text and, if enabled, spoken → Presence Layer reflects state throughout.

**4.2 Knowledge turn** (REQ-15–REQ-18)
Brain decides the question needs grounding → routes to the right source: document index for "my" questions, web search for current/external ones, utilities for conversions and calculations, screen/clipboard when the user invoked screen assistance → result is synthesized into a natural answer with sources available → on failure, Brain says it couldn't retrieve it and does **not** answer from guesswork (REQ-27).

**4.3 Action turn** (REQ-20–REQ-24, REQ-25)
Brain builds an `ActionRequest` → Action Gate classifies severity → if consequential, Gate returns a preview naming targets and counts and waits for explicit confirmation → on confirmation, Executor runs it → Undo Journal records the inverse → Brain reports the outcome conversationally. Confirmation is scoped to that single request; it never carries forward.

**4.4 Scheduled / proactive turn** (REQ-9, REQ-11, REQ-12)
Scheduler fires → for a reminder, notify (desktop + optional speech); for a routine, run its actions — consequential ones only if they were pre-approved at creation time → record every outcome in the action history. Proactive output is suppressed during focus sessions (REQ-23) and delivered afterwards.

**4.5 Briefing** (REQ-11)
Fan out to calendar, scheduler, task store, and mail connector in parallel with per-source timeouts → assemble configured sections in configured order → any section whose source failed is replaced with a one-line "couldn't reach X", not dropped silently.

**4.6 Capture session** (REQ-19)
User starts capture → persistent recording indicator shown → mic and/or system loopback audio transcribed locally in chunks → on stop, summarize into decisions and action items → offer to save action items as tasks.

## 5. Error handling & fallbacks

| Scenario | Fallback behavior | Requirement |
|---|---|---|
| Wake word engine unavailable | Push-to-talk hotkey | REQ-2 |
| STT confidence very low | Ask user to repeat | REQ-3 |
| No microphone / audio disabled | Full capability via text + hotkey | REQ-1, REQ-28 |
| Web search fails or times out | Say it couldn't look that up; do not answer from guesswork | REQ-15, REQ-27 |
| Connector not configured | Explain and offer setup; never fail silently | REQ-8, REQ-13 |
| Connector auth expired | Prompt re-auth once; keep all local features working | REQ-26, REQ-27 |
| Cloud LLM free-tier cap hit | Fall back to local Ollama model and tell the user what changed | REQ-27, REQ-30 |
| Ambiguous date/time in a request | Resolve, then confirm the resolved value before writing | REQ-8, REQ-24 |
| Multiple files match a description | Present candidates; never open one arbitrarily | REQ-20 |
| Action partially fails mid-batch | Stop, report what completed, offer undo of the completed part | REQ-25 |
| Undo requested for irreversible action | Refuse and explain — the warning was already given pre-execution | REQ-25 |
| Document index out of date / file locked | Answer from what is indexed, note staleness, retry in background | REQ-16, REQ-31 |
| No network at all | Disable network-dependent skills only; conversation, memory, reminders, tasks, documents, file and system actions keep working | REQ-26, REQ-27 |

## 6. Security & privacy design

- **Secrets** live in the OS credential store via `keyring`; config files hold only opaque references (REQ-26).
- **Egress is explicit.** Settings lists every feature that leaves the machine — web search, live-data utilities, each connector, optional cloud LLM — each individually toggleable. Everything else is local by construction.
- **Capture is never ambient.** Mic audio is discarded pre-wake-word; screen capture requires explicit invocation and shows an indicator; meeting capture requires an explicit start and shows a persistent indicator (REQ-17, REQ-19, REQ-26).
- **Memory writes are visible.** Long-term memory is never written silently; each write is surfaced in the turn and reviewable afterwards (REQ-7).
- **One-action-one-confirmation.** The Action Gate never generalizes approval across actions, and pre-approvals are explicit, listed, and revocable (REQ-24).
- **Delete everything** is a single settings action covering transcripts, history, memories, and indexes (REQ-26).

## 7. Repository structure (proposed)

```
kai/
├── backend/                    # FastAPI Brain service
│   ├── app/
│   │   ├── main.py
│   │   ├── brain/              # context assembly, skill routing, reply synthesis
│   │   ├── persona/            # PersonaConfig loader
│   │   ├── memory/             # short_term.py, long_term.py
│   │   ├── index/              # extract.py, chunk.py, embed.py, retrieve.py
│   │   ├── scheduler/          # reminders, routines, briefing trigger
│   │   ├── connectors/         # calendar.py, mail.py, credentials.py
│   │   ├── skills/
│   │   │   ├── registry.py     # discovery + JSON schemas (REQ-33)
│   │   │   ├── knowledge/      # search.py, documents.py, screen.py, utilities.py
│   │   │   ├── planning/       # calendar.py, reminders.py, tasks.py, briefing.py
│   │   │   ├── comms/          # triage.py, draft.py
│   │   │   └── system/         # file_search.py, organize.py, app_control.py, focus.py
│   │   ├── actions/            # gate.py (classify+confirm), executor.py, undo.py
│   │   └── services/           # stt.py, tts.py, capture.py
│   └── requirements.txt
├── ui/                         # Tauri + React
│   ├── src/
│   │   ├── chat/               # transcript, input, confirmation prompts
│   │   ├── settings/           # persona, connectors, indexed folders, privacy
│   │   ├── memory/             # review/edit/delete stored facts (REQ-7)
│   │   ├── history/            # action history + undo (REQ-25)
│   │   └── presence/           # state indicator; optional overlay (REQ-32)
│   └── src-tauri/
├── kai.config.yaml             # persona + preferences
└── installer/                  # Tauri bundle config + PyInstaller spec
```

## 8. Traceability matrix (REQ → Design component)

| REQ | Component(s) |
|---|---|
| REQ-1 | Input Router, Brain, UI chat |
| REQ-2 | Wake Word & VAD Listener |
| REQ-3 | STT Service |
| REQ-4 | TTS Service, Persona Config |
| REQ-5 | Persona Config, Brain, Settings UI |
| REQ-6 | Short-term Memory, Brain |
| REQ-7 | Long-term Memory, Memory review UI |
| REQ-8 | Connector Layer (calendar), Planning skills, Action Gate |
| REQ-9 | Scheduler, Planning skills |
| REQ-10 | Planning skills (task store) |
| REQ-11 | Planning skills (briefing), Scheduler, Connector Layer |
| REQ-12 | Scheduler (routines), Action Gate |
| REQ-13 | Connector Layer (mail), Comms skills |
| REQ-14 | Comms skills, Action Gate |
| REQ-15 | Knowledge skills (search), Brain |
| REQ-16 | Document Index, Knowledge skills (documents) |
| REQ-17 | Knowledge skills (screen/clipboard) |
| REQ-18 | Knowledge skills (utilities) |
| REQ-19 | Capture Service, STT Service |
| REQ-20 | System skills (file search), Document Index |
| REQ-21 | System skills (organize), Action Gate, Undo Journal |
| REQ-22 | System skills (app/system control) |
| REQ-23 | System skills (focus), Scheduler |
| REQ-24 | Action Gate |
| REQ-25 | Undo Journal, Action Executor, History UI |
| REQ-26 | Connector Layer (credentials), STT/TTS/Index (local), Settings & Privacy UI |
| REQ-27 | Brain (all skill calls), Connector Layer |
| REQ-28 | UI (keyboard/contrast/scaling), Persona Config (language), STT/TTS |
| REQ-29 | Installer/Packaging, first-run setup |
| REQ-30 | All components (tooling choice constraint) |
| REQ-31 | Brain (model loading), Document Index (low-priority indexing), Presence Layer |
| REQ-32 | Presence Layer (isolation boundary) |
| REQ-33 | Skill Registry |

`tasks.md` extends this matrix one level further, mapping each REQ/component pair to concrete implementation tasks.

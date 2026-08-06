# Requirements Document — Project "Kai" (Personal Desktop Assistant)

> "Kai" is a working title — rename freely. Referenced throughout `design.md` and `tasks.md` for traceability.

## 1. Overview

Kai is an always-available desktop assistant that reduces the everyday friction of using a PC. It talks and listens, remembers what matters about the person using it, keeps track of their calendar, reminders, tasks and files, answers questions with live internet data, reads and summarizes their own documents, and performs real actions on the machine — always under the user's control.

The design goal is **usefulness per interruption**: every feature must remove a step the user would otherwise do manually. Personality and voice make it pleasant to use; they are not the product.

It runs locally on the user's PC, is built entirely on free/open-source tooling and free-tier services, and installs as a single package.

## 2. Stakeholders

- **Primary user**: a non-technical everyday PC user. Wants things done in one sentence, not configured in five dialogs.
- **Developer/maintainer**: Gabox.

## 3. Scope

**In scope (v1):** voice and text conversation, configurable persona, short- and long-term memory, calendar and reminders, task/note capture, daily briefing, scheduled routines, inbox triage and draft assistance, web-grounded Q&A, Q&A over the user's own documents, screen/clipboard assistance, meeting capture and summarization, file search and safe organization, app/system control, focus sessions, confirmation-and-undo for consequential actions, single-step Windows installer.

**Out of scope (v1):** mobile companion app, multi-user accounts, cloud-hosted deployment, autonomous actions taken without user initiation or an explicitly configured routine, sending messages without confirmation.

**Optional / off by default (v1):** animated visual presence (REQ-32), custom cloned voice (REQ-4), third-party skill plugins (REQ-33).

## 4. Assumptions

- A4.1 — Primary target OS is Windows; cross-platform packaging via Tauri is a stretch goal, not a v1 requirement.
- A4.2 — "Free" means no mandatory paid subscription for core functionality; free tiers of cloud APIs (e.g. Groq, Tavily) are acceptable since they have no cost at expected usage volume.
- A4.3 — The user has a working microphone and speakers, but every voice feature has a text equivalent (REQ-1), so audio hardware is never a hard dependency.
- A4.4 — Internet connectivity is available for search and connector-backed features; degraded/offline behavior is covered by REQ-27.
- A4.5 — Connectors (calendar, mail) are opt-in. The assistant is fully useful with none of them connected; connecting them unlocks REQ-8, REQ-11, REQ-13, REQ-14.

## 5. Requirements

### 5.1 Conversation & personalization

**REQ-1 — Text and voice conversation**
User story: As the user, I want to reach the assistant however is convenient right now — typing when I'm at my desk, speaking when my hands are busy.

- THE SYSTEM SHALL accept every user request through either a text input or a voice input, with identical capability in both.
- WHEN a reply is produced, THE SYSTEM SHALL render it as text and, if voice output is enabled, speak it.
- THE SYSTEM SHALL be reachable at any time via a global hotkey without changing the focused window.

**REQ-2 — Hands-free activation**
User story: As the user, I want to start talking without clicking anything.

- WHEN the wake phrase is detected in the audio stream, THE SYSTEM SHALL begin active listening within 500ms.
- WHILE not activated, THE SYSTEM SHALL NOT transcribe, record, or transmit audio anywhere.
- IF no wake word engine is available on install, THEN THE SYSTEM SHALL fall back to a push-to-talk hotkey.
- THE SYSTEM SHALL allow the wake phrase to be changed or hands-free activation to be disabled entirely.

**REQ-3 — Local speech transcription (STT)**
User story: As the user, I want to speak naturally and be understood accurately.

- WHEN active listening ends (silence detected via VAD), THE SYSTEM SHALL transcribe the captured audio to text.
- THE SYSTEM SHALL perform transcription locally, without sending raw audio to a third-party service.
- IF transcription confidence is very low, THEN THE SYSTEM SHALL ask the user to repeat rather than guessing.

**REQ-4 — Spoken output**
User story: As the user, I want spoken replies when I'm not looking at the screen, in a voice I can stand hearing all day.

- WHEN a text reply is finalized and voice output is enabled, THE SYSTEM SHALL synthesize speech locally.
- THE SYSTEM SHALL offer a selectable set of bundled voices, and MAY optionally support a voice cloned from a user-supplied audio clip.
- WHERE a cloned voice is used, THE SYSTEM SHALL keep the voice model on the local machine and SHALL NOT transmit it to a third party.
- THE SYSTEM SHALL allow speech output to be muted without disabling any other capability.

**REQ-5 — Configurable persona and interaction style**
User story: As the user, I want to set how the assistant talks to me — brief and dry, or warm and chatty — without editing code.

- THE SYSTEM SHALL expose persona settings (name, tone, verbosity, language, forms of address) in a single human-editable config file and in an in-app settings screen.
- WHEN persona settings change, THE SYSTEM SHALL apply them on the next turn without requiring a restart or rebuild.
- THE SYSTEM SHALL apply the configured persona to every reply, including error and confirmation messages.
- THE SYSTEM SHALL default to a concise style: answer first, elaborate only if asked.

**REQ-6 — Short-term conversation memory**
User story: As the user, I want to say "and add that to my calendar too" without repeating myself.

- WHEN a new turn is processed, THE SYSTEM SHALL include recent conversation history (a rolling window) in context.
- THE SYSTEM SHALL resolve pronouns and references against the current session's turns and the result of the most recent tool call.
- IF the session is idle beyond a configurable timeout, THEN THE SYSTEM SHALL reset short-term memory.

**REQ-7 — Long-term personal memory**
User story: As the user, I want to tell the assistant something once — where my project folder is, that I'm allergic to shellfish, that "the standup" means 9:15 Tuesdays — and never repeat it.

- WHEN the user states a durable fact or preference, THE SYSTEM SHALL offer to remember it, and SHALL store it locally on confirmation.
- WHEN a stored fact is relevant to the current request, THE SYSTEM SHALL apply it without being reminded.
- WHEN the user corrects a stored fact, THE SYSTEM SHALL update or remove it.
- THE SYSTEM SHALL provide a screen where every stored memory can be reviewed, edited, and deleted individually or all at once.
- THE SYSTEM SHALL NOT store a fact silently: each write SHALL be visible to the user at the time it happens.

### 5.2 Daily planning

**REQ-8 — Calendar awareness and scheduling**
User story: As the user, I want to ask what my day looks like and to add events by saying them.

- WHERE a calendar connector is configured, THE SYSTEM SHALL read upcoming events and answer questions about the user's schedule ("what's next", "am I free Thursday afternoon").
- WHEN the user asks to create, move, or cancel an event, THE SYSTEM SHALL parse natural date/time expressions ("next Tuesday at 3", "in two hours") and SHALL confirm the resolved date, time, and title before writing (REQ-24).
- IF no calendar connector is configured, THEN THE SYSTEM SHALL say so and offer to set one up, rather than failing silently.

**REQ-9 — Reminders, timers, and alarms**
User story: As the user, I want to offload anything I'd otherwise have to hold in my head.

- WHEN the user sets a reminder, timer, or alarm in natural language, THE SYSTEM SHALL schedule it and confirm the resolved time.
- WHEN a scheduled item is due, THE SYSTEM SHALL notify the user via a desktop notification and, if voice output is enabled, speech.
- THE SYSTEM SHALL support recurring reminders ("every weekday at 6pm").
- THE SYSTEM SHALL persist pending reminders across restarts and machine reboots, and SHALL deliver a missed reminder on next launch with its original due time stated.
- THE SYSTEM SHALL list, edit, and cancel pending reminders on request.

**REQ-10 — Task and note capture**
User story: As the user, I want to capture a thought in one sentence and find it later.

- WHEN the user dictates a task or note, THE SYSTEM SHALL store it locally with a timestamp and any tags it can infer.
- THE SYSTEM SHALL list, search, complete, and delete stored tasks and notes on request.
- THE SYSTEM SHALL store tasks and notes in a plain, human-readable format so they are not trapped in the app.

**REQ-11 — Daily briefing and catch-up**
User story: As the user, I want one short summary at the start of my day instead of opening five apps.

- WHEN the user asks for a briefing, or at a configured time, THE SYSTEM SHALL summarize: today's calendar, due and overdue reminders/tasks, and — where connectors are configured — messages needing attention.
- THE SYSTEM SHALL let the user configure which sections appear in the briefing and in what order.
- IF a briefing section's data source is unavailable, THEN THE SYSTEM SHALL deliver the remaining sections and note which one it could not reach (REQ-27).

**REQ-12 — Routines and scheduled automations**
User story: As the user, I want recurring things to just happen — the briefing every weekday morning, the Downloads folder tidied every Friday.

- THE SYSTEM SHALL allow the user to define a routine as a trigger (time, schedule, or system event) plus one or more actions.
- WHEN a routine's trigger fires, THE SYSTEM SHALL execute its actions and record the outcome in the action history (REQ-25).
- WHERE a routine includes an action classed as consequential (REQ-24), THE SYSTEM SHALL require the user to approve that action at routine-creation time, and SHALL re-prompt if the routine is later edited.
- THE SYSTEM SHALL list, disable, and delete routines on request.

### 5.3 Communication

**REQ-13 — Inbox and message triage**
User story: As the user, I want to know what actually needs me without reading 40 emails.

- WHERE a mail connector is configured, THE SYSTEM SHALL summarize unread messages grouped by whether they appear to need a reply, and SHALL name senders and subjects.
- WHEN asked about a specific message or sender, THE SYSTEM SHALL summarize the relevant thread.
- THE SYSTEM SHALL support marking read, archiving, and flagging on request, subject to REQ-24.

**REQ-14 — Drafting and reply assistance**
User story: As the user, I want help writing the reply, but I decide what gets sent.

- WHEN asked to draft or reply, THE SYSTEM SHALL produce a draft in the user's configured writing style and present it for review.
- THE SYSTEM SHALL support iterating on a draft conversationally ("shorter", "less formal", "add that I'm out Friday").
- THE SYSTEM SHALL NOT send, reply to, or forward any message without explicit per-message confirmation from the user, and SHALL NOT treat a general "yes, go ahead" from an earlier turn as standing authorization.

### 5.4 Knowledge & information

**REQ-15 — Web-grounded answers**
User story: As the user, I want to ask about current or unfamiliar things and get a real answer, not a guess.

- WHEN the assistant cannot answer confidently from its own knowledge or stored memory, THE SYSTEM SHALL issue a web search before replying.
- WHEN search results are returned, THE SYSTEM SHALL synthesize them into a natural answer rather than reading raw results aloud, and SHALL make the source(s) available on request or in the text transcript.
- IF the search call fails, THEN THE SYSTEM SHALL say it could not look that up rather than answering from guesswork (REQ-27).

**REQ-16 — Q&A over the user's own documents**
User story: As the user, I want to ask "what did the lease say about the deposit" and get the answer out of my own files.

- THE SYSTEM SHALL index the text of documents in user-selected folders locally (at minimum PDF, DOCX, TXT, MD).
- WHEN a question is best answered from indexed documents, THE SYSTEM SHALL answer from them and SHALL cite the source file (and page/section where available).
- THE SYSTEM SHALL keep the index up to date as files in watched folders change.
- THE SYSTEM SHALL perform indexing and retrieval locally, and SHALL let the user see and change exactly which folders are indexed, and clear the index.

**REQ-17 — Screen and clipboard assistance**
User story: As the user, I want to ask about what I'm looking at without describing it.

- WHEN the user invokes screen assistance, THE SYSTEM SHALL capture the current screen or the clipboard contents and use it as context for the request (explain, summarize, translate, extract, rewrite).
- THE SYSTEM SHALL capture the screen only on explicit user invocation, never continuously or in the background.
- THE SYSTEM SHALL indicate visibly when a capture has been taken, and SHALL discard it after the turn unless the user saves it.

**REQ-18 — Everyday utilities**
User story: As the user, I want the small lookups answered instantly instead of opening a browser tab.

- THE SYSTEM SHALL answer unit conversions, currency conversions, date/time math, time-zone lookups, calculations, definitions, and spelling directly.
- THE SYSTEM SHALL translate text between the user's configured languages on request.
- WHERE a utility needs live data (e.g. exchange rates, weather), THE SYSTEM SHALL fetch it and state the value's as-of time.

**REQ-19 — Meeting and long-audio capture**
User story: As the user, I want a summary and my action items out of a call I just sat through.

- WHEN the user starts a capture session, THE SYSTEM SHALL transcribe audio locally until stopped.
- WHEN a capture session ends, THE SYSTEM SHALL produce a summary, decisions, and action items, and SHALL offer to save action items as tasks (REQ-10).
- THE SYSTEM SHALL require an explicit start action for every capture session and SHALL display a persistent recording indicator while capturing.
- THE SYSTEM SHALL store transcripts locally and allow the user to delete them.

### 5.5 Computer control

**REQ-20 — File search and retrieval**
User story: As the user, I want to find a file by describing it, not by remembering where I put it.

- WHEN the user describes a file by name, type, approximate date, or content, THE SYSTEM SHALL locate matching files and offer to open or reveal them.
- WHEN multiple files match, THE SYSTEM SHALL present the most likely candidates with enough detail to distinguish them rather than opening one arbitrarily.

**REQ-21 — Safe file organization**
User story: As the user, I want a messy folder tidied without risking anything.

- WHEN instructed to organize a folder, THE SYSTEM SHALL sort files into subfolders by type and/or date.
- THE SYSTEM SHALL preview the proposed changes and require confirmation before moving anything (REQ-24).
- THE SYSTEM SHALL NOT permanently delete any file; deletions SHALL route to the recycle bin.
- THE SYSTEM SHALL record every move so the operation can be undone in full (REQ-25).

**REQ-22 — Application and system control**
User story: As the user, I want to open apps and change basic settings by asking.

- WHEN a recognized app or system command is given, THE SYSTEM SHALL execute the corresponding local action (launch app, close app, adjust volume, change audio output device, lock screen, sleep, screenshot, toggle Wi-Fi).
- IF the requested app or action cannot be found, THEN THE SYSTEM SHALL report the failure conversationally and suggest the closest match.
- THE SYSTEM SHALL let the user define named shortcuts for multi-step sequences ("work setup" → open these three apps).

**REQ-23 — Focus sessions**
User story: As the user, I want help actually starting work and actually taking breaks.

- WHEN a focus session is started with a duration, THE SYSTEM SHALL close or minimize configured distracting apps, suppress non-critical notifications, and mute its own proactive prompts for that duration.
- WHILE a focus session is active, THE SYSTEM SHALL still respond to direct user requests.
- WHEN the duration ends, THE SYSTEM SHALL notify the user, restore notifications, and offer a break or another session.
- THE SYSTEM SHALL support interval patterns (e.g. work/break cycles) and SHALL allow a session to be ended early.

### 5.6 Trust & control

**REQ-24 — Confirmation before consequential actions**
User story: As the user, I want to be asked before anything happens that I can't shrug off.

- THE SYSTEM SHALL classify each action as routine or consequential. Consequential actions include at minimum: sending or replying to any message, creating/modifying/cancelling calendar events, moving or deleting files, closing applications with unsaved state, and changing system settings.
- WHEN a consequential action is about to run, THE SYSTEM SHALL state exactly what it will do — naming targets and counts — and SHALL wait for explicit confirmation.
- THE SYSTEM SHALL treat confirmation as valid for that single action only, and SHALL NOT carry approval forward to later actions.
- THE SYSTEM SHALL allow the user to mark specific action types as pre-approved, and SHALL make that setting reviewable and revocable.

**REQ-25 — Action history and undo**
User story: As the user, I want to see what the assistant did and take it back.

- WHEN any action is executed, THE SYSTEM SHALL record what ran, when, with what parameters, and whether it succeeded.
- THE SYSTEM SHALL provide a reviewable action history.
- WHERE an action is reversible (file moves, organization runs, task/reminder/event changes), THE SYSTEM SHALL support undoing it on request, including undoing the entire batch of a single organization run.
- IF an action is not reversible, THEN THE SYSTEM SHALL say so at the confirmation step, before running it.

**REQ-26 — Local-first privacy and data control**
User story: As the user, I want my voice, files, and messages to stay on my machine.

- THE SYSTEM SHALL process wake word detection, VAD, STT, TTS, and document indexing locally by default.
- THE SYSTEM SHALL transmit data externally only for explicitly network-dependent features (web search, live-data utilities, configured connectors, optional cloud LLM), and SHALL make clear in settings which features send data off the machine.
- WHERE a cloud LLM is enabled, THE SYSTEM SHALL disclose that conversation content is sent to it, and SHALL support running fully locally instead.
- THE SYSTEM SHALL store credentials for connectors in the OS credential store, never in plaintext config.
- THE SYSTEM SHALL provide a single action that deletes all stored conversation history, memories, transcripts, and indexes.

**REQ-27 — Graceful degradation**
User story: As the user, I want the assistant to stay usable when something external breaks.

- IF any external call (search, connector, live data, cloud LLM) fails or times out, THEN THE SYSTEM SHALL inform the user conversationally rather than crashing or hanging.
- WHEN a network-dependent feature is unavailable, THE SYSTEM SHALL continue to support all locally-run features (conversation, memory, reminders, tasks, document Q&A, file and system actions).
- IF a component's free tier hits a usage cap, THEN THE SYSTEM SHALL fall back to a local equivalent where one exists and SHALL tell the user what changed.
- THE SYSTEM SHALL NOT fabricate an answer or a result in place of a failed call.

### 5.7 Platform & non-functional

**REQ-28 — Accessibility and language**
User story: As the user, I want to use this comfortably regardless of how I interact with my PC or what language I speak.

- THE SYSTEM SHALL be fully operable by keyboard alone, and SHALL be fully operable without audio input or output.
- THE SYSTEM SHALL support at minimum English and Spanish for conversation, STT, and TTS, selectable in settings.
- THE SYSTEM SHALL respect OS-level text scaling and high-contrast settings in its own UI.

**REQ-29 — Single-step installation**
User story: As a non-technical user, I want to install this without configuring Python, models, or dependencies.

- THE SYSTEM SHALL ship as a single installer that bundles or auto-fetches all required runtime components.
- WHEN installation completes, THE SYSTEM SHALL be launchable from a single icon with no manual setup steps.
- WHEN first launched, THE SYSTEM SHALL run a short setup that covers language, voice on/off, hotkey, and optional connectors — and SHALL be usable if the user skips all of it.

**REQ-30 — Zero-cost toolchain**
User story: As the developer, I want the whole stack buildable and runnable without paying for licenses or subscriptions.

- THE SYSTEM SHALL use only free, open-source, or free-tier tools for all v1 functionality.
- IF a component's free tier has a usage cap, THEN THE SYSTEM SHALL degrade gracefully rather than fail hard (REQ-27).

**REQ-31 — Low idle resource footprint**
User story: As the user, I want this running in the background without slowing my PC down.

- WHILE idle, THE SYSTEM SHALL keep CPU usage low enough not to be noticeable during normal PC use.
- THE SYSTEM SHALL NOT keep heavy models loaded in memory when not actively needed, where feasible.
- THE SYSTEM SHALL perform document indexing at low priority and SHALL pause it during focus sessions and on battery power.

**REQ-32 — Optional visual presence**
User story: As the user, I want to see at a glance whether it's listening, thinking, or speaking — and optionally give it a face, if I want one.

- THE SYSTEM SHALL display a minimal state indicator (idle / listening / thinking / speaking) by default.
- THE SYSTEM MAY optionally display a richer animated character overlay, disabled by default and enabled in settings.
- THE SYSTEM SHALL keep the visual layer decoupled from the rest of the pipeline, consuming only state, audio, and an optional emotion tag — so the presentation can be replaced without changing the brain, STT, TTS, or actions.

**REQ-33 — Extensible skills**
User story: As the developer, I want to add new capabilities without touching the core.

- THE SYSTEM SHALL define a skill interface (name, description, parameters, handler, consequential flag) that the brain discovers at startup.
- WHEN a skill is added, THE SYSTEM SHALL make it available to the tool router without changes to the router itself.
- THE SYSTEM SHALL let the user enable or disable individual skills in settings.

## 6. Traceability summary

Every REQ ID above is referenced by name in `design.md` (component and data-model mapping) and `tasks.md` (implementation tasks), so any requirement can be traced forward to its design and implementation, and any task can be traced back to the requirement it satisfies.

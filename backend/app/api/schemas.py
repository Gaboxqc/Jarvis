"""What the API sends back — the other half of the contract.

Every handler used to be annotated `-> dict[str, Any]`, which is true and says
nothing. FastAPI put `{"type": "object", "additionalProperties": true}` in the
schema for all seventy of them, `openapi-typescript` rendered that as
`{[key: string]: unknown}`, and the only description of what the backend
actually returns was 354 lines of hand-written TypeScript in
ui/src/api/types.ts -- maintained in parallel, by hand, in a different
language, with nothing checking the two against each other.

So a renamed response field passed ruff, passed mypy, passed pytest, passed
`tsc`, and broke the UI at runtime. That was demonstrated rather than argued:
three fields were added to /health's response and the committed OpenAPI
snapshot did not move by one line.

These models close it. The schema now describes the responses, the generated
TypeScript describes them too, and ui/src/api/types.ts is aliases over the
generated types instead of a second opinion.

Two things to know when editing this file
-----------------------------------------

**A field missing here is a field deleted from the response.** FastAPI filters
the returned dict through the model, so an incomplete model is not a weaker
contract -- it is a silent data loss bug. Every model below was derived from
the `to_dict()` or the literal dict the handler actually returns, and the way
that was verified is worth copying: make ui/src/api/types.ts alias these, run
`tsc`, and every field the UI reads that a model forgot becomes a type error.
That found the real gaps; reading the code found most of them.

**Open-ended really does mean open-ended.** `settings.current`, a skill's
`data`, a transcript's `summary`: these carry shapes decided elsewhere, and
inventing structure for them here would be a lie with a type annotation on it.
They stay `dict[str, Any]`, which is honest rather than lazy -- the difference
from where this started is that it is now true of six fields rather than of
seventy endpoints.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

# -- the turn (REQ-1 to REQ-7) -------------------------------------------


class PendingAction(BaseModel):
    """An action waiting for a yes. The id is the whole contract (REQ-24)."""

    action_id: str
    skill: str
    preview: str
    reversible: bool


class SkillCall(BaseModel):
    """One gate outcome, as GateOutcome.to_dict() produces it.

    The hand-written TypeScript described three of these eight fields. That
    was not wrong -- TypeScript does not object to fields it was not told
    about -- but it meant `data` and `action_id` were being used from the
    wire with no type behind them.
    """

    status: str
    skill: str
    action_id: str | None
    preview: str
    reversible: bool
    message: str
    #: Whatever the skill chose to return. Shape is the skill's business.
    data: dict[str, Any]
    error: str | None


class TurnResult(BaseModel):
    reply: str
    needs_confirmation: bool
    pending: PendingAction | None
    skill_calls: list[SkillCall]
    #: Skills the message asked for that this turn did not run (REQ-27). The
    #: hand-written type omitted this field entirely, so the UI could not show
    #: it -- which is the whole point of it existing.
    not_run: list[str]
    error: str | None


class ClearedSession(BaseModel):
    cleared_turns: int


# -- the Action Gate (REQ-24, REQ-25) ------------------------------------


class ParkedAction(BaseModel):
    """A pending action as /actions/pending lists it.

    Not `PendingAction`: this one carries `expires_at` and that one does not,
    and they were the same name on the wire until this file made the
    difference visible. A confirmation has a TTL and a client that cannot see
    it cannot tell a live question from a dead one.
    """

    action_id: str
    skill: str
    preview: str
    reversible: bool
    expires_at: str | None


class PendingActions(BaseModel):
    pending: list[ParkedAction]


class ActionRecord(BaseModel):
    id: str
    batch_id: str
    skill: str
    preview: str
    severity: str
    status: str
    can_undo: bool
    executed_at: str | None
    error: str | None


class ActionHistory(BaseModel):
    history: list[ActionRecord]


class UndoOutcome(BaseModel):
    ok: bool
    message: str
    undone: list[str]
    failed: list[str]


class PreApproval(BaseModel):
    skill: str
    source: str
    granted_at: str | None


class PreApprovals(BaseModel):
    pre_approvals: list[PreApproval]


class PreApprovalGranted(BaseModel):
    granted: str
    pre_approvals: list[PreApproval]


class PreApprovalRevoked(BaseModel):
    revoked: str
    pre_approvals: list[PreApproval]


class SkillParameter(BaseModel):
    type: str
    description: str
    required: bool
    enum: list[str] | None = None


class SkillEntry(BaseModel):
    """One catalog entry, as the router sees it."""

    name: str
    description: str
    consequential: bool
    parameters: dict[str, SkillParameter]


class SkillCatalog(BaseModel):
    skills: list[SkillEntry]


# -- memory (REQ-7) ------------------------------------------------------


class MemoryFact(BaseModel):
    id: str
    text: str
    category: str
    created_at: str | None


class MemoryFacts(BaseModel):
    facts: list[MemoryFact]


class DeletedFact(BaseModel):
    deleted: MemoryFact


class DeletedFacts(BaseModel):
    deleted: int


# -- scheduling (REQ-9, REQ-10, REQ-12, REQ-22) --------------------------


class ScheduledItem(BaseModel):
    id: str
    kind: str
    label: str
    next_fire_at: str | None
    recurring: bool
    active: bool


class Reminders(BaseModel):
    reminders: list[ScheduledItem]


class Routine(ScheduledItem):
    #: One line per action, as the approval preview names them (REQ-12).
    steps: list[str]
    needs_approval: bool


class Routines(BaseModel):
    routines: list[Routine]


class RoutineApproved(BaseModel):
    approved: bool
    routine: ScheduledItem


class Shortcut(BaseModel):
    id: str
    label: str
    steps: list[str]
    needs_approval: bool


class Shortcuts(BaseModel):
    shortcuts: list[Shortcut]


class ShortcutApproved(BaseModel):
    approved: bool
    shortcut: ScheduledItem


class SequenceStep(BaseModel):
    skill: str
    status: str
    message: str


class SequenceRun(BaseModel):
    """What running a routine or a shortcut did, per step.

    `routine_id` duplicates `id` and is kept: it is the name routines used
    before shortcuts existed, and dropping it from the schema would be a
    breaking change dressed up as tidying.
    """

    id: str
    routine_id: str
    label: str
    batch_id: str
    steps: list[SequenceStep]
    ran: int
    skipped: int
    #: True when a step was skipped for want of approval, so the caller can
    #: say why rather than reporting a short run as a complete one. It was
    #: being returned and was absent from the first draft of this model, which
    #: means response_model was dropping it -- found by running the suite with
    #: `extra="forbid"` on every model, which is the audit worth repeating
    #: whenever one of these is added.
    needs_approval: bool


class Deleted(BaseModel):
    deleted: str


class Task(BaseModel):
    id: str
    text: str
    kind: str
    tags: list[str]
    due: str | None
    done: bool
    created_at: str | None


class Tasks(BaseModel):
    tasks: list[Task]


class CreatedTask(BaseModel):
    """What POST /tasks answers with, which is not a `Task`.

    create_task() returns the five fields it just decided, and not `kind`
    or `created_at`. The client typed this as a full Task, so two of its
    fields were a fiction -- nothing reads them today, which is the only
    reason it never bit.
    """

    id: str
    text: str
    tags: list[str]
    due: str | None
    done: bool


class TaskDone(BaseModel):
    id: str
    text: str
    done: bool


class TaskRemoved(BaseModel):
    id: str
    text: str


class ReminderCancelled(BaseModel):
    cancelled: str
    label: str


class Delivered(BaseModel):
    delivered: list[str]


# -- documents (REQ-16) --------------------------------------------------


class IndexFailure(BaseModel):
    #: The hand-written type had `path` and `error` and not `file`, so the
    #: screen could not show a filename it was being sent.
    file: str
    path: str
    error: str | None


class IndexStatus(BaseModel):
    folders: list[str]
    running: bool
    paused: bool
    #: Why a scan is holding off -- on battery, a focus session. Null when it
    #: is not (REQ-31).
    deferred_because: str | None
    last_scan: str | None
    documents: int
    chunks: int
    failed: int
    last_indexed: str | None
    failures: list[IndexFailure]


class IndexedDocument(BaseModel):
    file: str
    path: str
    chunks: int
    indexed_at: str | None
    error: str | None


class IndexedDocuments(BaseModel):
    documents: list[IndexedDocument]


class ScanResult(BaseModel):
    indexed: int
    updated: int
    removed: int
    failed: int
    skipped: int
    errors: list[str]
    duration_seconds: float


class SemanticStatus(BaseModel):
    enabled: bool
    model: str
    model_installed: bool
    download_mb: int
    chunks: int
    embedded: int


class DocumentHit(BaseModel):
    file: str
    path: str
    section: str
    #: "lease.pdf (Section 4)" -- what an answer should be attributed to.
    citation: str
    text: str


class DocumentHits(BaseModel):
    results: list[DocumentHit]


class IndexCleared(BaseModel):
    cleared_documents: int


# -- connectors (REQ-8, REQ-13, REQ-26) ----------------------------------


class CredentialStore(BaseModel):
    available: bool
    backend: str
    detail: str


class Account(BaseModel):
    """An account's details. Never a secret, by construction.

    `credential_stored` and `credential_ref` were being returned and were
    absent from the hand-written type, so the screen could not tell a
    configured account from one still waiting for its password.
    """

    kind: str
    label: str
    provider: str
    target: str
    username: str
    writable: bool
    enabled: bool
    credential_stored: bool
    credential_ref: str


class Connectors(BaseModel):
    credential_store: CredentialStore
    calendar: list[Account]
    mail: list[Account]


class AddedAccount(BaseModel):
    kind: str
    label: str
    provider: str
    config_file: str
    needs_secret: bool
    #: "password" for mail and CalDAV, "url" for an iCal calendar.
    secret_kind: Literal["password", "url"]


class RemovedAccount(BaseModel):
    removed: str
    kind: str
    config_file: str


class CredentialStored(BaseModel):
    """The reference, never the value."""

    stored: bool
    kind: str
    label: str
    credential_ref: str


class CredentialForgotten(BaseModel):
    removed: bool


class AccountCheck(BaseModel):
    ok: bool
    label: str
    unread: int | None = None
    events: int | None = None
    error: str | None = None


class BriefingSection(BaseModel):
    name: str
    lines: list[str]
    #: Set when that one source failed. The rest of the briefing still arrives.
    error: str | None = None
    #: False when there is no account for this source at all -- not the same as
    #: having nothing to report, and the screen must not conflate them.
    configured: bool


class Briefing(BaseModel):
    sections: list[BriefingSection]


# -- voice (REQ-1 to REQ-4) ----------------------------------------------


class SttStatus(BaseModel):
    model: str
    installed: bool
    loaded: bool
    idle_seconds: float | None


class TtsStatus(BaseModel):
    voice: str
    installed: bool
    loaded: bool
    muted: bool
    available: list[str]


class WakeStatus(BaseModel):
    phrase: str
    enabled: bool
    installed: bool
    available: bool
    threshold: float


class VoiceStatus(BaseModel):
    enabled: bool
    input_enabled: bool
    output_enabled: bool
    microphone: bool
    stt: SttStatus
    tts: TtsStatus
    wake: WakeStatus
    models_ready: bool
    download_mb: int


class VoiceModel(BaseModel):
    name: str
    kind: str
    present: bool
    detail: str = ""
    approx_mb: int = 0


class VoiceModels(BaseModel):
    models: list[VoiceModel]
    missing_mb: int
    location: str


class FailedDownload(BaseModel):
    model: str
    error: str


class VoiceModelsDownloaded(BaseModel):
    downloaded: list[str]
    failed: list[FailedDownload]
    ready: bool
    location: str


class VoiceModelsRemoved(BaseModel):
    freed_mb: int


class SpeechShape(BaseModel):
    #: False when speech output is muted, or no device would take it.
    spoke: bool
    seconds: float
    sample_rate: int
    #: Loudness 0-1 at 30 samples per second. Drives the avatar's mouth.
    envelope: list[float]


class VoiceTurn(BaseModel):
    heard: str
    confidence: float
    reply: str
    spoke: bool
    needs_confirmation: bool
    error: str | None = None


class CloneStatus(BaseModel):
    installed: bool
    engine_installed: bool
    packaged: bool
    enabled: bool
    consented: bool
    has_reference: bool
    reference_seconds: float
    loaded: bool
    min_seconds: int
    licence: str
    licence_accepted: bool
    licence_accepted_at: str


class EngineProgress(BaseModel):
    installed: bool
    state: Literal["idle", "downloading", "verifying", "installing", "installed", "failed"]
    received: int
    total: int
    error: str | None


class EngineRemoved(BaseModel):
    removed: bool
    installed: bool


class ReferenceSaved(CloneStatus):
    """The clone status, plus what the upload itself produced."""

    seconds: float
    path: str


class ReferenceForgotten(CloneStatus):
    removed: bool


# -- the avatar's runtime licence (REQ-32) -------------------------------


class AvatarLicence(BaseModel):
    licence_accepted: bool
    licence_accepted_at: str
    licence_summary: str
    licence_url: str


# -- meeting capture (REQ-19) --------------------------------------------


class CaptureSummary(BaseModel):
    summary: str
    decisions: list[str]
    actions: list[str]
    truncated: bool
    error: str | None


class CaptureStatus(BaseModel):
    recording: bool
    transcript_id: str | None
    label: str
    seconds: float
    #: Which inputs were captured. Microphone only, today.
    sources: list[str]
    words: int
    #: Why capture is degraded -- no microphone, a failed source.
    note: str


class Transcript(BaseModel):
    id: str
    label: str
    started_at: str | None
    ended_at: str | None
    sources: list[str]
    minutes: float
    words: int
    #: Written by summarize.summarise() and stored as it came -- so the shape
    #: *is* known, and calling it open-ended was wrong: the meetings screen
    #: reads summary.decisions and summary.actions directly, and typing this
    #: as a bare object is what left it doing that untyped.
    summary: CaptureSummary | None
    running: bool


class Transcripts(BaseModel):
    transcripts: list[Transcript]


class TranscriptDetail(Transcript):
    text: str


class StoppedCapture(BaseModel):
    """A finished recording, with the summary this call just produced.

    Not a subclass of `Transcript`: that one's `summary` is whatever was
    stored and so is open-ended, and narrowing a field in a subclass is a
    promise the parent did not make. Here the shape *is* known, because
    summarize.summarise() produced it one line above -- so it is spelled out,
    and the duplication is the price of the two being genuinely different.
    """

    id: str
    label: str
    started_at: str | None
    ended_at: str | None
    sources: list[str]
    minutes: float
    words: int
    summary: CaptureSummary | None
    running: bool


class DeletedTranscript(BaseModel):
    deleted: Transcript


# -- focus, presence, health (REQ-23, REQ-27, REQ-31, REQ-32) ------------


class FocusState(BaseModel):
    """One shape for GET /focus and POST /focus/start.

    They returned two: the first carried `would_close` and not `message`,
    the second the reverse. The Today screen stores the result of both in
    the same state and reads `minutes_left` and `closed_apps` off either,
    so the shapes were already required to agree and simply did not.
    Making them one is the fix; the alternative is a client-side union
    describing a difference with no reason behind it.
    """

    active: bool
    minutes_left: int
    closed_apps: list[str]
    #: Apps running right now that starting a session would terminate, so
    #: the screen can name them before asking (REQ-23).
    would_close: list[str]
    #: What the skill said it did. Null when nothing was started.
    message: str | None


class FocusEnded(BaseModel):
    """Ending a session answers with the one fact that changed."""

    active: bool


class PresenceState(BaseModel):
    state: Literal["idle", "listening", "thinking", "speaking", "recording"]
    emotion: str | None
    recording: bool
    focus: bool


class Notification(BaseModel):
    id: str
    kind: str
    title: str
    body: str
    at: str


class Notifications(BaseModel):
    notifications: list[Notification]


class BrainHealth(BaseModel):
    ok: bool
    host: str
    #: What will actually answer, which may not be `configured` -- see
    #: brain/llm.py active_model().
    model: str
    configured: str
    substituted: bool
    model_installed: bool
    #: What Ollama reports, tags included (`qwen2.5:latest`).
    models: list[str]
    error: str | None


class PrivacyFlags(BaseModel):
    web_search: bool
    live_data: bool
    cloud_llm: bool


class Health(BaseModel):
    ok: bool
    version: str
    problem: str | None
    brain: BrainHealth
    skills: int
    persona: str
    config_file: str | None
    data_dir: str
    privacy: PrivacyFlags


# -- settings, retention, diagnostics, the wipe (REQ-5, REQ-26, REQ-27) --


class Settings(BaseModel):
    #: Section -> the settings in it. Open-ended on purpose: what is in a
    #: section is settings.py's business, and restating it here would be a
    #: second definition to keep in step.
    current: dict[str, dict[str, Any]]
    #: Section -> keys the API will accept. Anything absent is refused.
    writable: dict[str, list[str]]


class SettingsUpdated(BaseModel):
    #: Section -> the keys that were actually written, after validation.
    changed: dict[str, dict[str, Any]]
    #: Absent when nothing changed, because there was nothing to write to.
    config_file: str | None = None


class RetentionWindow(BaseModel):
    """The window, and what is inside it right now (REQ-26).

    Four fields, and the first draft of this model had five: an `oldest_turn`
    that retention.describe() does not return and never has. Pydantic refused
    the response and the endpoint answered 500, which is the right failure --
    a response model that invents a field is a contract the server cannot
    keep, and it is better to find that in a test than to have the field
    quietly default itself into the schema.
    """

    conversation_days: int
    history_days: int
    conversation_turns: int
    action_records: int


class RetentionSwept(BaseModel):
    #: Table -> rows removed. Two tables today; a dict rather than two fields
    #: because retention.py decides which, and this should not have an opinion.
    removed: dict[str, int]


class LogFile(BaseModel):
    name: str
    size_bytes: int
    modified: float


class LogSummary(BaseModel):
    directory: str
    files: list[LogFile]
    total_bytes: int
    exists: bool


class Acknowledged(BaseModel):
    ok: bool


class Wiped(BaseModel):
    removed: dict[str, int]


class ExportCounts(BaseModel):
    memory: int
    tasks: int
    reminders: int
    conversations: int
    actions: int
    documents: int
    transcripts: int


class ExportPreview(BaseModel):
    counts: ExportCounts
    #: What the export deliberately leaves out, named so it can be shown.
    excludes: list[str]

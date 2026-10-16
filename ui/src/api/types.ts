/**
 * What the backend sends back — now its own description, not a second opinion.
 *
 * This was 354 lines of hand-written interfaces mirroring Python dataclasses:
 * maintained by hand, in a different language, with nothing checking the two
 * against each other. A renamed response field passed ruff, mypy, pytest and
 * `tsc`, and broke the UI at runtime. That was demonstrated rather than
 * argued — three fields were added to /health's response and the committed
 * OpenAPI snapshot did not move by one line, because every handler was
 * annotated `dict[str, Any]`.
 *
 * The handlers now carry Pydantic response models (backend/app/api/schemas.py),
 * so the schema describes the responses, `npm run api:types` generates them,
 * and this file is aliases over the result. The chain: a backend test holds
 * the snapshot equal to the live app, CI holds the generated types equal to
 * the snapshot, and `tsc` holds the components equal to the types. A field
 * renamed in Python is now a type error here.
 *
 * The aliases exist rather than importing `components["schemas"]["Health"]` at
 * every call site because that spelling is unreadable and because the names
 * are part of this client's own vocabulary. Adding one is one line; the shape
 * behind it is not ours to write any more.
 *
 * What is still hand-written, and why: the SSE event payloads in events.ts.
 * `/turn/stream` and `/events` are streams, so FastAPI has no response model
 * to describe and the schema says nothing about their frames. They are the
 * last unguarded piece of this contract.
 */

import type { components } from "../api-types.gen";

type Schemas = components["schemas"];

// -- the turn ------------------------------------------------------------

export type PendingAction = Schemas["PendingAction"];
export type SkillCall = Schemas["SkillCall"];
export type TurnResult = Schemas["TurnResult"];

// -- the Action Gate -----------------------------------------------------

/** A parked action as /actions/pending lists it — carries `expires_at`. */
export type ParkedAction = Schemas["ParkedAction"];
export type ActionRecord = Schemas["ActionRecord"];
export type UndoOutcome = Schemas["UndoOutcome"];
export type PreApproval = Schemas["PreApproval"];
export type SkillEntry = Schemas["SkillEntry"];

// -- memory --------------------------------------------------------------

export type MemoryFact = Schemas["MemoryFact"];

// -- scheduling ----------------------------------------------------------

export type Reminder = Schemas["ScheduledItem"];
export type RoutineSummary = Schemas["Routine"];
export type ShortcutSummary = Schemas["Shortcut"];
export type SequenceRun = Schemas["SequenceRun"];
export type Task = Schemas["Task"];
/** What POST /tasks answers with — fewer fields than a listed Task. */
export type CreatedTask = Schemas["CreatedTask"];

// -- documents -----------------------------------------------------------

export type IndexStatus = Schemas["IndexStatus"];
export type IndexedDocument = Schemas["IndexedDocument"];
export type ScanResult = Schemas["ScanResult"];
export type SemanticStatus = Schemas["SemanticStatus"];
export type DocumentHit = Schemas["DocumentHit"];

// -- connectors ----------------------------------------------------------

export type Account = Schemas["Account"];
export type Connectors = Schemas["Connectors"];
export type AddedAccount = Schemas["AddedAccount"];
export type AccountCheck = Schemas["AccountCheck"];
export type BriefingSection = Schemas["BriefingSection"];

/** The account kinds and providers the API accepts. */
export type AccountKind = "mail" | "calendar";
export type AccountProvider = "imap" | "caldav" | "ics";

/**
 * The fields a new account carries.
 *
 * Still hand-written, and deliberately: the schema types `fields` as an open
 * object because which keys are valid depends on the provider, and that rule
 * lives in connectors/setup.py. This is the client's own stricter view of it
 * — narrower than the API accepts, which is the safe direction.
 */
export interface AccountFields {
  label: string;
  host?: string;
  port?: number;
  username?: string;
  smtp_host?: string;
  smtp_port?: number;
  url?: string;
  writable?: boolean;
  enabled?: boolean;
}

// -- voice ---------------------------------------------------------------

export type VoiceStatus = Schemas["VoiceStatus"];
export type VoiceModel = Schemas["VoiceModel"];
export type SpeechShape = Schemas["SpeechShape"];
export type VoiceTurn = Schemas["VoiceTurn"];
export type CloneStatus = Schemas["CloneStatus"];
export type EngineProgress = Schemas["EngineProgress"];

// -- the avatar ----------------------------------------------------------

/** Live2D's runtime licence — the avatar stays inert until this is accepted. */
export type AvatarLicence = Schemas["AvatarLicence"];

// -- capture -------------------------------------------------------------

export type CaptureSummary = Schemas["CaptureSummary"];
export type CaptureStatus = Schemas["CaptureStatus"];
export type Transcript = Schemas["TranscriptDetail"];

// -- presence, health, focus --------------------------------------------

export type PresenceState = Schemas["PresenceState"];
export type Notification = Schemas["Notification"];
export type Health = Schemas["Health"];
export type FocusState = Schemas["FocusState"];

// -- settings, retention, diagnostics, export ---------------------------

export type Settings = Schemas["Settings"];
export type RetentionStatus = Schemas["RetentionWindow"];
export type LogSummary = Schemas["LogSummary"];
/** Recent turns in milliseconds, by stage. In memory only — see backend/app/timings.py. */
export type TimingSummary = Schemas["TimingSummary"];
export type TurnTiming = Schemas["TurnTiming"];
export type ExportPreview = Schemas["ExportPreview"];

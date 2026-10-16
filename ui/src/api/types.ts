/**
 * What the backend sends back.
 *
 * Hand-written, and that is the gap: the handlers return `dict[str, Any]`,
 * so the OpenAPI schema describes every response as an untyped object and
 * api-types.gen.ts cannot cover these. Closing it properly means response
 * models on the Python side -- see docs/NEXT-LEVEL-PLAN.md.
 *
 * Request bodies and paths *are* generated and checked; see client.ts.
 */

export interface PendingAction {
  action_id: string;
  skill: string;
  preview: string;
  reversible: boolean;
}

export interface TurnResult {
  reply: string;
  needs_confirmation: boolean;
  pending: PendingAction | null;
  skill_calls: { skill: string; status: string; message: string }[];
  error: string | null;
}

export interface PresenceState {
  state: "idle" | "listening" | "thinking" | "speaking" | "recording";
  emotion: string | null;
  recording: boolean;
  focus: boolean;
}

export interface MemoryFact {
  id: string;
  text: string;
  category: string;
  created_at: string | null;
}

export interface ActionRecord {
  id: string;
  batch_id: string;
  skill: string;
  preview: string;
  severity: string;
  status: string;
  can_undo: boolean;
  executed_at: string | null;
  error: string | null;
}

export interface VoiceStatus {
  enabled: boolean;
  input_enabled: boolean;
  output_enabled: boolean;
  microphone: boolean;
  models_ready: boolean;
  download_mb: number;
  stt: { model: string; installed: boolean; loaded: boolean };
  tts: { voice: string; installed: boolean; muted: boolean; available: string[] };
  wake: { phrase: string; enabled: boolean; installed: boolean; available: boolean };
}

export interface SpeechShape {
  /** False when speech output is muted, or no device would take it. */
  spoke: boolean;
  seconds: number;
  sample_rate: number;
  /** Loudness 0–1 at 30 samples per second. Drives the avatar's mouth. */
  envelope: number[];
}

export interface VoiceTurn {
  heard: string;
  confidence: number;
  reply: string;
  spoke: boolean;
  needs_confirmation: boolean;
  error: string | null;
}

export interface Notification {
  id: string;
  kind: string;
  title: string;
  body: string;
  at: string;
}

export interface Settings {
  current: {
    voice: Record<string, unknown>;
    persona: Record<string, unknown>;
    privacy?: Record<string, unknown>;
    documents?: Record<string, unknown>;
    system?: Record<string, unknown>;
    brain?: Record<string, unknown>;
  };
  /** section -> keys the API will accept. Anything absent is refused. */
  writable: Record<string, string[]>;
}

/** Live2D's runtime licence — the avatar stays inert until this is accepted. */
export interface AvatarLicence {
  licence_accepted: boolean;
  licence_accepted_at: string;
  licence_summary: string;
  licence_url: string;
}

export interface Health {
  ok: boolean;
  brain: {
    ok: boolean;
    model?: string;
    error?: string | null;
    /**
     * What Ollama actually reports, tags included (`qwen2.5:latest`).
     *
     * The picker needs this rather than a fixed list: the only models worth
     * offering are the ones already pulled, and offering one that is not
     * installed produces an app that cannot answer anything.
     */
    models?: string[];
    model_installed?: boolean;
    host?: string;
  };
  skills: number;
  persona: string;
  config_file: string | null;
  data_dir: string;
  privacy: { web_search: boolean; live_data: boolean; cloud_llm: boolean };
}

export type AccountKind = "mail" | "calendar";

/**
 * `ics` calendars carry no URL in `AccountFields`, deliberately.
 *
 * A Google iCal URL is what they call a "secret address" — anyone holding it
 * reads the whole calendar without logging in — so it is a credential and goes
 * through `setCredential`, into the OS store, never into the config file.
 */
export type AccountProvider = "imap" | "caldav" | "ics";

export interface AccountFields {
  label: string;
  host?: string;
  port?: number;
  username?: string;
  smtp_host?: string;
  smtp_port?: number;
  url?: string;
  writable?: boolean;
  // No password. Not omitted for brevity — it must not exist. See addAccount.
}

export interface SemanticStatus {
  enabled: boolean;
  model: string;
  model_installed: boolean;
  download_mb: number;
  chunks: number;
  embedded: number;
}

export interface RoutineSummary {
  id: string;
  label: string;
  next_fire_at: string | null;
  recurring: boolean;
  active: boolean;
  steps: string[];
  needs_approval: boolean;
}

export interface ShortcutSummary {
  id: string;
  label: string;
  steps: string[];
  needs_approval: boolean;
}

export interface RetentionStatus {
  conversation_days: number;
  history_days: number;
  conversation_turns: number;
  action_records: number;
}

export interface LogSummary {
  directory: string;
  files: { name: string; size_bytes: number; modified: number }[];
  total_bytes: number;
  exists: boolean;
}

/** One configured account. Contains no secret and never will. */
export interface Account {
  kind: string;
  label: string;
  provider: string;
  target: string;
  username: string;
  writable: boolean;
  enabled: boolean;
  credential_stored: boolean;
}

export interface Task {
  id: string;
  text: string;
  kind: string;
  tags: string[];
  due: string | null;
  done: boolean;
}

export interface Reminder {
  id: string;
  kind: string;
  label: string;
  next_fire_at: string | null;
  recurring: boolean;
  active: boolean;
}

export interface CloneStatus {
  /** Whether the XTTS package is present. It is a ~2GB optional dependency. */
  installed: boolean;
  /** A frozen build. Decides what advice "not installed" should carry. */
  packaged?: boolean;
  /** The downloadable sidecar is on disk. Not the same as being usable yet. */
  engine_installed?: boolean;
  /** XTTS-v2's own licence, separate from the consent switch. */
  licence_accepted?: boolean;
  licence_accepted_at?: string;
  enabled: boolean;
  consented: boolean;
  has_reference: boolean;
  reference_seconds: number;
  loaded: boolean;
  min_seconds: number;
  licence: string;
}

/** A download in flight. `total` is 0 until the server reports a length. */
export interface EngineProgress {
  installed: boolean;
  state: "idle" | "downloading" | "verifying" | "installing" | "installed" | "failed";
  received: number;
  total: number;
  error: string | null;
}

export interface CaptureSummary {
  summary: string;
  decisions: string[];
  actions: string[];
  /** True when the recording was too long to summarise whole. */
  truncated: boolean;
  error: string | null;
}

export interface Transcript {
  id: string;
  label: string;
  started_at: string | null;
  ended_at: string | null;
  /** Which inputs were captured. Microphone only, today. */
  sources: string[];
  minutes: number;
  words: number;
  summary: CaptureSummary | null;
  running: boolean;
  text?: string;
}

export interface CaptureStatus {
  recording: boolean;
  transcript_id: string | null;
  label: string;
  seconds: number;
  sources: string[];
  words: number;
  /** Why capture is degraded — no microphone, a failed source. */
  note: string;
}

export interface DocumentHit {
  file: string;
  path: string;
  section: string;
  /** "lease.pdf (Section 4)" — what the answer should be attributed to. */
  citation: string;
  text: string;
}

export interface IndexedDocument {
  file: string;
  path: string;
  chunks: number;
  indexed_at: string | null;
  error: string | null;
}

export interface IndexStatus {
  folders: string[];
  running: boolean;
  paused: boolean;
  /** Why a scan is holding off — on battery, machine busy. Null when it isn't. */
  deferred_because: string | null;
  last_scan: string | null;
  documents: number;
  chunks: number;
  failed: number;
  last_indexed: string | null;
  failures: { path: string; error: string }[];
}

export interface BriefingSection {
  name: string;
  lines: string[];
  /** Set when that one source failed. The rest of the briefing still arrives. */
  error: string | null;
  /** False when there is no account for this source at all — not the same as
   *  having nothing to report, and the screen must not conflate them. */
  configured: boolean;
}

export interface FocusState {
  active: boolean;
  minutes_left: number;
  closed_apps: string[];
  /** Apps running right now that starting a session would terminate. */
  would_close?: string[];
}

export interface Connectors {
  credential_store: { available: boolean; backend: string; detail: string };
  calendar: Account[];
  mail: Account[];
}

export interface AddedAccount {
  kind: string;
  label: string;
  provider: string;
  config_file: string;
  needs_secret: boolean;
  /** "password" for mail and CalDAV; "url" for an iCal calendar. */
  secret_kind: "password" | "url";
}

export interface ExportPreview {
  counts: {
    memory: number;
    tasks: number;
    reminders: number;
    conversations: number;
    actions: number;
    documents: number;
    transcripts: number;
  };
  /** What the export deliberately leaves out, named so it can be shown. */
  excludes: string[];
}

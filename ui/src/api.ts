/**
 * Backend client — REQ-1, REQ-24.
 *
 * Every call goes to the same FastAPI the CLI drives, so the UI cannot acquire
 * capabilities the terminal doesn't have, and the Action Gate sits in front of
 * exactly the same actions.
 *
 * The confirmation contract is visible in the types on purpose: `confirm` takes
 * an action id, never a boolean and never a "yes". There is no shape in this
 * file that could approve something the user was not shown.
 */

const BASE =
  (import.meta.env.VITE_KAI_API as string | undefined) ?? "http://127.0.0.1:8756";

export class ApiError extends Error {
  constructor(message: string, readonly status = 0) {
    super(message);
  }
}

async function request<T>(
  path: string,
  init?: RequestInit & { timeoutMs?: number },
): Promise<T> {
  let response: Response;
  const { timeoutMs, ...rest } = init ?? {};
  // Capturing an utterance blocks until the speaker stops, then transcribes.
  // The default fetch has no timeout at all, so a wedged microphone would hang
  // the button forever with no way back.
  const abort = timeoutMs ? AbortSignal.timeout(timeoutMs) : undefined;
  try {
    response = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      signal: abort,
      ...rest,
    });
  } catch {
    // A dead backend is the common case while developing, and the message the
    // user sees should say what to do about it (REQ-27).
    throw new ApiError(
      "Can't reach Kai. Is the backend running on port 8756?",
    );
  }
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* keep the status line */
    }
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

// -- types ----------------------------------------------------------------

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

// -- calls ----------------------------------------------------------------

export const api = {
  turn: (text: string, sessionId: string, pendingActionId?: string | null) =>
    request<TurnResult>("/turn", {
      method: "POST",
      body: JSON.stringify({
        text,
        session_id: sessionId,
        // Present only when answering a confirmation the user was just shown.
        pending_action_id: pendingActionId ?? null,
      }),
    }),

  /**
   * The same turn, delivered as it happens.
   *
   * `onStage` fires for the phases that cannot stream — routing, running a
   * skill — so the interface can say what it is waiting on instead of showing
   * a blank pause. `onDelta` fires with pieces of the reply.
   *
   * Resolves with the finished TurnResult, which is authoritative: the backend
   * revises the text after generation (memory receipts, the ungrounded-answer
   * guard), so a caller renders deltas as they arrive and then settles on this.
   *
   * fetch rather than EventSource: EventSource is GET-only, and the turn has a
   * body. The SSE framing is simple enough to read directly.
   */
  streamTurn: async (
    text: string,
    sessionId: string,
    handlers: {
      onDelta?: (piece: string) => void;
      onStage?: (stage: string) => void;
      signal?: AbortSignal;
    } = {},
    pendingActionId?: string | null,
  ): Promise<TurnResult> => {
    let response: Response;
    try {
      response = await fetch(`${BASE}/turn/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text,
          session_id: sessionId,
          pending_action_id: pendingActionId ?? null,
        }),
        signal: handlers.signal,
      });
    } catch {
      throw new ApiError("Can't reach Kai. Is the backend running on port 8756?");
    }
    if (!response.ok || !response.body) {
      throw new ApiError(`${response.status} ${response.statusText}`, response.status);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let result: TurnResult | null = null;

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE events are separated by a blank line. A chunk can split one in half,
      // so whatever follows the last separator stays in the buffer.
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() ?? "";

      for (const chunk of chunks) {
        const line = chunk.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        let event: Record<string, unknown>;
        try {
          event = JSON.parse(line.slice(5).trim());
        } catch {
          continue; // a frame we can't read is not worth failing the reply over
        }
        if (event.type === "delta") handlers.onDelta?.(String(event.text ?? ""));
        else if (event.type === "stage") handlers.onStage?.(String(event.stage ?? ""));
        else if (event.type === "done") result = event as unknown as TurnResult;
      }
    }

    if (!result) {
      // The stream ended without a verdict. Everything downstream assumes a
      // turn concluded one way or another, so say so rather than return a
      // half-state that looks like success.
      throw new ApiError("The reply ended before it finished.");
    }
    return result;
  },

  /** Approve one specific parked action. Takes an id, by design (REQ-24). */
  confirm: (actionId: string, sessionId: string) =>
    request<TurnResult>(
      `/actions/${encodeURIComponent(actionId)}/confirm?session_id=${encodeURIComponent(sessionId)}`,
      { method: "POST" },
    ),

  decline: (actionId: string, sessionId: string) =>
    request<TurnResult>(
      `/actions/${encodeURIComponent(actionId)}/decline?session_id=${encodeURIComponent(sessionId)}`,
      { method: "POST" },
    ),

  state: () => request<PresenceState>("/state"),
  health: () => request<Health>("/health"),

  connectors: () => request<Connectors>("/connectors"),

  /**
   * Add a mail or calendar account.
   *
   * `fields` carries account details only. There is no password parameter and
   * there must never be one: the backend refuses any field whose name looks
   * like a secret, and the password is typed at an OS prompt by `/connect`,
   * which writes it to the Windows Credential Manager. The response says so in
   * `next_step`.
   */
  addAccount: (kind: AccountKind, provider: AccountProvider, fields: AccountFields) =>
    request<AddedAccount>("/connectors/accounts", {
      method: "POST",
      body: JSON.stringify({ kind, provider, fields }),
    }),

  /**
   * Store an account's secret.
   *
   * It leaves this process once, over loopback, and the backend writes it to
   * the OS credential store. No endpoint returns it afterwards and it never
   * reaches kai.config.yaml. Callers should not keep it in state longer than
   * the submit.
   */
  setCredential: (kind: AccountKind, label: string, secret: string) =>
    request<{ stored: boolean; credential_ref: string }>(
      `/connectors/${kind}/${encodeURIComponent(label)}/credential`,
      { method: "PUT", body: JSON.stringify({ secret }) },
    ),

  /** Try the account and report whether it actually works. */
  checkAccount: (kind: AccountKind, label: string) =>
    request<{ ok: boolean; error?: string; unread?: number; events?: number }>(
      `/connectors/${kind}/${encodeURIComponent(label)}/check`,
      { method: "POST", timeoutMs: 60_000 },
    ),

  removeAccount: (kind: AccountKind, label: string) =>
    request<{ removed: string }>(
      `/connectors/accounts/${kind}/${encodeURIComponent(label)}`,
      { method: "DELETE" },
    ),

  memory: () => request<{ facts: MemoryFact[] }>("/memory"),
  forgetFact: (id: string) =>
    request<unknown>(`/memory/${encodeURIComponent(id)}`, { method: "DELETE" }),
  forgetAll: () => request<unknown>("/memory", { method: "DELETE" }),

  history: (limit = 25) =>
    request<{ history: ActionRecord[] }>(`/actions/history?limit=${limit}`),
  undo: (actionId: string) =>
    request<{ ok: boolean; message: string }>(
      `/actions/${encodeURIComponent(actionId)}/undo`,
      { method: "POST" },
    ),
  undoLast: () =>
    request<{ ok: boolean; message: string }>("/actions/undo", { method: "POST" }),

  wipe: () => request<{ removed: Record<string, number> }>("/privacy/wipe", {
    method: "POST",
  }),

  // -- voice --------------------------------------------------------------

  voiceStatus: () => request<VoiceStatus>("/voice/status"),

  voiceModels: () =>
    request<{ models: { name: string; kind: string; present: boolean; approx_mb: number }[];
              missing_mb: number; location: string }>("/voice/models"),

  downloadVoiceModels: (includeWake = false) =>
    request<{ downloaded: string[]; failed: { model: string; error: string }[]; ready: boolean }>(
      `/voice/models?include_wake=${includeWake}`,
      // Hundreds of megabytes over a slow connection; nothing shorter is safe.
      { method: "POST", timeoutMs: 30 * 60_000 },
    ),

  /** Capture one utterance and run it as a turn. Blocks while listening. */
  listen: () =>
    request<VoiceTurn>("/voice/listen", { method: "POST", timeoutMs: 120_000 }),

  speak: (text: string) =>
    request<SpeechShape>("/voice/speak", {
      method: "POST",
      body: JSON.stringify({ text }),
      timeoutMs: 120_000,
    }),

  // -- settings and notifications ------------------------------------------

  settings: () => request<Settings>("/settings"),

  saveSettings: (changes: Record<string, Record<string, unknown>>) =>
    request<{ changed: Record<string, unknown> }>("/settings", {
      method: "PATCH",
      body: JSON.stringify({ changes }),
    }),

  avatarLicence: () => request<AvatarLicence>("/avatar"),

  acceptAvatarLicence: (accepted: boolean) =>
    request<AvatarLicence>("/avatar/licence", {
      method: "POST",
      body: JSON.stringify({ accepted }),
    }),

  cloneStatus: () => request<CloneStatus>("/voice/clone"),

  engineProgress: () => request<EngineProgress>("/voice/clone/engine"),

  installEngine: () =>
    request<EngineProgress>("/voice/clone/engine", { method: "POST" }),

  removeEngine: () =>
    request<{ removed: boolean; installed: boolean }>("/voice/clone/engine", {
      method: "DELETE",
    }),

  setCloneConsent: (consent: boolean, enable = false) =>
    request<CloneStatus>("/voice/clone/consent", {
      method: "POST",
      body: JSON.stringify({ consent, enable }),
    }),

  /**
   * Upload the reference sample.
   *
   * Sent as multipart, so no Content-Type is set by hand — the browser has to
   * supply the boundary, and overriding it produces a body the server cannot
   * parse.
   */
  uploadCloneReference: async (file: File) => {
    const body = new FormData();
    body.append("file", file);
    const response = await fetch(`${BASE}/voice/clone/reference`, { method: "POST", body });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const parsed = await response.json();
        if (parsed?.detail) detail = String(parsed.detail);
      } catch {
        /* keep the status line */
      }
      throw new ApiError(detail, response.status);
    }
    return (await response.json()) as CloneStatus & { seconds: number };
  },

  forgetCloneReference: () =>
    request<CloneStatus & { removed: boolean }>("/voice/clone/reference", {
      method: "DELETE",
    }),

  captureStatus: () => request<CaptureStatus>("/capture/status"),

  startCapture: (label: string) =>
    request<CaptureStatus>(`/capture/start?label=${encodeURIComponent(label)}`, {
      method: "POST",
    }),

  // Stopping transcribes whatever was captured and then summarises it, both of
  // which are model work on a recording that may be an hour long.
  stopCapture: () =>
    request<Transcript>("/capture/stop", { method: "POST", timeoutMs: 30 * 60_000 }),

  transcripts: () => request<{ transcripts: Transcript[] }>("/capture/transcripts"),

  transcript: (id: string) =>
    request<Transcript & { text: string }>(`/capture/transcripts/${encodeURIComponent(id)}`),

  deleteTranscript: (id: string) =>
    request<{ removed: boolean }>(`/capture/transcripts/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),

  documentStatus: () => request<IndexStatus>("/documents/status"),

  documents: () => request<{ documents: IndexedDocument[] }>("/documents"),

  searchDocuments: (query: string, limit = 6) =>
    request<{ results: DocumentHit[] }>(
      `/documents/search?q=${encodeURIComponent(query)}&limit=${limit}`,
      { timeoutMs: 30_000 },
    ),

  // A full rescan reads every file in every indexed folder, so it is allowed
  // far longer than an ordinary call before being abandoned.
  reindex: () => request<Record<string, unknown>>("/documents/reindex", {
    method: "POST", timeoutMs: 30 * 60_000,
  }),

  clearIndex: () => request<{ cleared_documents: number }>("/documents/index", {
    method: "DELETE",
  }),

  briefing: () => request<{ sections: BriefingSection[] }>("/briefing", { timeoutMs: 60_000 }),

  focus: () => request<FocusState>("/focus"),

  startFocus: (minutes: number) =>
    request<FocusState & { message: string }>("/focus/start", {
      method: "POST",
      body: JSON.stringify({ minutes }),
    }),

  endFocus: () => request<{ active: boolean }>("/focus/end", { method: "POST" }),

  tasks: (includeDone = true) =>
    request<{ tasks: Task[] }>(`/tasks?include_done=${includeDone}`),

  addTask: (text: string) =>
    request<Task>("/tasks", { method: "POST", body: JSON.stringify({ text }) }),

  setTaskDone: (id: string, done: boolean) =>
    request<Task>(`/tasks/${encodeURIComponent(id)}?done=${done}`, { method: "PATCH" }),

  deleteTask: (id: string) =>
    request<{ id: string }>(`/tasks/${encodeURIComponent(id)}`, { method: "DELETE" }),

  reminders: () => request<{ reminders: Reminder[] }>("/reminders"),

  cancelReminder: (id: string) =>
    request<{ cancelled: string }>(`/reminders/${encodeURIComponent(id)}`, { method: "DELETE" }),

  /** Drain queued notifications. Destructive: each is handed out once. */
  notifications: () =>
    request<{ notifications: Notification[] }>("/notifications"),
};

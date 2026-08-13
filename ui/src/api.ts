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
  current: { voice: Record<string, unknown>; persona: Record<string, unknown> };
  writable: Record<string, string[]>;
}

export interface Health {
  ok: boolean;
  brain: { ok: boolean; model?: string; error?: string | null };
  skills: number;
  persona: string;
  config_file: string | null;
  data_dir: string;
  privacy: { web_search: boolean; live_data: boolean; cloud_llm: boolean };
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
    request<{ seconds: number }>("/voice/speak", {
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

  /** Drain queued notifications. Destructive: each is handed out once. */
  notifications: () =>
    request<{ notifications: Notification[] }>("/notifications"),
};

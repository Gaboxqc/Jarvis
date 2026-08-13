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

export type AccountKind = "mail" | "calendar";

/**
 * `ics` is deliberately absent.
 *
 * A Google iCal URL is what they call a "secret address" — anyone holding it
 * reads the whole calendar without logging in, so it is a credential, and the
 * backend refuses it. Leaving it out of the type means the UI cannot offer a
 * field that would end up carrying one.
 */
export type AccountProvider = "imap" | "caldav";

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
  /** The command that stores the password, e.g. "/connect mail gmail". */
  next_step: string;
  needs_password: boolean;
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

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

// Exported so events.ts can open the same origin with the same credentials
// rather than keeping a second copy of either.
export const BASE =
  (import.meta.env.VITE_KAI_API as string | undefined) ?? "http://127.0.0.1:8756";

export class ApiError extends Error {
  constructor(message: string, readonly status = 0) {
    super(message);
  }
}

/**
 * The bearer token every call carries.
 *
 * The backend refuses anything without it, because loopback and CORS are not
 * access control — a form on any web page can POST to 127.0.0.1 with no
 * preflight, and CORS only hides the reply. See backend/app/security.py.
 *
 * Two ways to get it, and neither of them is "bundle it":
 *
 *   in the desktop shell, from the Rust side, which reads the file the backend
 *       wrote and hands it over on request
 *   in the browser during development, from VITE_KAI_TOKEN, which vite.config.ts
 *       fills in from the same file — and only when serving, never when
 *       building, so a developer's token can never be compiled into a release
 *
 * Resolved once, and only a success is kept. A failure returns the empty string
 * rather than throwing — the call then comes back 401, which is a message about
 * the backend rather than an exception raised before any request was made — and
 * it is not cached, so a backend that was still starting up gets asked again on
 * the next call instead of leaving the window unauthenticated until it reloads.
 */
let pendingToken: Promise<string> | null = null;

function readToken(): Promise<string> {
  if (typeof window !== "undefined" && "__TAURI_INTERNALS__" in window) {
    return import("@tauri-apps/api/core").then(({ invoke }) =>
      invoke<string>("api_token"),
    );
  }
  return Promise.resolve((import.meta.env.VITE_KAI_TOKEN as string | undefined) ?? "");
}

export async function authHeaders(): Promise<Record<string, string>> {
  if (!pendingToken) {
    pendingToken = readToken().catch(() => "");
  }
  const value = await pendingToken;
  if (!value) pendingToken = null;
  return value ? { Authorization: `Bearer ${value}` } : {};
}

export async function request<T>(
  path: string,
  init?: RequestInit & { timeoutMs?: number },
): Promise<T> {
  let response: Response;
  const { timeoutMs, headers, ...rest } = init ?? {};
  // Capturing an utterance blocks until the speaker stops, then transcribes.
  // The default fetch has no timeout at all, so a wedged microphone would hang
  // the button forever with no way back.
  const abort = timeoutMs ? AbortSignal.timeout(timeoutMs) : undefined;
  try {
    response = await fetch(`${BASE}${path}`, {
      signal: abort,
      ...rest,
      // After the spread, not before: a caller's headers should add to these,
      // and the credentials are not a caller's to drop.
      headers: {
        "Content-Type": "application/json",
        ...(headers as Record<string, string> | undefined),
        ...(await authHeaders()),
      },
    });
  } catch {
    // A dead backend is the common case while developing, and the message the
    // user sees should say what to do about it (REQ-27).
    throw new ApiError(
      "Can't reach Kai. Is the backend running on port 8756?",
    );
  }
  if (response.status === 401 || response.status === 403) {
    // Distinguished from a dead backend on purpose: the port answered, so
    // "is it running" is the wrong question and sends people the wrong way.
    throw new ApiError(
      "Kai's backend refused this window. Restart the app so it can hand over " +
        "its access token again.",
      response.status,
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

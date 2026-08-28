/**
 * One connection instead of three timers — REQ-31.
 *
 * Presence polled every 2 seconds, notifications every 5, the prerequisite
 * check every 10, all three mounted for the life of the window: about 48
 * requests a minute with the app doing nothing, each one waking the backend's
 * threadpool and touching SQLite to establish that nothing had changed.
 *
 * This opens `/events` once and fans the frames out to whoever is listening.
 * See backend/app/events.py for what is sampled and how often.
 *
 * `fetch` rather than `EventSource`, for the same reason `streamTurn` uses it:
 * EventSource cannot send an Authorization header, and the API refuses calls
 * without one.
 *
 * The connection is also the liveness signal. A dead backend used to be found
 * by a poll that failed; now it is the stream ending, which is both faster and
 * one fewer request.
 */

import { authHeaders, BASE } from "./api";

export type KaiEvent =
  | { type: "hello" }
  | { type: "heartbeat" }
  | { type: "state"; state: string; emotion: string | null; recording: boolean; focus: boolean }
  | { type: "notifications"; items: { id: string; kind: string; title: string; body: string; at: string }[] }
  | { type: "health"; ok: boolean; error: string | null };

/** Whether the stream is up, so a component can say "backend unreachable". */
export type Connection = "connecting" | "open" | "down";

type Listener = (event: KaiEvent) => void;
type ConnectionListener = (state: Connection) => void;

const listeners = new Set<Listener>();
const connectionListeners = new Set<ConnectionListener>();

let connection: Connection = "connecting";
let running = false;
let abort: AbortController | null = null;

// Backoff, capped. A backend that is down stays down for as long as it takes
// someone to start it, and hammering it once a second in the meantime is the
// polling this exists to remove, wearing a different hat.
const FIRST_RETRY_MS = 500;
const MAX_RETRY_MS = 10_000;

function setConnection(next: Connection) {
  if (connection === next) return;
  connection = next;
  for (const listener of connectionListeners) listener(next);
}

function emit(event: KaiEvent) {
  for (const listener of listeners) listener(event);
}

async function readStream(signal: AbortSignal): Promise<void> {
  const response = await fetch(`${BASE}/events`, {
    headers: { ...(await authHeaders()) },
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`${response.status}`);
  }

  setConnection("open");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });

    // Frames are separated by a blank line, and a chunk can split one in half,
    // so whatever follows the last separator stays in the buffer.
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";
    for (const chunk of chunks) {
      const line = chunk.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      try {
        emit(JSON.parse(line.slice(5).trim()) as KaiEvent);
      } catch {
        // A malformed frame is not worth tearing the connection down for.
      }
    }
  }
}

async function run(): Promise<void> {
  let wait = FIRST_RETRY_MS;
  while (running) {
    abort = new AbortController();
    try {
      await readStream(abort.signal);
      // A clean end means the backend went away; reconnect as if it had failed.
      setConnection("down");
    } catch {
      if (!running) return;
      setConnection("down");
    }
    if (!running) return;
    await new Promise((resolve) => setTimeout(resolve, wait));
    wait = Math.min(wait * 2, MAX_RETRY_MS);
    setConnection("connecting");
  }
}

function start() {
  if (running) return;
  running = true;
  void run();
}

function stop() {
  running = false;
  abort?.abort();
  abort = null;
  setConnection("connecting");
}

/**
 * Listen for events. Returns the unsubscribe.
 *
 * The connection is shared and reference-counted: the first subscriber opens
 * it, the last one to leave closes it. Three components subscribing must not
 * mean three streams, which would put the notification drain into a race with
 * itself.
 */
export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  start();
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && connectionListeners.size === 0) stop();
  };
}

/** Listen for the connection going up and down. Returns the unsubscribe. */
export function subscribeConnection(listener: ConnectionListener): () => void {
  connectionListeners.add(listener);
  listener(connection);
  start();
  return () => {
    connectionListeners.delete(listener);
    if (listeners.size === 0 && connectionListeners.size === 0) stop();
  };
}

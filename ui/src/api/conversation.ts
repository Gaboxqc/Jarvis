/** The turn, and answering a confirmation. */

import { ApiError, BASE, authHeaders, request } from "./client";
import { body } from "./schema";
import type { TurnResult } from "./types";

export const conversation = {
  turn: (text: string, sessionId: string, pendingActionId?: string | null) =>
    request<TurnResult>("/turn", {
      method: "POST",
      body: body<"TurnRequest">({
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
        headers: { "Content-Type": "application/json", ...(await authHeaders()) },
        body: body<"TurnRequest">({
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
};

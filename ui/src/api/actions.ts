/** Action history and undo. */

import { request } from "./client";
import type { ActionRecord } from "./types";

export const actions = {
  history: (limit = 25) =>
    request<{ history: ActionRecord[] }>(`/actions/history?limit=${limit}`),

  undo: (actionId: string) =>
    request<{ ok: boolean; message: string }>(
      `/actions/${encodeURIComponent(actionId)}/undo`,
      { method: "POST" },
    ),

  undoLast: () =>
    request<{ ok: boolean; message: string }>("/actions/undo", { method: "POST" }),
};

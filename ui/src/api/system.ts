/** Presence, health and focus. */

import { request } from "./client";
import { body } from "./schema";
import type { FocusState, Health, Notification, PresenceState } from "./types";

export const system = {
  state: () => request<PresenceState>("/state"),

  health: () => request<Health>("/health"),

  focus: () => request<FocusState>("/focus"),

  startFocus: (minutes: number) =>
    request<FocusState & { message: string }>("/focus/start", {
      method: "POST",
      body: body<"FocusRequest">({ minutes }),
    }),

  endFocus: () => request<{ active: boolean }>("/focus/end", { method: "POST" }),

  /** Drain queued notifications. Destructive: each is handed out once. */
  notifications: () =>
    request<{ notifications: Notification[] }>("/notifications"),
};

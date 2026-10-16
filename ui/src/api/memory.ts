/** Durable facts. */

import { request } from "./client";
import type { MemoryFact } from "./types";

export const memory = {
  memory: () => request<{ facts: MemoryFact[] }>("/memory"),

  forgetFact: (id: string) =>
    request<unknown>(`/memory/${encodeURIComponent(id)}`, { method: "DELETE" }),

  forgetAll: () => request<unknown>("/memory", { method: "DELETE" }),
};

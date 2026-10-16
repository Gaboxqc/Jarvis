/** Meeting capture. */

import { request } from "./client";
import type { CaptureStatus, Transcript } from "./types";

export const capture = {
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
};

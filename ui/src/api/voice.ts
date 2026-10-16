/** Speech in and out, and the cloning engine. */

import { ApiError, BASE, authHeaders, request } from "./client";
import { body } from "./schema";
import type { CloneStatus, EngineProgress, SpeechShape, VoiceStatus, VoiceTurn } from "./types";

export const voice = {
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
      body: body<"SpeakRequest">({ text }),
      timeoutMs: 120_000,
    }),

  cloneStatus: () => request<CloneStatus>("/voice/clone"),

  setCloneConsent: (consent: boolean, enable = false) =>
    request<CloneStatus>("/voice/clone/consent", {
      method: "POST",
      body: body<"CloneConsentRequest">({ consent, enable }),
    }),

  acceptXttsLicence: (accepted: boolean) =>
    request<CloneStatus>("/voice/clone/licence", {
      method: "POST",
      body: body<"XttsLicenceRequest">({ accepted }),
    }),

  engineProgress: () => request<EngineProgress>("/voice/clone/engine"),

  installEngine: () =>
    request<EngineProgress>("/voice/clone/engine", { method: "POST" }),

  removeEngine: () =>
    request<{ removed: boolean; installed: boolean }>("/voice/clone/engine", {
      method: "DELETE",
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
    const response = await fetch(`${BASE}/voice/clone/reference`, {
      method: "POST",
      // Authorization only. Setting Content-Type here would override the
      // multipart boundary the browser generates and the server could not
      // parse the body.
      headers: await authHeaders(),
      body,
    });
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
};

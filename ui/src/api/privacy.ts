/** Settings, retention, diagnostics and the wipe. */

import { request } from "./client";
import { body } from "./schema";
import type { LogSummary, RetentionStatus, Settings } from "./types";

export const privacy = {
  // -- settings and notifications ------------------------------------------

  settings: () => request<Settings>("/settings"),

  saveSettings: (changes: Record<string, Record<string, unknown>>) =>
    request<{ changed: Record<string, unknown> }>("/settings", {
      method: "PATCH",
      body: body<"SettingsPatch">({ changes }),
    }),

  /** The retention window, and what is inside it right now. */
  retention: () => request<RetentionStatus>("/privacy/retention"),

  /**
   * Apply the window now.
   *
   * Shortening it has to visibly do something: saving "30 days" and being told
   * nothing happened until the next restart reads as a setting that did not take.
   */
  sweepRetention: () =>
    request<{ removed: Record<string, number> }>("/privacy/retention/sweep", {
      method: "POST",
    }),

  /** Where the log is and how big it is. Never its contents — see backend/app/diagnostics.py. */
  logs: () => request<LogSummary>("/diagnostics/logs"),

  /**
   * Record something that failed in the window rather than the backend.
   *
   * The updater, the WebView and the Live2D runtime are all reachable only
   * from here, and when one fails the message lives in a card until the user
   * clicks away. Writing it to the log means it can still be read afterwards
   * -- which is the difference between diagnosing a failure and asking
   * somebody what it said.
   *
   * Never throws. A reporting call that can fail the thing it is reporting on
   * would be worse than not reporting at all.
   */
  reportProblem: async (context: string, error: unknown, detail = ""): Promise<void> => {
    try {
      const name = error instanceof Error ? error.name : typeof error;
      const message = error instanceof Error ? error.message : String(error);
      const cause =
        error instanceof Error && error.cause !== undefined
          ? ` cause: ${String(error.cause)}`
          : "";
      await request<{ ok: boolean }>("/diagnostics/client-problem", {
        method: "POST",
        body: body<"ClientProblem">({ context, name, message, detail: detail + cause }),
      });
    } catch {
      // Nothing to do, and nowhere left to say it.
    }
  },

  /**
   * Open the log folder in Explorer.
   *
   * Only inside the desktop shell: a browser tab cannot open a folder, and
   * should not be able to. Resolves false when there is no shell to ask, so the
   * caller can show the path instead of a button that would do nothing.
   */
  revealLogs: async (): Promise<boolean> => {
    if (typeof window === "undefined" || !("__TAURI_INTERNALS__" in window)) return false;
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("open_log_folder");
    return true;
  },

  wipe: () => request<{ removed: Record<string, number> }>("/privacy/wipe", {
    method: "POST",
  }),
};

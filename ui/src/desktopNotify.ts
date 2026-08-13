/**
 * System notifications — REQ-9.
 *
 * A reminder that only renders inside the window is a reminder you miss by
 * having the window hidden, which is the normal state for something that lives
 * in the tray. This raises the OS notification instead.
 *
 * The same React code runs in a browser during development, where the Tauri API
 * does not exist, so the plugin is imported lazily and every failure degrades to
 * "no system notification". The in-window toast is always shown regardless, so
 * silence here costs presentation, never the reminder itself.
 */

type NotifyModule = typeof import("@tauri-apps/plugin-notification");

let cached: NotifyModule | null | undefined;

/** Whether this is the packaged desktop app rather than a browser tab. */
function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

async function load(): Promise<NotifyModule | null> {
  if (cached !== undefined) return cached;
  if (!inTauri()) {
    cached = null;
    return null;
  }
  try {
    cached = await import("@tauri-apps/plugin-notification");
  } catch {
    cached = null;
  }
  return cached;
}

/**
 * Ask for permission once, at a moment when the user has just done something.
 *
 * Deliberately not called on mount: a permission prompt that appears before the
 * app has shown its worth is the one people dismiss reflexively, and on Windows
 * a denied notification permission is awkward to reverse.
 */
export async function ensurePermission(): Promise<boolean> {
  const mod = await load();
  if (!mod) return false;
  try {
    if (await mod.isPermissionGranted()) return true;
    return (await mod.requestPermission()) === "granted";
  } catch {
    return false;
  }
}

/**
 * Raise a system notification. Resolves false when it could not be shown, which
 * is not an error worth surfacing — the toast in the window already carries it.
 */
export async function notifyDesktop(title: string, body: string): Promise<boolean> {
  const mod = await load();
  if (!mod) return false;
  try {
    if (!(await mod.isPermissionGranted())) return false;
    await mod.sendNotification({ title, body });
    return true;
  } catch {
    return false;
  }
}

/**
 * Start with Windows — REQ-29, REQ-32.
 *
 * An assistant reached by a global hotkey is only always-available if it is
 * running, and after a reboot it is not. This is the switch that fixes that.
 *
 * Off by default and asked for rather than assumed. Software that adds itself
 * to startup without being told is the kind people hunt through Task Manager
 * to remove, and doing it silently would undercut the rest of the app's
 * argument about being predictable.
 *
 * The same React runs in a browser during development where the plugin does
 * not exist, so the whole card hides itself rather than showing a control that
 * cannot work.
 */

import { useEffect, useState } from "react";
import type { Key } from "../i18n";

type Autostart = typeof import("@tauri-apps/plugin-autostart");

let cached: Autostart | null | undefined;

async function plugin(): Promise<Autostart | null> {
  if (cached !== undefined) return cached;
  if (typeof window === "undefined" || !("__TAURI_INTERNALS__" in window)) {
    cached = null;
    return null;
  }
  try {
    cached = await import("@tauri-apps/plugin-autostart");
  } catch {
    cached = null;
  }
  return cached;
}

export function Startup({ t }: { t: (key: Key) => string }) {
  const [available, setAvailable] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      const module = await plugin();
      if (!module || !alive) return;
      try {
        const on = await module.isEnabled();
        if (!alive) return;
        setAvailable(true);
        setEnabled(on);
      } catch {
        // Registry read refused: better to hide the switch than to show one
        // whose state is a guess.
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (!available) return null;

  async function toggle(next: boolean) {
    const module = await plugin();
    if (!module) return;
    setBusy(true);
    setError(null);
    try {
      if (next) await module.enable();
      else await module.disable();
      // Read it back rather than trusting the call: this writes to the
      // registry, and a switch that lies about whether it took is worse than
      // one that failed loudly.
      setEnabled(await module.isEnabled());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2 className="small muted">{t("startup.title")}</h2>
      <label className="spread">
        <span>{t("startup.enable")}</span>
        <input
          type="checkbox"
          checked={enabled}
          disabled={busy}
          onChange={(event) => void toggle(event.target.checked)}
        />
      </label>
      <p className="small muted">{t("startup.note")}</p>
      {error && (
        <p className="small" role="alert" style={{ color: "var(--danger)" }}>
          {error}
        </p>
      )}
    </section>
  );
}

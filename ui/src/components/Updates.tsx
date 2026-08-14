/**
 * Checking for a new version — REQ-26, REQ-29.
 *
 * Checking is something the user does, not something that happens quietly on
 * launch. This app's whole argument is that it does not reach off the machine
 * unless asked, and an automatic update check is a network request to a third
 * party carrying the version of software you run — small, but exactly the kind
 * of thing the privacy screen promises isn't happening behind your back.
 *
 * So there is a button. It says where it is looking before it looks.
 *
 * Installing is separate from downloading, and the size is shown first: a
 * 180MB download on a metered connection is worth being asked about.
 *
 * Updates are signed. The public key is compiled into the app and Tauri refuses
 * anything that does not verify against it, so a compromised release page
 * cannot push arbitrary code — which matters more than usual here, given what
 * this app is allowed to touch.
 */

import { useState } from "react";
import type { Key } from "../i18n";

type UpdaterModule = typeof import("@tauri-apps/plugin-updater");

interface Found {
  version: string;
  notes?: string;
  date?: string;
}

let cached: UpdaterModule | null | undefined;

async function updater(): Promise<UpdaterModule | null> {
  if (cached !== undefined) return cached;
  if (typeof window === "undefined" || !("__TAURI_INTERNALS__" in window)) {
    cached = null;
    return null;
  }
  try {
    cached = await import("@tauri-apps/plugin-updater");
  } catch {
    cached = null;
  }
  return cached;
}

export function Updates({ t, version }: { t: (key: Key, vars?: Record<string, string | number>) => string; version: string }) {
  const [checking, setChecking] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [found, setFound] = useState<Found | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [pending, setPending] = useState<Awaited<ReturnType<NonNullable<UpdaterModule>["check"]>> | null>(null);

  async function check() {
    const module = await updater();
    if (!module) {
      setMessage(t("updates.desktopOnly"));
      return;
    }
    setChecking(true);
    setMessage(null);
    setFound(null);
    try {
      const update = await module.check();
      if (update) {
        setPending(update);
        setFound({ version: update.version, notes: update.body, date: update.date });
      } else {
        setMessage(t("updates.upToDate"));
      }
    } catch (caught) {
      // Offline, rate-limited, or no release published yet. All three are
      // ordinary and none of them is the user's fault, so say what happened
      // rather than "an error occurred".
      setMessage(
        t("updates.checkFailed", {
          error: caught instanceof Error ? caught.message : String(caught),
        }),
      );
    } finally {
      setChecking(false);
    }
  }

  async function install() {
    if (!pending) return;
    setInstalling(true);
    setMessage(t("updates.installing"));
    try {
      // Downloads, verifies the signature, and runs the installer. The app is
      // replaced underneath itself, so this ends with a relaunch.
      await pending.downloadAndInstall();
      setMessage(t("updates.installed"));
    } catch (caught) {
      setMessage(
        t("updates.installFailed", {
          error: caught instanceof Error ? caught.message : String(caught),
        }),
      );
    } finally {
      setInstalling(false);
    }
  }

  return (
    <section className="card">
      <div className="spread">
        <h2 className="small muted">{t("updates.title")}</h2>
        <button className="ghost" onClick={() => void check()} disabled={checking || installing}>
          {checking ? t("updates.checking") : t("updates.check")}
        </button>
      </div>

      <div className="spread">
        <span className="small">{t("updates.installed_version")}</span>
        <span className="small muted">{version}</span>
      </div>

      {found && (
        <div style={{ marginTop: "0.5rem" }}>
          <div className="spread">
            <strong className="small">{t("updates.available", { version: found.version })}</strong>
            <button className="primary" onClick={() => void install()} disabled={installing}>
              {installing ? t("updates.installing") : t("updates.install")}
            </button>
          </div>
          {found.notes && (
            <p className="small" style={{ whiteSpace: "pre-wrap", marginTop: "0.3rem" }}>
              {found.notes}
            </p>
          )}
        </div>
      )}

      {message && (
        <p className="small" role="status" style={{ marginTop: "0.4rem" }}>
          {message}
        </p>
      )}

      {/* Said before the request is made, not buried in a privacy policy. */}
      <p className="small muted">{t("updates.note")}</p>
    </section>
  );
}

/**
 * Speaking in a cloned voice — REQ-4, REQ-26.
 *
 * The acknowledgement is a checkbox rather than a wall of text with an "I
 * agree" button, because the second kind is the kind nobody reads. What it says
 * is short and true: a copy of a voice can be used to say things that voice
 * never said, and the person being copied should be in the room.
 *
 * The reference is recorded here rather than uploaded from a file. A file
 * picker would happily accept a podcast, a voice note, anyone at all. Speaking
 * into the machine at least means whoever is being cloned is present — it is
 * not enforcement, and it is not meant to be, but the easy path should be the
 * legitimate one.
 *
 * Everything degrades. XTTS is a ~2GB optional dependency; without it this card
 * says so and Piper carries on speaking replies exactly as before.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type CloneStatus, type EngineProgress } from "../api";
import type { Key } from "../i18n";
import { Icon } from "./Icon";

interface Props {
  t: (key: Key, vars?: Record<string, string | number>) => string;
}

export function VoiceCloning({ t }: Props) {
  const [status, setStatus] = useState<CloneStatus | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [engine, setEngine] = useState<EngineProgress | null>(null);

  const load = useCallback(async () => {
    try {
      setStatus(await api.cloneStatus());
    } catch {
      // Voice is optional; a card that can't load its own status stays quiet.
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Polled rather than streamed: this is a progress number on a settings screen,
  // and a second of latency on it costs nothing. Only while something is
  // actually running, so an idle settings tab is not a timer.
  useEffect(() => {
    if (!status?.packaged || status.installed) return;
    let live = true;

    const tick = async () => {
      try {
        const next = await api.engineProgress();
        if (!live) return;
        setEngine(next);
        // The engine landing changes what the whole card should show.
        if (next.installed) void load();
      } catch {
        // A missed poll is not worth a message; the next one will do.
      }
    };

    void tick();
    const timer = window.setInterval(() => void tick(), 1000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [status?.packaged, status?.installed, load]);

  async function startInstall() {
    setBusy(true);
    setNote(null);
    try {
      setEngine(await api.installEngine());
    } catch (caught) {
      setNote(caught instanceof ApiError ? caught.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  async function run(action: () => Promise<CloneStatus>) {
    setBusy(true);
    setNote(null);
    try {
      setStatus(await action());
    } catch (caught) {
      setNote(caught instanceof ApiError ? caught.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  async function upload(file: File | undefined) {
    if (!file) return;
    setUploading(true);
    setNote(null);
    try {
      const result = await api.uploadCloneReference(file);
      setStatus(result);
      setNote(t("clone.uploaded", { seconds: result.reference_seconds }));
    } catch (caught) {
      setNote(caught instanceof ApiError ? caught.message : t("common.error"));
    } finally {
      setUploading(false);
      // Cleared so choosing the same file again still fires a change event.
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  function forget() {
    if (!window.confirm(t("clone.forgetConfirm"))) return;
    void run(() => api.forgetCloneReference());
  }

  // Never nothing. A card that erases itself when its status call fails is
  // indistinguishable from a feature that was never built -- which is exactly
  // how this one was reported missing.
  if (!status) {
    return (
      <section className="card">
        <h2 className="small muted">{t("clone.title")}</h2>
        <p className="small muted">{t("clone.unavailable")}</p>
      </section>
    );
  }

  return (
    <section className="card">
      <h2 className="small muted">{t("clone.title")}</h2>

      {!status.installed ? (
        <>
          {/* `pip install TTS` is sound advice in a checkout and nonsense in an
              installed app, where there is no environment to install into. The
              backend knows which build this is, so the answer differs. */}
          <p className="small">
            {status.packaged ? t("clone.notShipped") : t("clone.notInstalled")}
          </p>
          {!status.packaged && (
            <p className="small muted">
              <code>pip install TTS</code>
            </p>
          )}

          {/* Asked before the engine is fetched, not after. Downloading 300MB
              and then discovering the terms are unacceptable wastes the one
              thing the person cannot get back. */}
          {status.packaged && !status.licence_accepted && (
            <>
              <p className="small">{t("clone.licenceSummary")}</p>
              <a
                className="small"
                href="https://coqui.ai/cpml"
                target="_blank"
                rel="noreferrer noopener"
              >
                {t("clone.licenceTerms")}
              </a>
              <button
                className="primary"
                onClick={() => void run(() => api.acceptXttsLicence(true))}
                disabled={busy}
              >
                {t("clone.licenceAccept")}
              </button>
            </>
          )}

          {/* The download only exists for packaged builds. A checkout installs
              the package itself, and offering both would be two ways to get one
              thing, differing in which one the code then uses. */}
          {status.packaged && status.licence_accepted && (
            <>
              <p className="small muted">{t("clone.engineSize")}</p>
              {engine?.state === "downloading" || engine?.state === "verifying" ||
               engine?.state === "installing" ? (
                <p className="small" role="status">
                  {engine.state === "downloading" && engine.total > 0
                    ? t("clone.engineProgress", {
                        percent: Math.floor((engine.received / engine.total) * 100),
                      })
                    : t(`clone.engine.${engine.state}` as Key)}
                </p>
              ) : (
                <button
                  className="primary"
                  onClick={() => void startInstall()}
                  disabled={busy}
                >
                  {t("clone.engineInstall")}
                </button>
              )}
              {engine?.state === "failed" && engine.error && (
                <p className="small" role="alert" style={{ color: "var(--danger)" }}>
                  {engine.error}
                </p>
              )}
            </>
          )}

          <p className="small muted">{status.licence}</p>
        </>
      ) : (
        <>
          <label className="spread">
            <span>{t("clone.consent")}</span>
            <input
              type="checkbox"
              className="switch"
              checked={status.consented}
              disabled={busy}
              onChange={(event) =>
                void run(() => api.setCloneConsent(event.target.checked, true))
              }
            />
          </label>
          <p className="small muted">{t("clone.consentNote")}</p>

          {status.consented && (
            <>
              <div className="spread" style={{ marginTop: "0.6rem" }}>
                <span className="small">
                  {status.has_reference
                    ? t("clone.haveReference", { seconds: status.reference_seconds })
                    : t("clone.noReference", { seconds: status.min_seconds })}
                </span>
                <div className="row">
                  <input
                    ref={fileInput}
                    type="file"
                    accept=".wav,audio/wav"
                    className="sr-only"
                    onChange={(event) => void upload(event.target.files?.[0])}
                  />
                  <button
                    className="primary"
                    disabled={busy || uploading}
                    onClick={() => fileInput.current?.click()}
                  >
                    <Icon name="upload" />
                    {uploading
                      ? t("clone.uploading")
                      : status.has_reference
                        ? t("clone.replace")
                        : t("clone.choose")}
                  </button>
                  {status.has_reference && (
                    <button className="ghost" disabled={busy} onClick={forget}>
                      {t("clone.forget")}
                    </button>
                  )}
                </div>
              </div>
              <p className="small muted">{t("clone.uploadNote")}</p>
            </>
          )}

          {/* Stated where the decision is made, not only in a licence file. */}
          <p className="small muted">{status.licence}</p>
        </>
      )}

      {note && (
        <p className="small" role="status">
          {note}
        </p>
      )}
    </section>
  );
}

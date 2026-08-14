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

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type CloneStatus } from "../api";
import type { Key } from "../i18n";

interface Props {
  t: (key: Key, vars?: Record<string, string | number>) => string;
}

export function VoiceCloning({ t }: Props) {
  const [status, setStatus] = useState<CloneStatus | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);

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

  async function record() {
    setRecording(true);
    setNote(t("clone.recordingNow"));
    try {
      const result = await api.recordCloneReference(12);
      setStatus(result);
      setNote(t("clone.recorded", { seconds: result.reference_seconds }));
    } catch (caught) {
      setNote(caught instanceof ApiError ? caught.message : t("common.error"));
    } finally {
      setRecording(false);
    }
  }

  function forget() {
    if (!window.confirm(t("clone.forgetConfirm"))) return;
    void run(() => api.forgetCloneReference());
  }

  if (!status) return null;

  return (
    <section className="card">
      <h2 className="small muted">{t("clone.title")}</h2>

      {!status.installed ? (
        <>
          <p className="small">{t("clone.notInstalled")}</p>
          <p className="small muted">
            <code>pip install TTS</code>
          </p>
        </>
      ) : (
        <>
          <label className="spread">
            <span>{t("clone.consent")}</span>
            <input
              type="checkbox"
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
                  <button
                    className="primary"
                    disabled={busy || recording}
                    onClick={() => void record()}
                  >
                    {recording
                      ? t("clone.recording")
                      : status.has_reference
                        ? t("clone.rerecord")
                        : t("clone.record")}
                  </button>
                  {status.has_reference && (
                    <button className="ghost" disabled={busy} onClick={forget}>
                      {t("clone.forget")}
                    </button>
                  )}
                </div>
              </div>
              <p className="small muted">{t("clone.recordNote")}</p>
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

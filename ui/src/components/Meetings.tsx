/**
 * Recording and reading back meetings — REQ-19, REQ-26.
 *
 * Recording someone speaking is the most intrusive thing this app does, so the
 * screen is built around making the state impossible to mistake. While a
 * session runs, the card is outlined, the elapsed time and word count move, and
 * the button says Stop — there is no arrangement of this screen where a
 * recording is running and the screen looks idle.
 *
 * What is captured is stated rather than assumed. It is the microphone only:
 * system audio was tried and removed because the two libraries that can do it
 * on Windows either cannot, or bind COM to the importing thread and take the
 * interpreter down. So a call recorded here is your half of it, and the screen
 * says so instead of letting someone discover it afterwards from a transcript
 * with one voice in it.
 *
 * The summary is shown above the transcript because it is what anyone actually
 * wants; the full text is behind a toggle for when the summary is wrong.
 */

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type CaptureStatus, type Transcript } from "../api";
import type { Key } from "../i18n";
import { Icon } from "./Icon";

interface Props {
  t: (key: Key, vars?: Record<string, string | number>) => string;
}

export function Meetings({ t }: Props) {
  const [status, setStatus] = useState<CaptureStatus | null>(null);
  const [transcripts, setTranscripts] = useState<Transcript[]>([]);
  const [open, setOpen] = useState<(Transcript & { text?: string }) | null>(null);
  const [showText, setShowText] = useState(false);
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [state, list] = await Promise.all([api.captureStatus(), api.transcripts()]);
      setStatus(state);
      setTranscripts(list.transcripts);
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t("common.error"));
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  // While recording, the elapsed time and word count are the evidence that it
  // is still working. A frozen counter would be indistinguishable from a
  // session that died quietly.
  useEffect(() => {
    if (!status?.recording) return;
    const timer = setInterval(() => {
      void api.captureStatus().then(setStatus).catch(() => undefined);
    }, 3000);
    return () => clearInterval(timer);
  }, [status?.recording]);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      setStatus(await api.startCapture(label.trim() || t("meetings.defaultLabel")));
      setLabel("");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    setBusy(true);
    setError(null);
    try {
      const finished = await api.stopCapture();
      setOpen(finished);
      setShowText(false);
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  async function show(transcript: Transcript) {
    if (open?.id === transcript.id) {
      setOpen(null);
      return;
    }
    setShowText(false);
    try {
      setOpen(await api.transcript(transcript.id));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t("common.error"));
    }
  }

  async function remove(transcript: Transcript) {
    // A recording of people talking, and the only copy. Worth a question.
    if (!window.confirm(t("meetings.deleteConfirm", { label: transcript.label }))) return;
    try {
      await api.deleteTranscript(transcript.id);
      if (open?.id === transcript.id) setOpen(null);
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t("common.error"));
    }
  }

  const elapsed = (seconds: number) => {
    const total = Math.floor(seconds);
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
  };

  const when = (iso: string | null) =>
    iso
      ? new Date(iso).toLocaleString(undefined, {
          day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
        })
      : "—";

  return (
    <div className="view">
      <h1>{t("meetings.title")}</h1>
      {error && <div className="banner">{error}</div>}

      <section className={status?.recording ? "card recording-now" : "card"}>
        {status?.recording ? (
          <>
            <div className="spread">
              <div>
                <div className="row" style={{ gap: "0.5rem" }}>
                  <span className="dot recording" />
                  <strong>{t("meetings.recordingNow", { label: status.label })}</strong>
                </div>
                <div className="small muted">
                  {t("meetings.progress", {
                    time: elapsed(status.seconds),
                    words: status.words,
                  })}
                </div>
              </div>
              <button className="danger" onClick={() => void stop()} disabled={busy}>
                {busy ? t("meetings.stopping") : t("meetings.stop")}
              </button>
            </div>
            {status.note && <p className="small muted">{status.note}</p>}
          </>
        ) : (
          <>
            <div className="row">
              <label className="sr-only" htmlFor="meeting-label">
                {t("meetings.labelPlaceholder")}
              </label>
              <input
                id="meeting-label"
                value={label}
                onChange={(event) => setLabel(event.target.value)}
                placeholder={t("meetings.labelPlaceholder")}
                style={{ flex: 1 }}
              />
              <button className="primary" onClick={() => void start()} disabled={busy}>
                <Icon name="mic" />
                {t("meetings.start")}
              </button>
            </div>
            {/* Said before recording, not discovered afterwards. */}
            <p className="small muted">{t("meetings.micOnlyNote")}</p>
          </>
        )}
      </section>

      <section className="card">
        <h2 className="small muted">{t("meetings.past")}</h2>
        {transcripts.length === 0 ? (
          <p className="small muted">{t("meetings.none")}</p>
        ) : (
          <ul className="plain">
            {transcripts.map((transcript) => (
              <li key={transcript.id}>
                <div className="spread">
                  <button
                    className="ghost"
                    style={{ textAlign: "left", flex: 1, border: 0, background: "none" }}
                    onClick={() => void show(transcript)}
                    aria-expanded={open?.id === transcript.id}
                  >
                    <div>{transcript.label}</div>
                    <div className="small muted">
                      {when(transcript.started_at)} ·{" "}
                      {t("meetings.length", {
                        minutes: transcript.minutes,
                        words: transcript.words,
                      })}
                    </div>
                  </button>
                  <button className="ghost" onClick={() => remove(transcript)}>
                    <Icon name="trash" />
                    <span className="sr-only">{t("meetings.delete")}</span>
                  </button>
                </div>

                {open?.id === transcript.id && (
                  <div style={{ marginTop: "0.4rem" }}>
                    {open.summary?.error ? (
                      <p className="small" style={{ color: "var(--danger)" }}>
                        {t("meetings.summaryFailed", { error: open.summary.error })}
                      </p>
                    ) : (
                      <>
                        {open.summary?.summary && (
                          <p className="small" style={{ whiteSpace: "pre-wrap" }}>
                            {open.summary.summary}
                          </p>
                        )}
                        {open.summary?.decisions?.length ? (
                          <>
                            <div className="small muted">{t("meetings.decisions")}</div>
                            <ul className="plain">
                              {open.summary.decisions.map((line, i) => (
                                <li key={`d${i}`} className="small">{line}</li>
                              ))}
                            </ul>
                          </>
                        ) : null}
                        {open.summary?.actions?.length ? (
                          <>
                            <div className="small muted">{t("meetings.actions")}</div>
                            <ul className="plain">
                              {open.summary.actions.map((line, i) => (
                                <li key={`a${i}`} className="small">{line}</li>
                              ))}
                            </ul>
                          </>
                        ) : null}
                        {open.summary?.truncated && (
                          <p className="small muted">{t("meetings.truncated")}</p>
                        )}
                      </>
                    )}

                    {/* The summary is what anyone wants; the transcript is for
                        when the summary is wrong. */}
                    <button className="ghost" onClick={() => setShowText(!showText)}>
                      {showText ? t("meetings.hideText") : t("meetings.showText")}
                    </button>
                    {showText && (
                      <p className="small" style={{ whiteSpace: "pre-wrap" }}>
                        {open.text || t("meetings.noText")}
                      </p>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
        <p className="small muted">{t("meetings.localNote")}</p>
      </section>
    </div>
  );
}

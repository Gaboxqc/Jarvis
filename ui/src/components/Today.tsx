/**
 * The day in front of you — REQ-11, REQ-23.
 *
 * The briefing gathers calendar, mail, reminders and tasks in one pass. Each
 * source is fetched with its own timeout and can fail on its own, so a section
 * that could not be reached says so and the rest still arrives — a briefing
 * that showed nothing because one mail server was slow would be worse than no
 * briefing at all.
 *
 * Focus lives on this screen rather than in Settings because it is not a
 * setting: it is something you do to the next half hour, which is what this
 * screen is about.
 *
 * Starting a session closes the apps configured as distracting, and unsaved
 * work in them is lost. So the button names them first. The backend reports
 * what is actually running, which means the warning appears only when there is
 * something to warn about — a dialog that cries wolf on every click is one
 * people learn to dismiss without reading.
 */

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type BriefingSection, type FocusState } from "../api";
import type { Key } from "../i18n";

interface Props {
  t: (key: Key, vars?: Record<string, string | number>) => string;
}

const DURATIONS = [25, 45, 60];

export function Today({ t }: Props) {
  const [sections, setSections] = useState<BriefingSection[] | null>(null);
  const [focus, setFocus] = useState<FocusState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadFocus = useCallback(async () => {
    try {
      setFocus(await api.focus());
    } catch {
      // The briefing is the point of this screen; focus is an extra.
    }
  }, []);

  const load = useCallback(async () => {
    setError(null);
    try {
      const result = await api.briefing();
      setSections(result.sections);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t("common.error"));
      setSections([]);
    }
    await loadFocus();
  }, [t, loadFocus]);

  useEffect(() => {
    void load();
  }, [load]);

  // While a session runs the only number that changes is the countdown, so poll
  // gently rather than rebuilding the whole briefing.
  useEffect(() => {
    if (!focus?.active) return;
    const timer = setInterval(() => void loadFocus(), 30_000);
    return () => clearInterval(timer);
  }, [focus?.active, loadFocus]);

  async function start(minutes: number) {
    const closing = focus?.would_close ?? [];
    if (closing.length > 0) {
      const ok = window.confirm(
        t("today.focusConfirm", { count: closing.length, apps: closing.join(", ") }),
      );
      if (!ok) return;
    }
    setBusy(true);
    try {
      setFocus(await api.startFocus(minutes));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    setBusy(true);
    try {
      await api.endFocus();
      await loadFocus();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="view">
      <div className="spread">
        <h1>{t("today.title")}</h1>
        <button className="ghost" onClick={() => void load()} disabled={busy}>
          {t("today.refresh")}
        </button>
      </div>

      {error && <div className="banner">{error}</div>}

      <section className="card">
        <h2 className="small muted">{t("today.focus")}</h2>
        {focus?.active ? (
          <div className="spread">
            <div>
              <div>{t("today.focusActive", { minutes: focus.minutes_left })}</div>
              {focus.closed_apps.length > 0 && (
                <div className="small muted">
                  {t("today.focusClosed", { apps: focus.closed_apps.join(", ") })}
                </div>
              )}
            </div>
            <button className="ghost" onClick={() => void stop()} disabled={busy}>
              {t("today.focusEnd")}
            </button>
          </div>
        ) : (
          <>
            <div className="row">
              {DURATIONS.map((minutes) => (
                <button
                  key={minutes}
                  className="primary"
                  disabled={busy}
                  onClick={() => void start(minutes)}
                >
                  {t("today.focusStart", { minutes })}
                </button>
              ))}
            </div>
            <p className="small muted">
              {focus?.would_close?.length
                ? t("today.focusWillClose", { apps: focus.would_close.join(", ") })
                : t("today.focusNote")}
            </p>
          </>
        )}
      </section>

      {sections === null ? (
        <p className="small muted">{t("common.loading")}…</p>
      ) : sections.length === 0 && !error ? (
        <p className="small muted">{t("today.empty")}</p>
      ) : (
        sections.map((section) => (
          <section className="card" key={section.name}>
            <h2 className="small muted">{t(`today.section.${section.name}` as Key)}</h2>
            {section.error ? (
              // Named, not hidden. A section that quietly showed nothing would
              // read as "you have no meetings" when it means "I couldn't look".
              <p className="small" style={{ color: "var(--danger)" }}>
                {t("today.sectionFailed", { error: section.error })}
              </p>
            ) : !section.configured ? (
              // "Nothing here" would read as "you have no meetings" when it
              // means "there is no calendar to look at".
              <p className="small muted">{t("today.sectionUnconfigured")}</p>
            ) : section.lines.length === 0 ? (
              <p className="small muted">{t("today.sectionEmpty")}</p>
            ) : (
              <ul className="plain">
                {section.lines.map((line, index) => (
                  <li key={`${section.name}-${index}`}>{line}</li>
                ))}
              </ul>
            )}
          </section>
        ))
      )}
    </div>
  );
}

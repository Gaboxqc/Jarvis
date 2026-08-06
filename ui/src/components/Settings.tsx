/**
 * Settings and privacy — REQ-26, REQ-28.
 *
 * The egress switches are shown read-only on purpose. They live in
 * `kai.config.yaml`, which is the single human-editable source of truth, and a
 * UI toggle that silently disagreed with the file would make the file
 * untrustworthy. This screen's job is to make the current answer visible, and
 * to say where to change it.
 *
 * The wipe is the one destructive control, and it types the same confirmation
 * the CLI asks for.
 */

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type Health } from "../api";
import type { Key, Lang } from "../i18n";

interface Props {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (key: Key, vars?: Record<string, string | number>) => string;
}

export function Settings({ lang, setLang, t }: Props) {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setHealth(await api.health());
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t("common.error"));
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  async function wipe() {
    if (!window.confirm(t("settings.wipeConfirm"))) return;
    try {
      const result = await api.wipe();
      const total = Object.values(result.removed).reduce((sum, n) => sum + n, 0);
      setNote(t("settings.wiped", { count: total }));
    } catch (caught) {
      setNote(caught instanceof ApiError ? caught.message : t("common.error"));
    }
  }

  const yesNo = (value: boolean) => (value ? t("common.on") : t("common.off"));

  return (
    <div className="view">
      <h1>{t("settings.title")}</h1>
      {error && <div className="banner">{error}</div>}

      <section className="card">
        <h2 className="small muted">{t("settings.language")}</h2>
        <label className="sr-only" htmlFor="lang-select">
          {t("settings.language")}
        </label>
        <select
          id="lang-select"
          value={lang}
          onChange={(event) => setLang(event.target.value as Lang)}
        >
          <option value="en">English</option>
          <option value="es">Español</option>
        </select>
      </section>

      {health && (
        <>
          <section className="card">
            <div className="spread">
              <span>{t("settings.brain")}</span>
              <span className="muted small">
                {health.brain.ok
                  ? health.brain.model
                  : (health.brain.error ?? t("state.offline"))}
              </span>
            </div>
            <div className="spread">
              <span>{t("settings.skills")}</span>
              <span className="muted small">{health.skills}</span>
            </div>
            <div className="spread">
              <span>{t("settings.config")}</span>
              <span className="muted small">{health.config_file ?? "—"}</span>
            </div>
            <div className="spread">
              <span>{t("settings.data")}</span>
              <span className="muted small">{health.data_dir}</span>
            </div>
          </section>

          <section className="card">
            <h2 className="small muted">{t("settings.egress")}</h2>
            <div className="spread">
              <span>{t("settings.webSearch")}</span>
              <span className="muted small">{yesNo(health.privacy.web_search)}</span>
            </div>
            <div className="spread">
              <span>{t("settings.liveData")}</span>
              <span className="muted small">{yesNo(health.privacy.live_data)}</span>
            </div>
            <div className="spread">
              <span>{t("settings.cloudLlm")}</span>
              <span className="muted small">{yesNo(health.privacy.cloud_llm)}</span>
            </div>
            <p className="small muted">{t("settings.egressNote")}</p>
          </section>
        </>
      )}

      <section className="card danger-zone">
        <h2 className="small">{t("settings.danger")}</h2>
        <p className="small muted">{t("settings.dangerNote")}</p>
        {note && (
          <p className="small" role="status">
            {note}
          </p>
        )}
        <button className="danger" onClick={wipe}>
          {t("settings.wipe")}
        </button>
      </section>
    </div>
  );
}

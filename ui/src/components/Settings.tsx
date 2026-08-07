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
import type { Voice } from "../useVoice";

interface Props {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (key: Key, vars?: Record<string, string | number>) => string;
  voice: Voice;
}

export function Settings({ lang, setLang, t, voice }: Props) {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  async function downloadModels() {
    setDownloading(true);
    try {
      await api.downloadVoiceModels(Boolean(voice.status?.wake.enabled));
      await voice.refresh();
    } catch (caught) {
      setNote(caught instanceof ApiError ? caught.message : t("common.error"));
    } finally {
      setDownloading(false);
    }
  }

  function toggle(key: string, value: boolean) {
    void api
      .saveSettings({ voice: { [key]: value } })
      .then(() => voice.refresh())
      .catch((caught) =>
        setNote(caught instanceof ApiError ? caught.message : t("common.error")),
      );
  }

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
        <h2 className="small muted">{t("settings.voice")}</h2>

        {voice.status && !voice.status.models_ready && (
          <div className="spread">
            <span className="small">{t("settings.voiceModels")}</span>
            <button className="primary" onClick={downloadModels} disabled={downloading}>
              {downloading
                ? t("settings.voiceDownloading")
                : t("settings.voiceDownload", { mb: voice.status.download_mb })}
            </button>
          </div>
        )}

        {voice.status?.models_ready && (
          <>
            <label className="spread">
              <span>{t("settings.voiceEnabled")}</span>
              <input
                type="checkbox"
                checked={voice.status.enabled}
                disabled={voice.busy}
                onChange={(event) => toggle("enabled", event.target.checked)}
              />
            </label>
            <label className="spread">
              <span>{t("settings.voiceInput")}</span>
              <input
                type="checkbox"
                checked={voice.status.input_enabled}
                disabled={voice.busy || !voice.status.enabled}
                onChange={(event) => toggle("input_enabled", event.target.checked)}
              />
            </label>
            <label className="spread">
              <span>{t("settings.voiceOutput")}</span>
              <input
                type="checkbox"
                checked={voice.status.output_enabled}
                disabled={voice.busy || !voice.status.enabled}
                onChange={(event) => toggle("output_enabled", event.target.checked)}
              />
            </label>
            <label className="spread">
              <span>
                {t("settings.voiceWake")}{" "}
                <span className="muted">({voice.status.wake.phrase})</span>
              </span>
              <input
                type="checkbox"
                checked={voice.status.wake.enabled}
                disabled={voice.busy || !voice.status.enabled || !voice.status.wake.installed}
                onChange={(event) => toggle("wake_enabled", event.target.checked)}
              />
            </label>
            <p className="small muted">{t("settings.voiceWakeNote")}</p>
            <div className="spread">
              <span className="small muted">{t("settings.voiceModels")}</span>
              <span className="small muted">
                {voice.status.stt.model} · {voice.status.tts.voice}
              </span>
            </div>
          </>
        )}

        {voice.status && !voice.status.microphone && (
          <p className="small muted">{t("settings.voiceNoMic")}</p>
        )}
        <p className="small muted">{t("settings.voiceLocal")}</p>
      </section>

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

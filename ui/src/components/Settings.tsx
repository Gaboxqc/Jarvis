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
import { api, ApiError, type Health, type Settings as SettingsData } from "../api";
import type { Key, Lang } from "../i18n";
import type { Voice } from "../useVoice";
import { Accounts } from "./Accounts";
import { FolderList } from "./FolderList";
import { Startup } from "./Startup";
import { Updates } from "./Updates";
import { VoiceCloning } from "./VoiceCloning";

interface Props {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (key: Key, vars?: Record<string, string | number>) => string;
  voice: Voice;
}

// Injected by Vite from package.json, so it cannot drift from the build.
const APP_VERSION = __APP_VERSION__;

export function Settings({ lang, setLang, t, voice }: Props) {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  // Kept apart from `note` on purpose. A shared one rendered folder errors in
  // the "Delete all local data" card at the foot of the page — 400px below the
  // control that produced them, and off-screen at the default window size, so
  // clicking Add on a bad path looked like nothing happening at all.
  const [configNote, setConfigNote] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [config, setConfig] = useState<SettingsData["current"] | null>(null);
  const [saving, setSaving] = useState(false);

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
      const [h, s] = await Promise.all([api.health(), api.settings()]);
      setHealth(h);
      setConfig(s.current);
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t("common.error"));
    }
  }, [t]);

  /** Save one nested patch and reload, so the screen shows what actually landed. */
  async function save(patch: Record<string, Record<string, unknown>>) {
    setSaving(true);
    setConfigNote(null);
    try {
      await api.saveSettings(patch);
      await load();
      await voice.refresh();
    } catch (caught) {
      // Rethrown so whichever control made the change can show the reason next
      // to itself. Only the egress switches have nowhere else to put it —
      // FolderList renders its own, and setting this too showed the same
      // message twice, once of them 500px from the control that caused it.
      if ("privacy" in patch) {
        setConfigNote(caught instanceof ApiError ? caught.message : t("common.error"));
      }
      throw caught;
    } finally {
      setSaving(false);
    }
  }

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

      <VoiceCloning t={t} />

      <Accounts t={t} configFile={health?.config_file ?? null} />

      <Updates t={t} version={APP_VERSION} />

      <Startup t={t} />

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
            {configNote && (
              <p className="small" role="alert" style={{ color: "var(--danger)" }}>
                {configNote}
              </p>
            )}
            {config && (
              <>
                <label className="spread">
                  <span>{t("settings.webSearch")}</span>
                  <input
                    type="checkbox"
                    checked={Boolean(config.privacy?.allow_web_search)}
                    disabled={saving}
                    onChange={(e) =>
                      void save({ privacy: { allow_web_search: e.target.checked } }).catch(
                        () => undefined,
                      )
                    }
                  />
                </label>
                <label className="spread">
                  <span>{t("settings.liveData")}</span>
                  <input
                    type="checkbox"
                    checked={Boolean(config.privacy?.allow_live_data)}
                    disabled={saving}
                    onChange={(e) =>
                      void save({ privacy: { allow_live_data: e.target.checked } }).catch(
                        () => undefined,
                      )
                    }
                  />
                </label>
                <label className="spread">
                  <span>{t("settings.cloudLlm")}</span>
                  <input
                    type="checkbox"
                    checked={Boolean(config.privacy?.allow_cloud_llm)}
                    disabled={saving}
                    onChange={(e) =>
                      void save({ privacy: { allow_cloud_llm: e.target.checked } }).catch(
                        () => undefined,
                      )
                    }
                  />
                </label>
              </>
            )}
            <p className="small muted">{t("settings.egressNote")}</p>
          </section>

          {config && (
            <section className="card">
              <h2 className="small muted">{t("settings.files")}</h2>
              <FolderList
                t={t}
                busy={saving}
                label={t("settings.allowedRoots")}
                hint={t("settings.allowedRootsNote")}
                folders={(config.system?.allowed_roots as string[]) ?? []}
                onChange={(folders) => save({ system: { allowed_roots: folders } })}
              />
              <FolderList
                t={t}
                busy={saving}
                label={t("settings.indexedFolders")}
                hint={t("settings.indexedFoldersNote")}
                folders={(config.documents?.indexed_folders as string[]) ?? []}
                onChange={(folders) => save({ documents: { indexed_folders: folders } })}
              />
            </section>
          )}
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

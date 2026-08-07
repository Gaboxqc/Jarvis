/**
 * Shell — REQ-1, REQ-28, REQ-32.
 *
 * Four sections, a presence indicator, and nothing else. The chat stays mounted
 * while other tabs are shown so a conversation isn't thrown away by looking at
 * the history, and — more importantly — so a pending confirmation cannot be
 * lost by navigating away from it.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Chat } from "./components/Chat";
import { History } from "./components/History";
import { Memory } from "./components/Memory";
import { Prerequisite } from "./components/Prerequisite";
import { Presence } from "./components/Presence";
import { Settings } from "./components/Settings";
import { Notifications } from "./components/Notifications";
import { detectLang, translate, type Key, type Lang } from "./i18n";
import { useVoice } from "./useVoice";

type Tab = "chat" | "memory" | "history" | "settings";
const TABS: Tab[] = ["chat", "memory", "history", "settings"];

export function App() {
  const [tab, setTab] = useState<Tab>("chat");
  const [lang, setLang] = useState<Lang>(detectLang);
  const [busy, setBusy] = useState(false);
  const voice = useVoice();

  const t = useMemo(
    () => (key: Key, vars?: Record<string, string | number>) => translate(lang, key, vars),
    [lang],
  );

  useEffect(() => {
    localStorage.setItem("kai.lang", lang);
    document.documentElement.lang = lang;
  }, [lang]);

  // Ctrl+1..4 switches section without reaching for the mouse (REQ-28).
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (!event.ctrlKey || event.altKey || event.metaKey) return;
      const index = Number(event.key) - 1;
      if (index >= 0 && index < TABS.length) {
        event.preventDefault();
        setTab(TABS[index]);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const onBusyChange = useCallback((value: boolean) => setBusy(value), []);

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to content
      </a>

      <header className="bar">
        <span className="brand">{t("app.title")}</span>
        <Presence busy={busy} lang={lang} t={t} />
        <button
          type="button"
          className="speaker"
          aria-pressed={voice.speaks}
          disabled={voice.busy}
          title={voice.speaks ? t("voice.speakOn") : t("voice.speakOff")}
          onClick={() => void voice.setSpeaks(!voice.speaks).catch(() => undefined)}
        >
          <span aria-hidden="true">{voice.speaks ? "▶" : "□"}</span>
          <span className="sr-only">
            {voice.speaks ? t("voice.speakOn") : t("voice.speakOff")}
          </span>
        </button>
        <nav className="tabs" aria-label={t("nav.label")}>
          {TABS.map((name) => (
            <button
              key={name}
              onClick={() => setTab(name)}
              aria-current={tab === name ? "page" : undefined}
            >
              {t(`nav.${name}` as Key)}
            </button>
          ))}
        </nav>
      </header>

      <main id="main" tabIndex={-1}>
        {/* The one prerequisite the installer cannot bundle (REQ-29). */}
        <div className="view">
          <Prerequisite t={t} />
        </div>

        {/* Kept mounted: navigating away must not discard a pending confirmation. */}
        <Notifications t={t} />

        <div hidden={tab !== "chat"}>
          <Chat lang={lang} t={t} onBusyChange={onBusyChange} voice={voice} />
        </div>
        {tab === "memory" && <Memory t={t} />}
        {tab === "history" && <History t={t} />}
        {tab === "settings" && <Settings lang={lang} setLang={setLang} t={t} voice={voice} />}
      </main>
    </div>
  );
}

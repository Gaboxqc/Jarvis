/**
 * Editing a list of folders — REQ-16, REQ-26.
 *
 * These two settings are the blast radius of every file skill: what the
 * assistant may read, and what it has indexed. They were hand-edited YAML
 * until the app was required to be configurable without a text editor.
 *
 * Typed rather than picked from a dialog. A native folder picker would be
 * friendlier, and Tauri has one — but the same React runs in a browser during
 * development, where it does not exist, and a control that silently does
 * nothing in one of the two places it runs is worse than a text field. The
 * backend validates every path anyway: it must exist, be a directory, and not
 * be a whole drive or a Windows folder. So a typo produces a clear refusal
 * rather than a setting that quietly points nowhere.
 */

import { useState } from "react";
import type { Key } from "../i18n";

interface Props {
  label: string;
  hint: string;
  folders: string[];
  busy?: boolean;
  onChange: (folders: string[]) => Promise<void> | void;
  t: (key: Key, vars?: Record<string, string | number>) => string;
}

export function FolderList({ label, hint, folders, busy, onChange, t }: Props) {
  const [draft, setDraft] = useState("");

  function add(event: React.FormEvent) {
    event.preventDefault();
    const value = draft.trim();
    if (!value) return;
    // Deduplicate case-insensitively: Windows paths are case-preserving but not
    // case-sensitive, so two spellings of one folder would be indexed twice.
    if (folders.some((f) => f.toLowerCase() === value.toLowerCase())) {
      setDraft("");
      return;
    }
    void onChange([...folders, value]);
    setDraft("");
  }

  return (
    <div className="stack">
      <span className="small">{label}</span>

      {folders.length === 0 ? (
        <p className="small muted">{t("folders.none")}</p>
      ) : (
        <ul className="plain">
          {folders.map((folder) => (
            <li key={folder}>
              <div className="spread">
                <code className="small">{folder}</code>
                <button
                  className="ghost"
                  disabled={busy || folders.length === 1}
                  // The last one cannot go: an empty list is refused by the
                  // backend, and disabling the button explains why in advance
                  // rather than after a failed save.
                  title={folders.length === 1 ? t("folders.lastOne") : undefined}
                  onClick={() => void onChange(folders.filter((f) => f !== folder))}
                >
                  {t("folders.remove")}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <form className="row" onSubmit={add}>
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={t("folders.placeholder")}
          spellCheck={false}
          style={{ flex: 1 }}
        />
        <button className="ghost" type="submit" disabled={busy || !draft.trim()}>
          {t("folders.add")}
        </button>
      </form>

      <p className="small muted">{hint}</p>
    </div>
  );
}

/**
 * Action history — REQ-25.
 *
 * Everything Kai has done, whether it worked, and whether it can still be taken
 * back. The undo button appears only where the backend says the action is
 * reversible, so the interface never offers a promise the journal cannot keep.
 */

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type ActionRecord } from "../api";
import type { Key } from "../i18n";

export function History({ t }: { t: (key: Key) => string }) {
  const [records, setRecords] = useState<ActionRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRecords((await api.history()).history);
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t("common.error"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  async function undo(id: string) {
    try {
      setNote((await api.undo(id)).message);
    } catch (caught) {
      setNote(caught instanceof ApiError ? caught.message : t("common.error"));
    }
    void load();
  }

  return (
    <div className="view">
      <div className="spread">
        <h1>{t("history.title")}</h1>
        <button className="ghost" onClick={() => void api.undoLast().then(load)}>
          {t("history.undoLast")}
        </button>
      </div>

      {error && <div className="banner">{error}</div>}
      {note && <p className="small muted" role="status">{note}</p>}
      {loading && <p className="muted">{t("common.loading")}…</p>}
      {!loading && !error && records.length === 0 && (
        <p className="muted">{t("history.empty")}</p>
      )}

      <ul className="plain">
        {records.map((record) => (
          <li key={record.id} className="spread">
            <span>
              <span className={`tag ${record.status}`}>{record.status}</span>{" "}
              {record.preview}
              {record.error && <div className="small muted">{record.error}</div>}
            </span>
            {record.can_undo && (
              <button className="ghost" onClick={() => undo(record.id)}>
                {t("history.undo")}
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Memory review — REQ-7.
 *
 * REQ-7 requires that every stored fact can be reviewed and deleted, singly or
 * all at once. This screen is that requirement; without it, "nothing is stored
 * silently" would rest on the assistant's own reporting.
 */

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type MemoryFact } from "../api";
import type { Key } from "../i18n";

export function Memory({ t }: { t: (key: Key) => string }) {
  const [facts, setFacts] = useState<MemoryFact[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setFacts((await api.memory()).facts);
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

  async function forget(id: string) {
    await api.forgetFact(id).catch(() => undefined);
    void load();
  }

  async function forgetAll() {
    if (!window.confirm(t("memory.confirmAll"))) return;
    await api.forgetAll().catch(() => undefined);
    void load();
  }

  return (
    <div className="view">
      <div className="spread">
        <h1>{t("memory.title")}</h1>
        {facts.length > 0 && (
          <button className="danger" onClick={forgetAll}>
            {t("memory.forgetAll")}
          </button>
        )}
      </div>

      {error && <div className="banner">{error}</div>}
      {loading && <p className="muted">{t("common.loading")}…</p>}
      {!loading && !error && facts.length === 0 && (
        <p className="muted">{t("memory.empty")}</p>
      )}

      <ul className="plain">
        {facts.map((fact) => (
          <li key={fact.id} className="spread">
            <span>
              <span className="tag">{fact.category}</span> {fact.text}
            </span>
            <button className="ghost" onClick={() => forget(fact.id)}>
              {t("memory.forget")}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

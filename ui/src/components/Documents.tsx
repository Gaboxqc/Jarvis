/**
 * Searching your own paperwork — REQ-16, REQ-26.
 *
 * Two things share this screen because they answer one another. The search box
 * is what you came for; the index status underneath is what explains an empty
 * result. Without it, "no matches" is indistinguishable from "nothing has been
 * indexed yet" and from "the scan is paused because you're on battery" — and a
 * user with no way to tell those apart concludes the feature is broken.
 *
 * Results quote the passage and cite the file. That is the whole point of
 * indexing documents rather than asking the model: an answer about your lease
 * is only worth anything if you can see which line it came from and open the
 * file to check.
 *
 * This is also the one screen that shows the contents of the user's files, so
 * it shows them and nothing else — no summarising, no rewriting. What is on
 * screen is what is in the document.
 */

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type SemanticStatus, type DocumentHit, type IndexStatus } from "../api";
import type { Key } from "../i18n";

interface Props {
  t: (key: Key, vars?: Record<string, string | number>) => string;
}

export function Documents({ t }: Props) {
  const [status, setStatus] = useState<IndexStatus | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<DocumentHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showFiles, setShowFiles] = useState(false);
  const [files, setFiles] = useState<string[]>([]);
  const [semantic, setSemantic] = useState<SemanticStatus | null>(null);
  const [installing, setInstalling] = useState(false);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await api.documentStatus());
      setSemantic(await api.semanticStatus().catch(() => null));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t("common.error"));
    }
  }, [t]);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  // A scan can take minutes on a large folder, so keep the counts moving while
  // one is running rather than leaving a stale number on screen.
  useEffect(() => {
    if (!status?.running) return;
    const timer = setInterval(() => void loadStatus(), 5000);
    return () => clearInterval(timer);
  }, [status?.running, loadStatus]);

  async function search(event: React.FormEvent) {
    event.preventDefault();
    const text = query.trim();
    if (!text) return;
    setSearching(true);
    setError(null);
    try {
      const found = await api.searchDocuments(text);
      setResults(found.results);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t("common.error"));
    } finally {
      setSearching(false);
    }
  }

  async function reindex() {
    setReindexing(true);
    setError(null);
    try {
      await api.reindex();
      await loadStatus();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t("common.error"));
    } finally {
      setReindexing(false);
    }
  }

  async function clearIndex() {
    if (!window.confirm(t("documents.clearConfirm"))) return;
    try {
      await api.clearIndex();
      setResults(null);
      await loadStatus();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t("common.error"));
    }
  }

  async function toggleFiles() {
    if (!showFiles && files.length === 0) {
      try {
        const listed = await api.documents();
        setFiles(listed.documents.map((d) => d.path));
      } catch {
        // The list is a detail; the counts above already carry the substance.
      }
    }
    setShowFiles(!showFiles);
  }

  const indexed = status?.documents ?? 0;

  return (
    <div className="view">
      <h1>{t("documents.title")}</h1>
      {error && <div className="banner">{error}</div>}

      {semantic?.enabled && (
        <section className="card">
          <h2 className="small muted">{t("documents.semantic")}</h2>
          <p className="small muted">{t("documents.semanticNote")}</p>
          {semantic.model_installed ? (
            <p className="small">
              {t(
                semantic.embedded < semantic.chunks
                  ? "documents.semanticCatchUp"
                  : "documents.semanticOn",
                { embedded: semantic.embedded, chunks: semantic.chunks },
              )}
            </p>
          ) : (
            <>
              <p className="small muted">
                {t("documents.semanticOff", {
                  size: semantic.download_mb,
                  model: semantic.model,
                })}
              </p>
              <button
                disabled={installing}
                onClick={() => {
                  setInstalling(true);
                  setError(null);
                  void api
                    .installEmbeddingModel()
                    .then(setSemantic)
                    .catch((caught) =>
                      setError(caught instanceof ApiError ? caught.message : t("common.error")),
                    )
                    .finally(() => setInstalling(false));
                }}
              >
                {installing
                  ? t("documents.semanticInstalling")
                  : t("documents.semanticInstall")}
              </button>
            </>
          )}
        </section>
      )}

      <section className="card">
        <form className="row" onSubmit={search}>
          <label className="sr-only" htmlFor="doc-search">
            {t("documents.placeholder")}
          </label>
          <input
            id="doc-search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("documents.placeholder")}
            style={{ flex: 1 }}
          />
          <button className="primary" type="submit" disabled={searching || !query.trim()}>
            {searching ? t("documents.searching") : t("documents.search")}
          </button>
        </form>

        {indexed === 0 && (
          // The most likely reason a search returns nothing, said before the
          // user tries and draws the wrong conclusion.
          <p className="small muted" style={{ marginTop: "0.5rem" }}>
            {t("documents.nothingIndexed")}
          </p>
        )}
      </section>

      {results !== null && (
        <section className="card">
          <h2 className="small muted">
            {results.length === 0
              ? t("documents.noMatches")
              : t("documents.matches", { count: results.length })}
          </h2>
          <ul className="plain">
            {results.map((hit, index) => (
              <li key={`${hit.path}-${index}`}>
                <div className="small" style={{ fontWeight: 600 }}>{hit.citation}</div>
                {/* The passage verbatim. Nothing here is rewritten. */}
                <div className="small" style={{ whiteSpace: "pre-wrap", margin: "0.2rem 0" }}>
                  {hit.text}
                </div>
                <code className="small muted">{hit.path}</code>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="card">
        <div className="spread">
          <h2 className="small muted">{t("documents.index")}</h2>
          <div className="row">
            <button className="ghost" onClick={() => void reindex()} disabled={reindexing}>
              {reindexing ? t("documents.scanning") : t("documents.rescan")}
            </button>
            {indexed > 0 && (
              <button className="ghost" onClick={() => void clearIndex()}>
                {t("documents.clear")}
              </button>
            )}
          </div>
        </div>

        {status && (
          <>
            <div className="spread">
              <span className="small">{t("documents.indexed")}</span>
              <span className="small muted">
                {t("documents.counts", { documents: status.documents, chunks: status.chunks })}
              </span>
            </div>

            {status.running && <p className="small">{t("documents.scanningNow")}…</p>}

            {/* Why nothing is happening, when nothing is happening. */}
            {status.deferred_because && (
              <p className="small muted">
                {t("documents.deferred", { reason: status.deferred_because })}
              </p>
            )}

            <div className="spread">
              <span className="small muted">{t("documents.folders")}</span>
              <span className="small muted">
                {status.folders.length ? status.folders.join(", ") : t("documents.noFolders")}
              </span>
            </div>

            {status.failed > 0 && (
              <details style={{ marginTop: "0.4rem" }}>
                <summary className="small" style={{ color: "var(--warn)" }}>
                  {t("documents.failed", { count: status.failed })}
                </summary>
                <ul className="plain">
                  {status.failures.map((failure) => (
                    <li key={failure.path}>
                      <code className="small">{failure.path}</code>
                      <div className="small muted">{failure.error}</div>
                    </li>
                  ))}
                </ul>
              </details>
            )}

            {indexed > 0 && (
              <p className="small" style={{ marginTop: "0.4rem" }}>
                <button className="ghost" onClick={() => void toggleFiles()}>
                  {showFiles ? t("documents.hideFiles") : t("documents.showFiles")}
                </button>
              </p>
            )}
            {showFiles && (
              <ul className="plain">
                {files.map((path) => (
                  <li key={path}>
                    <code className="small">{path}</code>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
        <p className="small muted">{t("documents.localNote")}</p>
      </section>
    </div>
  );
}

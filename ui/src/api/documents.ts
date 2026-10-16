/** The document index and search over it. */

import { request } from "./client";
import type { DocumentHit, IndexStatus, IndexedDocument, SemanticStatus } from "./types";

export const documents = {
  documentStatus: () => request<IndexStatus>("/documents/status"),

  documents: () => request<{ documents: IndexedDocument[] }>("/documents"),

  searchDocuments: (query: string, limit = 6) =>
    request<{ results: DocumentHit[] }>(
      `/documents/search?q=${encodeURIComponent(query)}&limit=${limit}`,
      { timeoutMs: 30_000 },
    ),

  // A full rescan reads every file in every indexed folder, so it is allowed
  // far longer than an ordinary call before being abandoned.
  reindex: () => request<Record<string, unknown>>("/documents/reindex", {
    method: "POST", timeoutMs: 30 * 60_000,
  }),

  clearIndex: () => request<{ cleared_documents: number }>("/documents/index", {
    method: "DELETE",
  }),

  /** Whether search by meaning is on, and how much of the index has it. */
  semanticStatus: () => request<SemanticStatus>("/documents/semantic"),

  /**
   * Pull the embedding model. 274MB, so the timeout is generous and the caller
   * has to show that something is happening.
   */
  installEmbeddingModel: () =>
    request<SemanticStatus>("/documents/semantic/model", {
      method: "POST",
      timeoutMs: 30 * 60_000,
    }),
};

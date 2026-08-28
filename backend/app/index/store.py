"""Index storage and retrieval — REQ-16.

Writes chunks into the FTS5 table and queries them back with citations.

The query sanitiser matters more than it looks. FTS5's MATCH grammar treats
quotes, `*`, `-`, `^`, `:` and the bare words AND/OR/NOT/NEAR as operators, so
passing a natural question straight through raises `sqlite3.OperationalError` on
perfectly ordinary input — "what's the deposit?" is a syntax error. Every query
is rebuilt from extracted terms rather than escaped, so there is no input that
can reach the parser as syntax.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import db
from .chunk import Chunk

log = logging.getLogger(__name__)

MAX_TERMS = 12

# Words that carry no retrieval signal but do carry FTS5 operator meaning, plus
# the usual question scaffolding.
_NOISE = {
    "a", "about", "all", "and", "any", "are", "as", "at", "be", "been", "but",
    "by", "can", "did", "do", "does", "for", "from", "get", "give", "had", "has",
    "have", "how", "i", "if", "in", "into", "is", "it", "its", "just", "know",
    "like", "me", "much", "my", "near", "not", "of", "on", "one", "or", "our",
    "out", "over", "say", "see", "should", "so", "some", "tell", "that", "the",
    "their", "them", "then", "there", "these", "they", "this", "to", "up", "was",
    "we", "were", "what", "when", "where", "which", "who", "why", "will", "with",
    "would", "you", "your",
}


@dataclass
class Hit:
    path: str
    section: str
    text: str
    score: float

    @property
    def filename(self) -> str:
        return Path(self.path).name

    def citation(self) -> str:
        return f"{self.filename} ({self.section})" if self.section else self.filename

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.filename,
            "path": self.path,
            "section": self.section,
            "citation": self.citation(),
            "text": self.text,
        }


# -- writing ---------------------------------------------------------------


def replace_document(
    path: Path, *, title: str, size: int, mtime: float, chunks: list[Chunk]
) -> None:
    """Index a document, replacing anything previously stored for that path."""
    key = str(path)
    conn = db.connect()
    conn.execute("DELETE FROM document_chunks WHERE path = ?", (key,))
    conn.executemany(
        "INSERT INTO document_chunks(text, path, section, ordinal) VALUES(?, ?, ?, ?)",
        [(c.text, key, c.section, c.ordinal) for c in chunks],
    )
    conn.execute(
        """
        INSERT INTO indexed_documents(path, title, size, mtime, chunk_count, indexed_at, error)
        VALUES(?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(path) DO UPDATE SET
            title=excluded.title, size=excluded.size, mtime=excluded.mtime,
            chunk_count=excluded.chunk_count, indexed_at=excluded.indexed_at, error=NULL
        """,
        (key, title, size, mtime, len(chunks), db.now()),
    )
    conn.commit()

    # After the commit, and tolerant of failing. Embedding is a network call to
    # Ollama; holding the chunk write open across it would mean a slow or absent
    # daemon could leave a document half-indexed. Keyword search works the
    # moment the commit above lands, and the vectors catch up or do not.
    from . import search as search_module

    search_module.index_chunks(key, chunks)


def record_failure(path: Path, *, size: int, mtime: float, error: str) -> None:
    """Remember that a file could not be read, so it isn't retried every scan.

    Stored rather than swallowed: the user can be told which documents are not
    searchable and why, instead of wondering why an answer never cites them.
    """
    key = str(path)
    conn = db.connect()
    conn.execute("DELETE FROM document_chunks WHERE path = ?", (key,))
    conn.execute("DELETE FROM chunk_vectors WHERE path = ?", (key,))
    conn.execute(
        """
        INSERT INTO indexed_documents(path, title, size, mtime, chunk_count, indexed_at, error)
        VALUES(?, ?, ?, ?, 0, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            size=excluded.size, mtime=excluded.mtime, chunk_count=0,
            indexed_at=excluded.indexed_at, error=excluded.error
        """,
        (key, path.stem, size, mtime, db.now(), error[:300]),
    )
    conn.commit()


def forget_document(path: str) -> None:
    conn = db.connect()
    conn.execute("DELETE FROM document_chunks WHERE path = ?", (path,))
    conn.execute("DELETE FROM chunk_vectors WHERE path = ?", (path,))
    conn.execute("DELETE FROM indexed_documents WHERE path = ?", (path,))
    conn.commit()


def known_state() -> dict[str, tuple[int, float]]:
    """path -> (size, mtime) for everything already indexed."""
    return {
        row["path"]: (int(row["size"]), float(row["mtime"]))
        for row in db.query("SELECT path, size, mtime FROM indexed_documents")
    }


def clear() -> int:
    count = len(known_state())
    conn = db.connect()
    conn.execute("DELETE FROM document_chunks")
    conn.execute("DELETE FROM chunk_vectors")
    conn.execute("DELETE FROM indexed_documents")
    conn.commit()
    return count


def stats() -> dict[str, Any]:
    row = db.query_one(
        """
        SELECT COUNT(*) AS documents,
               COALESCE(SUM(chunk_count), 0) AS chunks,
               SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS failed,
               MAX(indexed_at) AS last_indexed
          FROM indexed_documents
        """
    )
    # An aggregate with no GROUP BY returns exactly one row, empty table or
    # not. The assert is for the type checker, and would fire only if that
    # query stopped being an aggregate.
    assert row is not None
    return {
        "documents": int(row["documents"] or 0),
        "chunks": int(row["chunks"] or 0),
        "failed": int(row["failed"] or 0),
        "last_indexed": row["last_indexed"],
    }


def failures() -> list[dict[str, str]]:
    return [
        {"file": Path(row["path"]).name, "path": row["path"], "error": row["error"]}
        for row in db.query(
            "SELECT path, error FROM indexed_documents WHERE error IS NOT NULL ORDER BY path"
        )
    ]


def documents() -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT path, title, chunk_count, indexed_at, error FROM indexed_documents ORDER BY path"
    )
    return [
        {
            "file": Path(row["path"]).name,
            "path": row["path"],
            "chunks": int(row["chunk_count"]),
            "indexed_at": row["indexed_at"],
            "error": row["error"],
        }
        for row in rows
    ]


# -- searching -------------------------------------------------------------


def search(query: str, limit: int = 6) -> list[Hit]:
    """Retrieve chunks for a natural-language question.

    Tries all terms together first for precision, then falls back to any-term
    so a question that mentions one word the document doesn't use still returns
    something rather than nothing.
    """
    terms = extract_terms(query)
    if not terms:
        return []

    hits = _match(" AND ".join(terms), limit)
    if not hits and len(terms) > 1:
        hits = _match(" OR ".join(terms), limit)
    return hits


def _match(expression: str, limit: int) -> list[Hit]:
    try:
        rows = db.query(
            """
            SELECT text, path, section, bm25(document_chunks) AS score
              FROM document_chunks
             WHERE document_chunks MATCH ?
             ORDER BY score
             LIMIT ?
            """,
            (expression, limit),
        )
    except Exception as exc:  # noqa: BLE001 — never let a query shape kill the turn
        log.warning("FTS query failed for %r: %s", expression, exc)
        return []

    return [
        Hit(path=row["path"], section=row["section"], text=row["text"], score=float(row["score"]))
        for row in rows
    ]


def extract_terms(query: str) -> list[str]:
    """Rebuild a query as a list of safely quoted terms.

    Terms are extracted, not escaped: only word characters survive, and each is
    wrapped in double quotes so FTS5 reads it as a literal. Operators, wildcards
    and stray punctuation cannot reach the parser.
    """
    words = re.findall(r"[0-9A-Za-z_]+", query.lower())
    terms: list[str] = []
    for word in words:
        if len(word) < 2 or word in _NOISE:
            continue
        quoted = f'"{word}"'
        if quoted not in terms:
            terms.append(quoted)
        if len(terms) >= MAX_TERMS:
            break

    if not terms:
        # Everything was filtered out (e.g. "what is it about?"). Fall back to
        # the longest raw word so the search is weak rather than absent.
        longest = max(words, key=len, default="")
        if len(longest) >= 2:
            terms = [f'"{longest}"']
    return terms

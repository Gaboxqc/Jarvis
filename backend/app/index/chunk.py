"""Chunking — REQ-16.

Splits sections into retrieval-sized pieces on paragraph, then sentence
boundaries, with a small overlap so a fact that straddles a boundary is still
findable from either side.

Chunks stay reasonably large (~1200 characters). The retrieved text is read by a
model that needs surrounding context to answer, and a chunk cut too tight
retrieves the matching sentence while losing the clause that qualifies it —
which is how a document search ends up confidently wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .extract import Section

TARGET_CHARS = 1200
OVERLAP_CHARS = 150
MIN_CHARS = 60


@dataclass
class Chunk:
    text: str
    section: str
    ordinal: int


def chunk_sections(sections: list[Section]) -> list[Chunk]:
    chunks: list[Chunk] = []
    ordinal = 0
    for section in sections:
        for body in _split(section.text):
            if len(body) < MIN_CHARS and chunks and chunks[-1].section == section.label:
                # Fold a stray fragment into the previous chunk rather than
                # storing something too small to be meaningful on its own.
                chunks[-1].text = f"{chunks[-1].text}\n{body}"
                continue
            chunks.append(Chunk(text=body, section=section.label, ordinal=ordinal))
            ordinal += 1
    return chunks


def _split(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= TARGET_CHARS:
        return [text]

    pieces: list[str] = []
    for paragraph in _paragraphs(text):
        if len(paragraph) <= TARGET_CHARS:
            pieces.append(paragraph)
        else:
            pieces.extend(_split_long(paragraph))

    return _merge(pieces)


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _split_long(paragraph: str) -> list[str]:
    """Break an oversized paragraph on sentences, falling back to hard cuts."""
    sentences = _SENTENCE_END.split(paragraph)
    out: list[str] = []
    current = ""

    for sentence in sentences:
        if len(sentence) > TARGET_CHARS:
            if current:
                out.append(current.strip())
                current = ""
            # No sentence boundary to use — a table row, minified text, or a
            # language this regex doesn't segment. Cut on width.
            for start in range(0, len(sentence), TARGET_CHARS):
                out.append(sentence[start : start + TARGET_CHARS].strip())
            continue

        if len(current) + len(sentence) + 1 > TARGET_CHARS:
            out.append(current.strip())
            current = _tail(current) + " " + sentence
        else:
            current = f"{current} {sentence}".strip()

    if current.strip():
        out.append(current.strip())
    return [piece for piece in out if piece]


def _merge(pieces: list[str]) -> list[str]:
    """Pack small consecutive pieces up towards the target size."""
    merged: list[str] = []
    current = ""
    for piece in pieces:
        if not current:
            current = piece
        elif len(current) + len(piece) + 2 <= TARGET_CHARS:
            current = f"{current}\n\n{piece}"
        else:
            merged.append(current)
            current = piece
    if current:
        merged.append(current)
    return merged


def _tail(text: str) -> str:
    """The trailing overlap carried into the next chunk."""
    if len(text) <= OVERLAP_CHARS:
        return text
    tail = text[-OVERLAP_CHARS:]
    # Start the overlap at a word boundary so it reads as language.
    space = tail.find(" ")
    return tail[space + 1 :] if space != -1 else tail

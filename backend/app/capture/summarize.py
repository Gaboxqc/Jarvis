"""Meeting summarisation — REQ-19.

Turns a raw transcript into a summary, the decisions taken, and the action items
— which is the actual reason anyone records a meeting.

Two rules shape the prompt. Action items are only extracted where somebody
actually committed to something, because a list padded with everything that was
merely mentioned is worse than no list: it has to be re-read against the
transcript to be trusted, which is the work it was supposed to save. And the
summary is grounded strictly in the transcript, since a plausible invented
decision in a meeting record is a genuinely harmful artefact.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from ..brain import llm

log = logging.getLogger(__name__)

MAX_TRANSCRIPT_CHARS = 12_000


@dataclass
class Summary:
    summary: str = ""
    decisions: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    truncated: bool = False
    error: str = ""

    def render(self) -> str:
        if self.error:
            return f"Couldn't summarise: {self.error}"
        parts = [self.summary.strip() or "(no summary produced)"]
        if self.decisions:
            parts.append("Decisions:\n" + "\n".join(f"  - {d}" for d in self.decisions))
        if self.actions:
            parts.append("Action items:\n" + "\n".join(f"  - {a}" for a in self.actions))
        if not self.decisions and not self.actions:
            parts.append("No clear decisions or action items were committed to.")
        if self.truncated:
            parts.append("(Long meeting - summarised from the transcript's first portion.)")
        return "\n\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "decisions": self.decisions,
            "actions": self.actions,
            "truncated": self.truncated,
            "error": self.error,
        }


PROMPT = """You are summarising a meeting transcript.

The transcript comes from automatic speech recognition of a live conversation,
so it has no speaker labels and contains recognition errors. Do not guess who
said what.

Reply with JSON only, in this exact shape:
{"summary": "...", "decisions": ["..."], "actions": ["..."]}

Rules:
- summary: three or four sentences on what the meeting was about and where it
  landed. Base it only on the transcript. Do not add context you were not given.
- decisions: things the group actually settled. If nothing was settled, use [].
- actions: only things someone committed to doing. Include who, if the
  transcript makes it clear. A topic being discussed is not an action item.
  If nobody committed to anything, use [].
- Never invent a decision, an action or a name. An empty list is a correct
  answer and a fabricated entry is not.

Transcript:
"""


def summarise(text: str) -> Summary:
    text = (text or "").strip()
    if not text:
        return Summary(error="the transcript is empty")
    if len(text.split()) < 20:
        return Summary(
            summary="The recording was too short to summarise.",
            error="",
        )

    truncated = len(text) > MAX_TRANSCRIPT_CHARS
    body = text[:MAX_TRANSCRIPT_CHARS]

    try:
        reply = llm.chat(
            [{"role": "user", "content": PROMPT + body}], json_mode=True, temperature=0.1
        )
    except llm.LLMUnavailable as exc:
        # The transcript is already saved; failing to summarise loses nothing
        # but the convenience (REQ-27).
        return Summary(error=str(exc), truncated=truncated)

    parsed = reply.as_json()
    if not isinstance(parsed, dict):
        log.info("summariser returned unparseable output: %r", reply.text[:200])
        return Summary(
            summary=reply.text.strip()[:1000],
            truncated=truncated,
        )

    return Summary(
        summary=str(parsed.get("summary", "")).strip(),
        decisions=_string_list(parsed.get("decisions")),
        actions=_string_list(parsed.get("actions")),
        truncated=truncated,
    )


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for entry in value:
        if isinstance(entry, dict):
            # Models sometimes emit {"who": ..., "what": ...} instead of a string.
            entry = " - ".join(str(v) for v in entry.values() if v)
        text = str(entry).strip()
        if text and text.lower() not in {"none", "n/a", "no decisions", "no actions"}:
            out.append(text)
    return out

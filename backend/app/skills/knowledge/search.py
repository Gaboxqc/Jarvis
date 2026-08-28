"""Web search — REQ-15, REQ-27.

Returns sources alongside the text. The brain is instructed to synthesise from
these results rather than read them out, and — this is the part that matters —
to say it could not look something up rather than fall back on guesswork when
this skill fails.
"""

from __future__ import annotations

import logging
from typing import Any

from ..base import Skill, SkillContext, SkillError, SkillParam, SkillResult

log = logging.getLogger(__name__)


class WebSearchSkill(Skill):
    name = "knowledge.web_search"
    description = (
        "Search the web for current, factual, or unfamiliar information — news, prices, "
        "release dates, documentation, anything after your training data or specific to "
        "right now. Returns snippets with source URLs."
    )
    parameters = (
        SkillParam("query", "string", "The search query. Write it as you would type it."),
        SkillParam("max_results", "integer", "How many results (default 5).",
                   required=False, default=5),
    )
    requires = ("web_search",)

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        query = str(args["query"]).strip()
        if not query:
            raise SkillError("There was nothing to search for.")
        limit = max(1, min(int(args.get("max_results", 5) or 5), 10))

        results = _search(query, limit)
        if not results:
            return SkillResult(
                ok=True,
                message=f"No search results for '{query}'.",
                data={"query": query, "results": []},
            )

        lines = []
        for index, item in enumerate(results, start=1):
            lines.append(f"[{index}] {item['title']}\n{item['snippet']}\nSource: {item['url']}")

        return SkillResult(
            ok=True,
            message="\n\n".join(lines),
            data={"query": query, "results": results},
        )


def _search(query: str, limit: int) -> list[dict[str, str]]:
    """DuckDuckGo via `ddgs` — no API key, no account, no quota (REQ-30)."""
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # type: ignore[no-redef]  # older package name
        except ImportError as exc:
            raise SkillError(
                "Web search isn't installed on this machine (missing the 'ddgs' package)."
            ) from exc

    try:
        with DDGS() as client:
            raw = list(client.text(query, max_results=limit))
    except Exception as exc:  # noqa: BLE001 — network, parsing, rate limits all land here
        log.warning("web search failed for %r: %s", query, exc)
        raise SkillError("I couldn't reach the search service just now.") from exc

    results: list[dict[str, str]] = []
    for item in raw:
        results.append(
            {
                "title": (item.get("title") or "").strip(),
                "snippet": (item.get("body") or "").strip(),
                "url": (item.get("href") or item.get("url") or "").strip(),
            }
        )
    return results

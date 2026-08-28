"""Document Q&A — REQ-16, REQ-20.

Answers from the user's own files and always says which file the answer came
from. The citation is the point: an uncited answer about a private document is
indistinguishable from the model making it up, and the user has no way to check.

Retrieval returns chunk text verbatim for the brain to synthesise from. This
skill never paraphrases — if it did, an error would be introduced before the
model even sees the source.
"""

from __future__ import annotations

from typing import Any

from ...index import scanner, search, store
from ..base import Skill, SkillContext, SkillError, SkillParam, SkillResult

MAX_CHARS = 6000


class SearchDocumentsSkill(Skill):
    name = "documents.search"
    description = (
        "Search the user's own indexed documents (PDFs, Word files, notes) and return "
        "the relevant passages with their source file. Use for any question about "
        "'my' documents, contracts, notes, invoices, reports, or anything the user "
        "wrote or received rather than something on the public web."
    )
    parameters = (
        SkillParam("query", "string", "What to look for, in the user's own words."),
        SkillParam("limit", "integer", "How many passages (default 5).",
                   required=False, default=5),
    )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        query = str(args["query"]).strip()
        if not query:
            raise SkillError("There was nothing to search for.")
        limit = max(1, min(int(args.get("limit", 5) or 5), 10))

        indexed = store.stats()
        if indexed["documents"] == 0:
            folders = scanner.status()["folders"]
            if not folders:
                raise SkillError(
                    "No folders are set up for document search yet. Add them under "
                    "documents.indexed_folders in kai.config.yaml."
                )
            raise SkillError(
                "Nothing is indexed yet. The first scan may still be running — "
                "ask again shortly, or say 'reindex my documents'."
            )

        hits = search.search(query, limit=limit)
        if not hits:
            # Say nothing matched rather than letting the brain fill the gap.
            note = ""
            if indexed["failed"]:
                note = (
                    f" ({indexed['failed']} document(s) couldn't be read - "
                    "ask about document index status for details.)"
                )
            return SkillResult(
                ok=True,
                message=f"Nothing in the indexed documents matches '{query}'.{note}",
                data={"query": query, "results": []},
            )

        blocks, used = [], 0
        for hit in hits:
            block = f"From {hit.citation()}:\n{hit.text}"
            if used + len(block) > MAX_CHARS:
                break
            blocks.append(block)
            used += len(block)

        sources = sorted({hit.citation() for hit in hits[: len(blocks)]})
        return SkillResult(
            ok=True,
            message="\n\n".join(blocks),
            data={
                "query": query,
                "results": [hit.to_dict() for hit in hits[: len(blocks)]],
                "sources": sources,
            },
        )


class IndexStatusSkill(Skill):
    name = "documents.index_status"
    description = (
        "Report what is in the document index: how many files, which folders, and "
        "which documents could not be read. Use when the user asks what you can "
        "search, or why a document wasn't found."
    )
    parameters = ()

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        status = scanner.status()
        folders = status["folders"]
        if not folders:
            return SkillResult(
                ok=True,
                message="No folders are configured for document search.",
                data=status,
            )

        lines = [
            f"{status['documents']} documents indexed ({status['chunks']} passages) from: "
            + ", ".join(folders)
        ]
        if status["deferred_because"]:
            lines.append(f"Indexing is paused because {status['deferred_because']}.")
        elif status["running"]:
            lines.append("A scan is running right now.")

        problems = store.failures()
        if problems:
            lines.append(f"{len(problems)} could not be read:")
            lines += [f"  {p['file']} - {p['error']}" for p in problems[:10]]

        return SkillResult(ok=True, message="\n".join(lines),
                           data={**status, "failures": problems})


class ReindexSkill(Skill):
    name = "documents.reindex"
    description = (
        "Rescan the configured folders now instead of waiting for the next automatic "
        "scan. Use when the user has just added or changed a document."
    )
    parameters = ()

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        if not scanner.status()["folders"]:
            raise SkillError(
                "No folders are set up for document search. Add them under "
                "documents.indexed_folders in kai.config.yaml."
            )
        # force=True: an explicit ask overrides the battery/focus back-off,
        # because the user is right here waiting for it.
        result = scanner.scan(force=True)
        return SkillResult(ok=True, message=result.summary(), data=result.to_dict())

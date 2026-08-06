"""Memory skills — REQ-7.

These are deliberately *not* gated. REQ-7 requires that a memory write is
visible to the user, not that they approve it — and those are different bars.
Interrupting someone to ask permission before writing down "prefers short
replies" is the kind of prompt that gets clicked through without reading, which
devalues the prompts that matter.

Visibility is met three other ways: the write is stated in the reply, every
entry is listed and individually deletable, and both skills are reversible so
"undo" takes the last one back. Nothing is stored silently.
"""

from __future__ import annotations

from typing import Any

from ..memory import long_term
from .base import Skill, SkillContext, SkillError, SkillParam, SkillResult


class RememberSkill(Skill):
    name = "memory.remember"
    description = (
        "Store a durable fact or preference about the user so it is available in future "
        "conversations. Use for stable things (where a folder lives, a recurring meeting "
        "time, a dietary restriction, how they like replies written) — never for passing "
        "detail from the current conversation."
    )
    parameters = (
        SkillParam("text", "string", "The fact, written as a standalone sentence in the third person."),
        SkillParam(
            "category", "string", "One of: preference, fact, shortcut, person.",
            required=False, default="fact", enum=long_term.CATEGORIES,
        ),
    )
    reversible = True

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        text = str(args["text"]).strip()
        if not text:
            raise SkillError("There was nothing to remember.")
        fact = long_term.add(text, str(args.get("category", "fact")))
        return SkillResult(
            ok=True,
            # Stated back so the write is visible in the reply, per REQ-7.
            message=f"Noted and saved: {fact.text}",
            data=fact.to_dict(),
            undo_payload={"fact_id": fact.id},
        )

    def undo(self, undo_payload: dict[str, Any]) -> SkillResult:
        fact = long_term.delete(str(undo_payload.get("fact_id", "")))
        if fact is None:
            return SkillResult(ok=False, message="That memory was already gone.")
        return SkillResult(ok=True, message=f"Forgot: {fact.text}")


class ForgetSkill(Skill):
    name = "memory.forget"
    description = (
        "Delete a stored fact. Accepts either the fact id or text to match against. "
        "Use when the user says something is wrong or no longer true."
    )
    parameters = (
        SkillParam("query", "string", "The fact id, or words that appear in the fact."),
    )
    reversible = True

    @staticmethod
    def _matches(query: str) -> list[long_term.MemoryFact]:
        query = query.strip()
        if not query:
            return []
        exact = long_term.get(query)
        if exact is not None:
            return [exact]
        return long_term.search(query)

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        matches = self._matches(str(args["query"]))
        if not matches:
            raise SkillError("I don't have anything stored matching that.")

        removed = []
        for fact in matches:
            deleted = long_term.delete(fact.id)
            if deleted:
                removed.append({"text": deleted.text, "category": deleted.category})

        return SkillResult(
            ok=True,
            message=f"Forgot {len(removed)}: " + "; ".join(r["text"] for r in removed),
            data={"removed": removed},
            undo_payload={"removed": removed},
        )

    def undo(self, undo_payload: dict[str, Any]) -> SkillResult:
        restored = []
        for entry in undo_payload.get("removed", []):
            fact = long_term.add(entry["text"], entry.get("category", "fact"))
            restored.append(fact.text)
        return SkillResult(ok=True, message=f"Restored {len(restored)} memories.")


class ListMemoriesSkill(Skill):
    name = "memory.list"
    description = "List what is stored about the user. Use when asked what you remember."
    parameters = (
        SkillParam("filter", "string", "Optional words to filter by.", required=False),
    )

    def run(self, args: dict[str, Any], ctx: SkillContext) -> SkillResult:
        query = str(args.get("filter", "") or "").strip()
        facts = long_term.search(query) if query else long_term.all_facts()
        if not facts:
            return SkillResult(ok=True, message="Nothing is stored yet.", data={"facts": []})
        lines = [f"[{f.category}] {f.text}" for f in facts]
        return SkillResult(
            ok=True,
            message=f"{len(facts)} stored:\n" + "\n".join(lines),
            data={"facts": [f.to_dict() for f in facts]},
        )

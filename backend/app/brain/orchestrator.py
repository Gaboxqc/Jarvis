"""The turn loop — REQ-1 to REQ-7, REQ-15, REQ-24, REQ-25, REQ-27.

One user message in, one reply out, with the Action Gate in the middle.

Two intents are handled deterministically *before* the model sees them — undo,
and answering a pending confirmation. Both are high-stakes and unambiguous, and
neither should depend on a 4-billion-parameter model reading the room correctly.
Everything else routes through the LLM.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ..actions import gate, undo
from ..memory import long_term, short_term
from ..settings import load_config
from ..skills.base import SkillContext
from ..skills.registry import catalog
from . import llm, prompts

log = logging.getLogger(__name__)

MAX_SKILLS_PER_TURN = 3

_AFFIRMATIVE = {
    "y", "yes", "yeah", "yep", "yup", "ok", "okay", "sure", "do it", "go ahead",
    "go for it", "confirm", "confirmed", "please do", "proceed", "affirmative",
    "sí", "si", "vale", "dale", "hazlo",
}
_NEGATIVE = {
    "n", "no", "nope", "cancel", "stop", "don't", "dont", "nevermind",
    "never mind", "forget it", "no thanks", "nah",
}
_UNDO = re.compile(
    r"^\s*(undo|revert|roll ?back|take that back|put (them|it|those|that) back|deshaz)\b",
    re.IGNORECASE,
)

# Turns that cannot possibly need a tool, so they do not pay for a routing call.
#
# Deliberately a fixed set of whole phrases rather than a pattern: the cost of
# being wrong is asymmetric. Skipping the router on something that did need a
# tool produces a confident answer with nothing behind it, which is the exact
# failure _guard_ungrounded_reply exists to catch. Skipping it on "thanks" saves
# half the turn. So the set only contains phrases that are complete in
# themselves and carry no request — anything longer still routes.
_PLEASANTRIES = {
    "hi", "hello", "hey", "yo", "good morning", "good afternoon", "good evening",
    "thanks", "thank you", "thanks a lot", "thank you very much", "ta", "cheers",
    "ok", "okay", "cool", "nice", "great", "perfect", "got it", "understood",
    "never mind", "nevermind", "no worries", "sorry",
    "bye", "goodbye", "good night", "see you", "later",
    # Spanish — the UI ships in both languages (REQ-28).
    "hola", "buenos días", "buenos dias", "buenas tardes", "buenas noches",
    "gracias", "muchas gracias", "vale", "genial", "perfecto", "entendido",
    "adiós", "adios", "hasta luego", "buenas",
}

# Sources the model cannot possibly know about without calling a tool. Memory
# facts are deliberately absent: those are injected into the system prompt, so
# answering from them without a tool call is legitimate.
_EXTERNAL_DATA = re.compile(
    r"\b(my|the)\s+("
    r"calendar|schedule|agenda|diary|meetings?|appointments?|"
    r"inbox|e-?mails?|mail|messages?|"
    r"documents?|files?|folders?|"
    r"tasks?|to-?dos?|reminders?"
    r")\b",
    re.IGNORECASE,
)

# First-person claims that something was carried out. Kept tight and
# action-specific: "I've noticed" and "I've got" must not match.
_ACTION_CLAIM = re.compile(
    r"\b(i(?:'ve| have)?\s+(?:just\s+)?"
    r"(added|created|scheduled|booked|set(?: up)?|sent|deleted|removed|cancelled|"
    r"canceled|moved|saved|stored|updated|marked|organi[sz]ed|reminded)"
    r"|(?:has|have) been (added|created|scheduled|sent|deleted|removed|cancelled|"
    r"canceled|moved|saved|updated|marked|set))\b",
    re.IGNORECASE,
)


@dataclass
class PendingAction:
    action_id: str
    skill: str
    preview: str
    reversible: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "skill": self.skill,
            "preview": self.preview,
            "reversible": self.reversible,
        }


@dataclass
class TurnResult:
    reply: str
    pending: PendingAction | None = None
    skill_calls: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    @property
    def needs_confirmation(self) -> bool:
        return self.pending is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "reply": self.reply,
            "needs_confirmation": self.needs_confirmation,
            "pending": self.pending.to_dict() if self.pending else None,
            "skill_calls": self.skill_calls,
            "error": self.error,
        }


def handle_turn(
    text: str,
    session_id: str = "default",
    *,
    pending_action_id: str | None = None,
) -> TurnResult:
    text = (text or "").strip()
    if not text:
        return TurnResult(reply="")

    config = load_config()
    ctx = SkillContext(session_id=session_id, config=config, user_text=text)
    short_term.record(session_id, "user", text)

    normalized = text.lower().strip(" .!?")

    # 1. Answering an outstanding confirmation.
    #
    # The bare word "yes" never authorises anything on its own — the caller has to
    # hand back the action_id it was given, and the gate re-checks that id's state.
    # A "yes" with no pending id is just conversation.
    if pending_action_id:
        if normalized in _AFFIRMATIVE:
            return _finish(session_id, _run_confirmation(pending_action_id, ctx))
        if normalized in _NEGATIVE:
            outcome = gate.decline(pending_action_id)
            return _finish(session_id, TurnResult(reply=f"Cancelled. I didn't {_lower_first(outcome.preview)}"))

    # 2. Undo.
    if _UNDO.match(text):
        result = undo.undo_last()
        return _finish(session_id, TurnResult(reply=result.message, error=None if result.ok else result.message))

    # 3. Everything else goes through the model.
    facts = long_term.relevant(text)
    system = prompts.system_prompt(config, facts)
    history = [turn.as_message() for turn in short_term.window(session_id)]

    # 3a. A greeting needs an answer, but it never needs a tool. Routing it costs
    # a whole model call to be told so — half the latency of the turn, spent
    # establishing that "thanks" is not a request.
    if normalized in _PLEASANTRIES:
        try:
            answer = llm.chat([{"role": "system", "content": system}, *history])
        except llm.LLMUnavailable as exc:
            return _finish(session_id, TurnResult(reply=str(exc), error="llm_unavailable"))
        return _finish(session_id, TurnResult(reply=answer.text))

    try:
        route = _route(system, history, text)
    except llm.LLMUnavailable as exc:
        # REQ-27: a dead model is reported plainly, and everything stored locally
        # is still reachable through the API and CLI commands.
        return _finish(session_id, TurnResult(reply=str(exc), error="llm_unavailable"))

    calls = _extract_calls(route)
    if not calls:
        direct = (route.get("reply") or "").strip() if isinstance(route, dict) else ""
        if direct:
            return _finish(
                session_id, TurnResult(reply=_guard_ungrounded_reply(text, direct, []))
            )
        try:
            answer = llm.chat([{"role": "system", "content": system}, *history])
        except llm.LLMUnavailable as exc:
            return _finish(session_id, TurnResult(reply=str(exc), error="llm_unavailable"))
        return _finish(
            session_id, TurnResult(reply=_guard_ungrounded_reply(text, answer.text, []))
        )

    # 4. Execute, stopping at the first action that needs approval.
    batch_id = gate.new_batch_id()
    results: list[dict[str, Any]] = []
    for call in calls[:MAX_SKILLS_PER_TURN]:
        outcome = gate.submit(call["name"], call.get("args") or {}, ctx, batch_id=batch_id)
        results.append(outcome.to_dict())

        if outcome.status == gate.NEEDS_CONFIRMATION:
            # Nothing after this runs. A later step might depend on this one, and
            # queuing side effects behind an unanswered question is how a "no"
            # ends up having done half the work anyway.
            pending = PendingAction(
                action_id=outcome.action_id or "",
                skill=outcome.skill_name,
                preview=outcome.preview,
                reversible=outcome.reversible,
            )
            return _finish(
                session_id,
                TurnResult(
                    reply=prompts.confirmation_prompt(outcome.preview, outcome.reversible),
                    pending=pending,
                    skill_calls=results,
                ),
            )

    reply = _guard_ungrounded_reply(text, _synthesize(system, history, results), results)
    return _finish(session_id, TurnResult(reply=reply, skill_calls=results))


def confirm_pending(action_id: str, session_id: str = "default") -> TurnResult:
    """Direct confirmation path used by the API and by UI buttons."""
    ctx = SkillContext(session_id=session_id, config=load_config())
    return _finish(session_id, _run_confirmation(action_id, ctx))


def decline_pending(action_id: str, session_id: str = "default") -> TurnResult:
    outcome = gate.decline(action_id)
    return _finish(session_id, TurnResult(reply=f"Cancelled. I didn't {_lower_first(outcome.preview)}"))


# -- internals -------------------------------------------------------------


def _run_confirmation(action_id: str, ctx: SkillContext) -> TurnResult:
    outcome = gate.confirm(action_id, ctx)
    if outcome.status == gate.EXECUTED:
        tail = " Say 'undo' if that wasn't right." if outcome.reversible else ""
        return TurnResult(reply=f"{outcome.message}{tail}", skill_calls=[outcome.to_dict()])
    return TurnResult(
        reply=outcome.error or "That didn't work.",
        skill_calls=[outcome.to_dict()],
        error=outcome.error,
    )


def _route(system: str, history: list[dict[str, str]], text: str) -> dict[str, Any] | None:
    available = catalog()
    if not available:
        return None
    messages = [
        {"role": "system", "content": system},
        {"role": "system", "content": prompts.router_prompt(available)},
        *history[-6:],
        {"role": "user", "content": text},
    ]
    reply = llm.chat(messages, json_mode=True, temperature=0.0)
    parsed = reply.as_json()
    if parsed is None:
        log.info("router returned unparseable output: %r", reply.text[:200])
    return parsed


def _extract_calls(route: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Pull valid skill calls out of the router's JSON, discarding anything malformed.

    Small models hallucinate tool names and nest arguments unpredictably. Filtering
    against the live catalog here means a bad route degrades into a plain
    conversational answer instead of a confusing error.
    """
    if not isinstance(route, dict):
        return []

    raw = route.get("skills") or route.get("tools") or route.get("skill_calls") or []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []

    known = {entry["name"] for entry in catalog()}
    calls: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("skill") or entry.get("tool")
        if not isinstance(name, str) or name not in known:
            if name:
                log.info("router proposed unknown skill %r; ignoring", name)
            continue
        args = entry.get("args") or entry.get("arguments") or entry.get("parameters") or {}
        calls.append({"name": name, "args": args if isinstance(args, dict) else {}})
    return calls


def _synthesize(system: str, history: list[dict[str, str]], results: list[dict[str, Any]]) -> str:
    payload = [
        {
            "skill": r["skill"],
            "status": r["status"],
            "result": r["message"],
            "error": r.get("error"),
        }
        for r in results
    ]
    # The results go in as the final *user* turn, not a trailing system message.
    # Small models reliably answer the last user message; a system message tacked
    # on after the history tends to get treated as background and the model
    # replays its previous answer instead.
    messages = [
        {"role": "system", "content": system},
        *history[-4:],
        {"role": "user", "content": prompts.synthesis_prompt(payload)},
    ]
    try:
        reply = llm.chat(messages).text or _fallback_summary(results)
    except llm.LLMUnavailable:
        # The work already happened; report it verbatim rather than losing it
        # because the phrasing pass could not run (REQ-27).
        return _fallback_summary(results)

    return _append_receipts(reply, results)


def _append_receipts(reply: str, results: list[dict[str, Any]]) -> str:
    """Guarantee that writes made without asking are disclosed (REQ-7).

    The synthesis pass reliably turns "Noted and saved: prefers short replies"
    into "I'll keep replies brief" — agreement, not disclosure. Since these
    actions run without a confirmation prompt, the disclosure is the only signal
    the user gets, so it cannot be left to the model's discretion. If the reply
    already conveys it, nothing is added.
    """
    from ..skills.registry import get_skill

    notes: list[str] = []
    for entry in results:
        if entry.get("status") != gate.EXECUTED:
            continue
        skill = get_skill(entry.get("skill", ""))
        if skill is None or not getattr(skill, "always_report", False):
            continue
        message = (entry.get("message") or "").strip()
        if message and not _conveys(reply, message):
            notes.append(message)

    if not notes:
        return reply
    return reply.rstrip() + "\n" + "\n".join(f"({note})" for note in notes)


def _conveys(reply: str, message: str) -> bool:
    """Whether the reply already carries the substance of a skill's message.

    Compares the part after the colon — the payload — rather than the whole
    sentence, since the model legitimately rewords the lead-in.
    """
    payload = message.split(":", 1)[-1].strip().rstrip(".")
    if not payload:
        return False
    return payload.lower() in reply.lower()


def _fallback_summary(results: list[dict[str, Any]]) -> str:
    return "\n".join(r["message"] for r in results if r.get("message")) or "Done."


def _guard_ungrounded_reply(user_text: str, reply: str, results: list[dict[str, Any]]) -> str:
    """Catch answers the model had no way to know — REQ-27.

    When routing fails, a small model does not say "I can't check that". It
    invents a plausible answer: meetings with people who don't exist, or a
    confirmation that it added an event when nothing ran. Both are worse than an
    error, because they are indistinguishable from success.

    The system prompt already forbids this and is ignored under pressure, so it
    is enforced here instead. Both checks require that *no skill executed*, which
    is what makes them safe: a grounded turn is never touched.
    """
    if any(r.get("status") == gate.EXECUTED for r in results):
        return reply
    if not reply:
        return reply

    # Claiming to have done something, having done nothing.
    if _ACTION_CLAIM.search(reply):
        log.warning("blocked an unperformed action claim: %r", reply[:120])
        return (
            "I didn't actually do that — I couldn't work out which action to run. "
            "Could you say it again more plainly?"
        )

    # Answering about a data source that requires a tool it never called.
    if _EXTERNAL_DATA.search(user_text):
        log.warning("blocked an ungrounded answer about external data: %r", reply[:120])
        return (
            "I'd have to look that up and I wasn't able to — so I don't want to guess. "
            "Check that the account or folder is connected, then ask me again."
        )

    return reply


def _lower_first(text: str) -> str:
    """First clause of a preview, lowercased, for "I didn't ..." phrasing.

    Previews carry reassurance on the end ("Nothing is deleted; fully
    undoable"), which reads as nonsense once negated -- "I didn't move 97 files
    ... Nothing is deleted". Only the first sentence describes the action.
    """
    if not text:
        return "do that."
    first = text.split(". ")[0].rstrip(".")
    return (first[0].lower() + first[1:]) + "."


def _finish(session_id: str, result: TurnResult) -> TurnResult:
    if result.reply:
        short_term.record(session_id, "assistant", result.reply, result.skill_calls)
    return result

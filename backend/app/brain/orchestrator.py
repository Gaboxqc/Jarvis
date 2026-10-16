"""The turn loop — REQ-1 to REQ-7, REQ-15, REQ-24, REQ-25, REQ-27.

One user message in, one reply out, with the Action Gate in the middle.

Two intents are handled deterministically *before* the model sees them — undo,
and answering a pending confirmation. Both are high-stakes and unambiguous, and
neither should depend on a 4-billion-parameter model reading the room correctly.
Everything else routes through the LLM.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from .. import timings
from ..actions import gate, undo
from ..memory import long_term, short_term
from ..settings import ActionSettings, load_config
from ..skills.base import SkillContext
from ..skills.registry import catalog
from . import intents, llm, prompts

log = logging.getLogger(__name__)

# How many skills one turn may run.
#
# A bound on runaway tool loops, and worth keeping. What was not worth keeping
# is how it was applied: `calls[:3]`, with no word to anyone. The router would
# ask for five, three would run, and the synthesis pass -- which only ever sees
# results -- would describe those three as though they were the whole request.
# Nothing in the reply, the receipts or the logs said otherwise.
#
# Now the router is told the limit (prompts.router_prompt), so it usually does
# not propose more; and when it does anyway, the ones left over are named in
# the reply instead of vanishing.
#
# The value lives in `actions.max_skills_per_turn`, because the loop and the
# prompt both have to agree about it and a module private is not somewhere a
# reader can find the second one. This name is kept, as the default, so the
# sweep tooling and the prompt tests have one number to refer to.
MAX_SKILLS_PER_TURN = ActionSettings.max_skills_per_turn

# How many times one turn may route.
#
# Two, and the second one is conditional. A request whose later step needs an
# earlier step's *output* cannot be planned in one pass -- "find the lease,
# then tell me the deposit" is two tool calls where the second's arguments are
# in the first's result -- so one round is a ceiling on what the assistant can
# be asked, not just on what it costs.
#
# Two rather than "until it stops asking": each round is a model call and a
# batch of real side effects, and a loop with no fixed bound is one bad route
# away from running the Action Gate in circles. Two covers look-then-act, which
# is the shape almost every dependent request has; a third round has yet to be
# wanted by anything.
MAX_ROUNDS = 2

# Recognising what a user means, and what the model claimed, lives in
# brain/intents/ -- one module per language, selected two different ways. See
# its docstring: what the *user* typed is matched in every language at once,
# because a persona set to English is not a promise about what the person will
# type; what the *model* wrote is matched in the persona's language, because
# that is the only language it could be in.


# One lock per session, taken for the whole of a turn.
#
# Two overlapping turns in one session interleave: both record the user's text,
# both then read a window that contains the other's message, both route against
# it, and both can park an action -- so the conversation the model sees is not
# the conversation that happened, and a confirmation can be shown for an action
# proposed in answer to a different question. The UI serializes by disabling
# the composer, which is a property of one client rather than of the API, and
# /turn/stream is long-lived enough for a second request to be ordinary.
#
# Held across the generator rather than around a function call, because the
# turn *is* the generator: released in a `finally`, which runs on normal
# completion and on the GeneratorExit raised when a client disconnects
# mid-stream and the generator is closed.
#
# Sessions are created by name by whoever calls, so the dict is unbounded in
# principle. In practice there are two ("default" and "ui") plus whatever a
# script invents, each entry is a lock and a short string, and reaping them
# would need to know that no turn is about to arrive for a session -- which is
# exactly what the lock is for. Not worth the race.
_session_locks: dict[str, threading.Lock] = {}
_session_locks_guard = threading.Lock()


def _session_lock(session_id: str) -> threading.Lock:
    with _session_locks_guard:
        lock = _session_locks.get(session_id)
        if lock is None:
            lock = threading.Lock()
            _session_locks[session_id] = lock
        return lock


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
    # Skills the user's message asked for that this turn did not run: over the
    # per-turn limit, or behind an action still waiting for an answer. Carried
    # out to the API so a caller can see the difference between "that is
    # everything" and "that is what fitted".
    not_run: list[str] = field(default_factory=list)
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
            "not_run": self.not_run,
            "error": self.error,
        }


def handle_turn(
    text: str,
    session_id: str = "default",
    *,
    pending_action_id: str | None = None,
) -> TurnResult:
    """Run a turn and return the finished result.

    Defined in terms of the streaming path so the two cannot drift. There is
    only one turn loop; this one throws the intermediate events away.
    """
    result = TurnResult(reply="")
    for event in run_turn(text, session_id, pending_action_id=pending_action_id, stream=False):
        if event["type"] == "done":
            result = event["result"]
    return result


def run_turn(
    text: str,
    session_id: str = "default",
    *,
    pending_action_id: str | None = None,
    stream: bool = True,
) -> Iterator[dict[str, Any]]:
    """One turn at a time per session — see `_session_locks`.

    A refusal rather than a queue, and deliberately. A queued turn answers a
    question the user asked before they saw the answer to the previous one, by
    which time they have usually rephrased it or moved on; and queueing behind
    a model call that can take the full timeout means a request that is held
    open for two minutes before anything happens. Being told to wait a moment
    is the honest version of both.

    Nothing is recorded for a refused turn. The user's text does not reach the
    conversation window, so a retry sees exactly the history it would have
    seen, and the refusal is not something the model is later asked to explain.

    `confirm_pending` and `decline_pending` deliberately do not take this lock.
    Answering a confirmation while the turn that proposed it is still finishing
    its stream is ordinary -- the UI can show the buttons before the connection
    closes -- and the gate already protects the thing that matters there, by
    consuming the action id.
    """
    lock = _session_lock(session_id)
    if not lock.acquire(blocking=False):
        language = load_config().persona.language
        log.info("refused a second concurrent turn for session %r", session_id)
        yield {
            "type": "done",
            "result": TurnResult(
                reply=intents.for_language(language).busy,
                error="turn_in_progress",
            ),
        }
        return

    timer = timings.Turn()
    try:
        yield from _run_turn(
            text, session_id, pending_action_id=pending_action_id, stream=stream, timer=timer
        )
    finally:
        # Recorded on every exit, including the GeneratorExit raised when a
        # client disconnects mid-stream. An abandoned turn still cost what it
        # cost, and dropping those would bias the window towards the turns that
        # were waited for -- which is the opposite of the ones worth looking at.
        timer.finish()
        lock.release()


def _run_turn(
    text: str,
    session_id: str = "default",
    *,
    pending_action_id: str | None = None,
    stream: bool = True,
    timer: timings.Turn,
) -> Iterator[dict[str, Any]]:
    """The turn loop, as a sequence of events.

    Yields, in order:

        {"type": "stage",  "stage": "routing" | "working" | "writing"}
        {"type": "delta",  "text": "..."}            (only when stream=True)
        {"type": "done",   "result": TurnResult}

    The stages exist because the slow parts of a turn are the parts that cannot
    stream. Routing has to be parsed and validated whole before anything runs,
    and a skill either finishes or does not. Without stages the interface would
    sit blank through all of that and then produce text in a rush, which reads
    as a hang followed by a burst.

    Nothing streams before the Action Gate has decided. A confirmation prompt is
    emitted whole, as a finished result: streaming it would make a question that
    is waiting for approval look like an answer already being given.
    """
    text = (text or "").strip()
    if not text:
        yield {"type": "done", "result": TurnResult(reply="")}
        return

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
        if intents.affirms(normalized):
            yield {"type": "done", "result": _finish(
                session_id, _run_confirmation(pending_action_id, ctx))}
            return
        if intents.declines(normalized):
            outcome = gate.decline(pending_action_id)
            yield {"type": "done", "result": _finish(
                session_id,
                TurnResult(reply=f"Cancelled. I didn't {_lower_first(outcome.preview)}"),
            )}
            return

    # 2. Undo.
    if intents.is_undo(text):
        result = undo.undo_last()
        yield {"type": "done", "result": _finish(
            session_id,
            TurnResult(reply=result.message, error=None if result.ok else result.message),
        )}
        return

    # 3. Everything else goes through the model.
    facts = long_term.relevant(text)
    system = prompts.system_prompt(config, facts)
    history = [turn.as_message() for turn in short_term.window(session_id)]

    # 3a. A greeting needs an answer, but it never needs a tool. Routing it costs
    # a whole model call to be told so, and that call is the most expensive thing
    # in a turn: 2614ms median against 950ms of synthesis over four real turns
    # against a local qwen2.5 (app/timings.py), so roughly two thirds of the turn
    # spent establishing that "thanks" is not a request.
    if intents.is_pleasantry(normalized):
        yield {"type": "stage", "stage": "writing"}
        yield from _emit_reply(
            session_id, [{"role": "system", "content": system}, *history],
            stream=stream, guard=None, language=config.persona.language, timer=timer,
        )
        return

    yield {"type": "stage", "stage": "routing"}
    try:
        route = _timed_route(
            timer, system, history, text, config.actions.max_skills_per_turn
        )
    except llm.LLMUnavailable as exc:
        # REQ-27: a dead model is reported plainly, and everything stored locally
        # is still reachable through the API and CLI commands.
        yield {"type": "done", "result": _finish(
            session_id, TurnResult(reply=str(exc), error="llm_unavailable"))}
        return

    calls = _extract_calls(route)
    if not calls:
        direct = (route.get("reply") or "").strip() if isinstance(route, dict) else ""
        if direct:
            # The router already wrote an answer. Emitting it whole rather than
            # regenerating it keeps the turn to a single model call.
            reply = _guard_ungrounded_reply(text, direct, [], config.persona.language)
            if stream:
                yield {"type": "delta", "text": reply}
            yield {"type": "done", "result": _finish(session_id, TurnResult(reply=reply))}
            return

        yield {"type": "stage", "stage": "writing"}
        yield from _emit_reply(
            session_id, [{"role": "system", "content": system}, *history],
            stream=stream, guard=text, language=config.persona.language, timer=timer,
        )
        return

    # 4. Execute, stopping at the first action that needs approval.
    #
    # Up to MAX_ROUNDS of it. One round cannot answer "find the lease, then
    # tell me what the deposit was": the second step's arguments are in the
    # first step's output, so a router asked to plan both in one shot has to
    # guess at them. A second pass, given the results, does not.
    #
    # It happens only when the router asked for it. A routing call is half the
    # latency of a turn and almost no request needs two, so the first round
    # sets `needs_followup` when a later step depends on an earlier one's
    # output -- and a model that ignores the field gets precisely the
    # behaviour this had before, which is what makes it safe to ask for.
    yield {"type": "stage", "stage": "working"}
    batch_id = gate.new_batch_id()
    results: list[dict[str, Any]] = []
    not_run: list[str] = []
    budget = config.actions.max_skills_per_turn

    for round_number in range(1, MAX_ROUNDS + 1):
        deferred = [call["name"] for call in calls[budget:]]
        if deferred:
            log.info(
                "router asked for %d skills; running %d, deferring %s",
                len(calls),
                budget,
                ", ".join(deferred),
            )

        round_results: list[dict[str, Any]] = []
        parked: gate.GateOutcome | None = None
        for call in calls[:budget]:
            with timer.stage(timings.SKILLS):
                outcome = gate.submit(
                    call["name"], call.get("args") or {}, ctx, batch_id=batch_id
                )
            round_results.append(outcome.to_dict())
            results.append(outcome.to_dict())

            if outcome.status == gate.NEEDS_CONFIRMATION:
                # Nothing after this runs, in this round or any later one. A
                # later step might depend on this one, and queuing side effects
                # behind an unanswered question is how a "no" ends up having
                # done half the work anyway.
                parked = outcome
                break
            # Counted here rather than from `len(results)`, which includes the
            # one that stopped to ask: a turn that proposed an action and ran
            # nothing is not a turn that ran a skill, and the timings panel
            # would otherwise attribute a confirmation prompt's latency to work.
            timer.ran_skill()

        if parked is not None:
            # Emitted whole, never streamed: a question awaiting approval must
            # not arrive looking like an answer already being given.
            pending = PendingAction(
                action_id=parked.action_id or "",
                skill=parked.skill_name,
                preview=parked.preview,
                reversible=parked.reversible,
            )
            yield {"type": "done", "result": _finish(
                session_id,
                TurnResult(
                    reply=prompts.confirmation_prompt(parked.preview, parked.reversible),
                    pending=pending,
                    skill_calls=results,
                    not_run=not_run + _not_run(calls, round_results, deferred, budget),
                ),
            )}
            return

        not_run += deferred

        if round_number >= MAX_ROUNDS or not _wants_followup(route):
            break

        # Re-route, with what just happened in front of the model.
        follow_on = _timed_route(timer, system, history, text, budget, results=results)
        next_calls = _extract_calls(follow_on)
        if not next_calls:
            # It looked at the results and had nothing more to ask for, which
            # is a legitimate answer and the common one. Any text it wrote is
            # left alone; the synthesis pass below is what phrases the reply.
            break
        log.info("round %d: %d more skill(s)", round_number + 1, len(next_calls))
        calls, route = next_calls, follow_on

    # 5. Say what happened.
    yield {"type": "stage", "stage": "writing"}
    written = ""
    try:
        for kind, payload in _timed(
            timer,
            _generate(_synthesis_messages(system, history, results, not_run), stream=stream),
        ):
            if kind == "delta":
                yield {"type": "delta", "text": payload}
            else:
                written = payload
    except llm.LLMUnavailable:
        # The work already happened; report it verbatim rather than losing it
        # because the phrasing pass could not run (REQ-27).
        written = ""

    reply = _append_receipts(written or _fallback_summary(results, not_run), results)
    reply = _guard_ungrounded_reply(text, reply, results, config.persona.language)

    # Receipts and the guard can both change text already shown, so the final
    # result carries the authoritative version for clients to settle on.
    yield {"type": "done", "result": _finish(
        session_id,
        TurnResult(reply=reply, skill_calls=results, not_run=not_run))}


def confirm_pending(action_id: str, session_id: str = "default") -> TurnResult:
    """Direct confirmation path used by the API and by UI buttons."""
    ctx = SkillContext(session_id=session_id, config=load_config())
    return _finish(session_id, _run_confirmation(action_id, ctx))


def decline_pending(action_id: str, session_id: str = "default") -> TurnResult:
    outcome = gate.decline(action_id)
    return _finish(session_id, TurnResult(reply=f"Cancelled. I didn't {_lower_first(outcome.preview)}"))


# -- internals -------------------------------------------------------------


def _generate(
    messages: list[dict[str, str]], *, stream: bool
) -> Iterator[tuple[str, str]]:
    """Produce a reply, in pieces or whole.

    Yields ("delta", piece) zero or more times, then ("text", whole) exactly
    once. The non-streaming branch still goes through llm.chat rather than
    draining llm.stream, so a single request is a single request either way --
    and so the hundreds of tests that stub llm.chat keep testing the path they
    were written for.
    """
    if not stream:
        yield ("text", llm.chat(messages).text)
        return

    parts: list[str] = []
    for piece in llm.stream(messages):
        parts.append(piece)
        yield ("delta", piece)
    yield ("text", "".join(parts))


def _timed_route(
    timer: timings.Turn,
    system: str,
    history: list[dict[str, str]],
    text: str,
    budget: int,
    *,
    results: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """`_route`, on the clock, counted as a round even when it raises.

    Both call sites go through this so neither can be the one that forgets:
    the follow-up round was added as an opt-in because a routing call is the
    most expensive stage of a turn (see MAX_ROUNDS), and a timings panel that
    could not see it would leave that claim exactly as unmeasured as before.

    It is measured now, and it was worse than the comments here claimed: 2614ms
    median for routing against 950ms for synthesis over four real turns, where
    this file used to put a routing call at "half the latency of a turn". A
    second pass does not add half a turn. It adds most of one.
    """
    timer.routed()
    with timer.stage(timings.ROUTING):
        return _route(system, history, text, budget, results=results)


def _timed(
    timer: timings.Turn, source: Iterator[tuple[str, str]]
) -> Iterator[tuple[str, str]]:
    """Charge synthesis for the model's time, not the client's.

    The clock runs while `next()` is in flight and stops while the value is
    downstream. Wrapping the whole `for` in one `with` would have charged the
    synthesis stage for the HTTP write of every delta -- over a slow connection
    most of the number, and over the SSE endpoint a number that says more about
    the reader than about the model.
    """
    while True:
        with timer.stage(timings.SYNTHESIS):
            try:
                item = next(source)
            except StopIteration:
                return
        yield item


def _emit_reply(
    session_id: str,
    messages: list[dict[str, str]],
    *,
    stream: bool,
    guard: str | None,
    language: str = "en",
    timer: timings.Turn,
) -> Iterator[dict[str, Any]]:
    """A turn that answers in prose, with no skills involved.

    `guard` is the user's text when the reply needs checking for invented
    answers, and None when it cannot need it -- a greeting has no external data
    to get wrong.
    """
    written = ""
    try:
        for kind, payload in _timed(timer, _generate(messages, stream=stream)):
            if kind == "delta":
                yield {"type": "delta", "text": payload}
            else:
                written = payload
    except llm.LLMUnavailable as exc:
        yield {"type": "done", "result": _finish(
            session_id, TurnResult(reply=str(exc), error="llm_unavailable"))}
        return

    reply = (
        _guard_ungrounded_reply(guard, written, [], language)
        if guard is not None
        else written
    )
    yield {"type": "done", "result": _finish(session_id, TurnResult(reply=reply))}


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


def _route(
    system: str,
    history: list[dict[str, str]],
    text: str,
    budget: int,
    *,
    results: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Decide which skills, if any, this turn needs.

    Two mechanisms, same return shape. Where the model advertises tool-calling
    the tools go in the request's own tools field, which is both more accurate
    and less prompt to carry. Where it does not -- llama3, and most of what
    Ollama serves -- the tools are described in the prompt and the answer comes
    back as JSON. The JSON path is the floor, not a legacy branch: it is what
    keeps the assistant working when someone changes one line of config.

    `results` is what already ran this turn, on the second pass. The user's
    message is still the last thing in the list -- small models reliably answer
    the final user turn, and the request is what is being routed; the results
    go in ahead of it as context, so the second pass is "given this, what else
    does that need" rather than "comment on this".
    """
    available = catalog()
    if not available:
        return None

    context: list[dict[str, str]] = []
    if results:
        context = [{"role": "user", "content": prompts.results_so_far(results)}]

    if load_config().brain.native_tools and llm.supports_tools():
        messages = [
            {"role": "system", "content": system},
            {"role": "system", "content": prompts.native_router_prompt()},
            *history[-6:],
            *context,
            {"role": "user", "content": text},
        ]
        reply = llm.chat(
            messages, temperature=0.0, tools=prompts.tool_schemas(available)
        )
        # A reply with no tool calls is a legitimate answer, not a failure: the
        # model decided nothing needed running. Carrying its text through means
        # the caller can use it instead of paying for a second call.
        return {"skills": reply.tool_calls, "reply": reply.text or None}

    messages = [
        {"role": "system", "content": system},
        {"role": "system", "content": prompts.router_prompt(available, budget)},
        *history[-6:],
        *context,
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


def _wants_followup(route: dict[str, Any] | None) -> bool:
    """Whether the router said it will need to see these results.

    Opt-in, and the model opts in. A second routing pass costs more than the
    synthesis that writes the answer -- 2614ms median against 950ms, measured
    -- and almost no request needs one, so this is asked for in the prompt
    rather than assumed, and a model that never sets it behaves exactly as this
    did before the field existed.

    Anything other than a true-ish `needs_followup` is a no, including the
    absent key and a model that returned a string: a turn that re-routes
    because it misread a reply is a turn that runs side effects twice.
    """
    if not isinstance(route, dict):
        return False
    return route.get("needs_followup") is True


def _not_run(
    calls: list[dict[str, Any]],
    results: list[dict[str, Any]],
    deferred: list[str],
    budget: int,
) -> list[str]:
    """Everything asked for that this turn will not do.

    Two sources, and the second is easy to miss. Stopping at an action that
    needs approval abandons the rest of the batch as well -- deliberately, so
    a "no" cannot have already done half the work -- but those calls are just
    as absent from the reply as the ones over the limit, and until now just as
    unmentioned.
    """
    ran = len(results)
    abandoned = [call["name"] for call in calls[ran:budget]]
    return abandoned + deferred


def _synthesis_messages(
    system: str,
    history: list[dict[str, str]],
    results: list[dict[str, Any]],
    not_run: list[str] | None = None,
) -> list[dict[str, str]]:
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
    return [
        {"role": "system", "content": system},
        *history[-4:],
        {"role": "user", "content": prompts.synthesis_prompt(payload, not_run)},
    ]


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


def _fallback_summary(
    results: list[dict[str, Any]], not_run: list[str] | None = None
) -> str:
    """What the user is told when the phrasing pass could not run.

    This is the path where honesty matters most: the model is unavailable, so
    there is nothing to notice an omission on the user's behalf.
    """
    lines = [r["message"] for r in results if r.get("message")]
    if not_run:
        lines.append(
            "I didn't get to " + ", ".join(not_run) + " — ask me and I'll do that next."
        )
    return "\n".join(lines) or "Done."


def _guard_ungrounded_reply(
    user_text: str,
    reply: str,
    results: list[dict[str, Any]],
    language: str = "en",
) -> str:
    """Catch answers the model had no way to know — REQ-27, REQ-28.

    When routing fails, a small model does not say "I can't check that". It
    invents a plausible answer: meetings with people who don't exist, or a
    confirmation that it added an event when nothing ran. Both are worse than an
    error, because they are indistinguishable from success.

    The system prompt already forbids this and is ignored under pressure, so it
    is enforced here instead. Both checks require that *no skill executed*, which
    is what makes them safe: a grounded turn is never touched.

    `language` is not decoration. The patterns were English-only for as long as
    this existed, so a persona set to Spanish -- a language the app ships, with
    its own system-prompt instruction to answer in it -- had no guard at all.
    The model invented in Spanish and nothing looked.
    """
    if any(r.get("status") == gate.EXECUTED for r in results):
        return reply
    if not reply:
        return reply

    guard = intents.for_language(language)

    # Claiming to have done something, having done nothing.
    if guard.action_claim.search(reply):
        log.warning("blocked an unperformed action claim: %r", reply[:120])
        return guard.unperformed

    # Answering about a data source that requires a tool it never called.
    if guard.external_data.search(user_text):
        log.warning("blocked an ungrounded answer about external data: %r", reply[:120])
        return guard.ungrounded

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

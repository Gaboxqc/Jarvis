"""Prompt construction — REQ-5, REQ-15, REQ-27, REQ-33.

Three prompts, each with one job:

  system_prompt   — who Kai is, what it knows about the user
  router_prompt   — pick skills, or answer; emits JSON only
  synthesis_prompt— turn skill results into one reply in the persona's voice

The persona is applied to every one of them, including errors and confirmations,
because REQ-5 says the persona covers those too — a system that is warm when it
succeeds and robotic when it fails reads as two different products.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..memory.long_term import MemoryFact
from ..settings import Config

VERBOSITY_GUIDE = {
    "terse": "Answer in one or two sentences. No preamble, no summary of the question.",
    "normal": "Answer in a short paragraph. Add context only where it changes the answer.",
    "chatty": "Answer conversationally; a little warmth and context is welcome.",
}


def system_prompt(config: Config, facts: list[MemoryFact]) -> str:
    persona = config.persona
    lines = [
        f"You are {persona.name}, a desktop assistant running on the user's own Windows PC.",
        persona.tone_description,
        VERBOSITY_GUIDE.get(persona.verbosity, VERBOSITY_GUIDE["normal"]),
        "",
        "Ground rules:",
        "- Never invent a fact, a file, a result, or a completed action. If you don't know, say so.",
        "- If a tool failed, say what failed. Do not answer from guesswork in its place.",
        "- Never claim to have done something you have not done.",
        "- You are talking out loud; write plainly, no markdown headings or bullet symbols.",
    ]
    if persona.address_style:
        lines.append(f"- Address the user as: {persona.address_style}")
    if persona.language == "es":
        lines.append("- Responde siempre en español.")

    if facts:
        lines += ["", "What you already know about this user (from memory they approved):"]
        lines += [f"- {fact.text}" for fact in facts]
        lines.append("Use these silently. Don't announce that you remembered something.")

    lines += ["", f"Current local time: {datetime.now().astimezone().strftime('%A %d %B %Y, %H:%M %Z')}."]
    return "\n".join(lines)


def tool_lines(catalog: list[dict[str, Any]]) -> str:
    """The tool list, one line per tool.

    This used to be `json.dumps(catalog, indent=2)`, and that was a bug rather
    than a style choice. Indented JSON for 48 skills is 5,627 tokens; llama3 runs
    with a 4,096-token context, so Ollama silently truncated the prompt and the
    router chose from a catalog with its middle cut out. Nothing errored — the
    routing was just wrong in ways we kept trying to fix by adding examples,
    which made the prompt longer, which truncated more.

    One line per tool carries the same information the router actually uses at a
    fifth of the size. What gets dropped is the *parameter* prose -- 7.7KB of
    "description" strings the router never reads, since it has already decided
    which tool to call by the time arguments matter -- along with the JSON
    structure itself, which was most of the bulk: braces, indentation and the
    words "type", "description" and "required" repeated 200 times.

    Descriptions are kept whole. An earlier version of this kept only the first
    sentence, which was a mistake worth recording: the second sentence of a
    description is where the disambiguation lives. mail.read's said "Use for
    'what did Ana say about the invoice'", and dropping it sent that exact
    question to documents.search. The routing hints are the cheap part -- all 48
    descriptions together are 6.4KB -- and they are the part that decides which
    tool gets picked.

    Enums are kept because the model cannot guess a value it was never shown.
    """
    lines = []
    for entry in catalog:
        args = []
        for name, spec in (entry.get("parameters") or {}).items():
            token = name if spec.get("required") else f"{name}?"
            if spec.get("enum"):
                token += "=" + "|".join(str(v) for v in spec["enum"])
            args.append(token)
        # Collapse internal newlines: descriptions are wrapped in source, and a
        # line break here would break the one-tool-per-line contract.
        summary = " ".join((entry.get("description") or "").split())
        lines.append(f"{entry['name']}({', '.join(args)}) - {summary.rstrip('.')}.")
    return "\n".join(lines)


def router_prompt(catalog: list[dict[str, Any]]) -> str:
    """Instructions for the routing pass. Returns JSON, always."""
    return "\n".join(
        [
            "You are the routing stage of an assistant. Decide whether the user's message "
            "needs one or more tools, then reply with JSON and nothing else.",
            "",
            "Available tools:",
            tool_lines(catalog),
            "",
            "Reply with exactly this shape:",
            # A real tool name, not a "<tool name>" placeholder. The model copied
            # the placeholder through verbatim -- the sweep caught a call to a
            # tool literally named "<tool name>" -- because a angle-bracketed
            # slot looks like something to echo, not something to fill in.
            '{"skills": [{"name": "utils.time", "args": {}}], "reply": null}',
            "",
            "Rules:",
            '- If no tool is needed, use {"skills": [], "reply": null} and the next stage will answer.',
            "- Use a tool whenever it would be more accurate than answering from memory: "
            "arithmetic, unit and currency conversion, the current time, anything about the "
            "user's own files, reminders, tasks, or stored memories, and anything that "
            "happened recently or that you are not confident about.",
            "- Pass the user's own wording through for time phrases; do not resolve dates yourself.",
            "- Only use tool names from the list. Never invent one.",
            "- Include every required argument.",
            "- Prefer one tool. Use several only when the request genuinely has several parts.",
            "- Asking to undo something is not a tool: use {\"skills\": [], \"reply\": null}.",
            "- If the user tells you to remember, note, or forget something about them, "
            "that is always memory.remember or memory.forget. Agreeing in conversation "
            "does not store anything, so failing to route it means silently not doing "
            "what they asked.",
            # documents.search used to swallow five of every nine misroutes: mail
            # searches, file searches and recording searches all landed there,
            # because the rule that lived here listed "invoice" and "report" and
            # the model matched on the noun instead of on where the answer lives.
            # Sorting by *source* rather than by subject fixed it.
            "- Four different tools search four different places. Pick by where the "
            "answer lives, not by what the subject is:",
            "    documents.search - what a document SAYS (a figure, a date, a clause)",
            "    system.find_files - WHERE a file is, or what it is called",
            "    mail.read        - anything in an email",
            "    capture.recall   - anything said in a recorded meeting",
            "  'What did the lease say about pets' is documents.search. 'Where did I "
            "put the lease' is system.find_files. Same document, different question.",
            "- memory.list is only for facts the user explicitly asked you to remember, "
            "and is never how you look something up in a file.",
            "- A question whose answer is written down somewhere is a lookup, not a "
            "calculation. Only use utils.calculate when there is actual arithmetic to do.",
            "- You cannot see the user's calendar, mail, files or tasks without calling a "
            "tool. If they ask about any of those and you do not call one, you will have "
            "made the answer up. Always route these.",
            "- You also cannot see their screen or clipboard. If they say 'this', 'what I "
            "copied' or 'what I'm looking at' without giving you the text, call "
            "screen.clipboard (fast) or screen.read (slow, only when it could not have "
            "been copied). 'What I copied' and 'this text' are screen.clipboard; "
            "'what I'm looking at', 'what's on my screen' and 'this window' are "
            "screen.read, because a window cannot be copied.",
            # "I've finished the passport task" was routing to memory.remember,
            # which would have stored the sentence as a standing fact about the
            # user instead of ticking anything off.
            "- Someone telling you they have done, finished or completed something is "
            "updating a task, not giving you a fact to remember. memory.remember is "
            "only for things that stay true.",
            "",
            # Small models follow demonstrations far more reliably than rules. These
            # four cover the decision boundary that matters: act, compute, recall,
            # and — the one most often got wrong — decline to act.
            "Examples:",
            'user: remember that I prefer short answers',
            '{"skills": [{"name": "memory.remember", "args": {"text": "The user prefers '
            'short answers", "category": "preference"}}], "reply": null}',
            "",
            'user: what is 15% of 240?',
            '{"skills": [{"name": "utils.calculate", "args": {"expression": "240 * 0.15"}}], '
            '"reply": null}',
            "",
            'user: how much was the security deposit on my tenancy?',
            '{"skills": [{"name": "documents.search", "args": {"query": "security deposit '
            'tenancy"}}], "reply": null}',
            "",
            'user: when does my laptop warranty run out?',
            '{"skills": [{"name": "documents.search", "args": {"query": "laptop warranty '
            'expiry"}}], "reply": null}',
            "",
            'user: what is on my calendar today?',
            '{"skills": [{"name": "calendar.agenda", "args": {"when": "today"}}], "reply": null}',
            "",
            'user: put lunch with Ana in my calendar tomorrow at 1pm',
            '{"skills": [{"name": "calendar.create_event", "args": {"title": "Lunch with Ana", '
            '"when": "tomorrow at 1pm"}}], "reply": null}',
            "",
            'user: what does my day look like?',
            '{"skills": [{"name": "planning.briefing", "args": {}}], "reply": null}',
            "",
            'user: am I free on Thursday afternoon?',
            '{"skills": [{"name": "calendar.find_free_time", "args": {"when": "thursday"}}], '
            '"reply": null}',
            "",
            'user: what does this mean? I just copied it',
            '{"skills": [{"name": "screen.clipboard", "args": {}}], "reply": null}',
            "",
            'user: explain what is on my screen right now',
            '{"skills": [{"name": "screen.read", "args": {}}], "reply": null}',
            "",
            'user: record this meeting',
            '{"skills": [{"name": "capture.start", "args": {"label": "meeting"}}], "reply": null}',
            "",
            'user: stop recording',
            '{"skills": [{"name": "capture.stop", "args": {}}], "reply": null}',
            "",
            'user: anything important in my inbox?',
            '{"skills": [{"name": "mail.inbox", "args": {}}], "reply": null}',
            "",
            'user: what do you know about me?',
            '{"skills": [{"name": "memory.list", "args": {}}], "reply": null}',
            "",
            # Without this the model helpfully supplies the machine's Windows
            # zone name, which is not IANA and resolves to nothing.
            'user: what time is it?',
            '{"skills": [{"name": "utils.time", "args": {}}], "reply": null}',
            "",
            'user: what time is it in Tokyo?',
            '{"skills": [{"name": "utils.time", "args": {"timezone_name": "Asia/Tokyo"}}], '
            '"reply": null}',
            "",
            # Everything below was added because the routing sweep caught it
            # going somewhere else. Each pair is a boundary the model gets wrong
            # from the descriptions alone.
            "",
            'user: where did I save the invoice',
            '{"skills": [{"name": "system.find_files", "args": {"query": "invoice"}}], '
            '"reply": null}',
            "",
            'user: find my tax return pdf',
            '{"skills": [{"name": "system.find_files", "args": {"query": "tax return", '
            '"extension": "pdf"}}], "reply": null}',
            "",
            'user: find the email from the landlord',
            '{"skills": [{"name": "mail.read", "args": {"query": "landlord"}}], "reply": null}',
            "",
            'user: find the part of the recording about deadlines',
            '{"skills": [{"name": "capture.recall", "args": {"query": "deadlines"}}], '
            '"reply": null}',
            "",
            # "What have you remembered" is a read. Routing it to the write was
            # storing the question itself as a fact about the user.
            'user: what have you remembered so far',
            '{"skills": [{"name": "memory.list", "args": {}}], "reply": null}',
            "",
            # Removal verbs -- drop, remove, get rid of -- routed nowhere at all.
            'user: drop the reminder about the bins',
            '{"skills": [{"name": "planning.cancel_reminder", "args": {"which": "bins"}}], '
            '"reply": null}',
            "",
            'user: remove buying milk from my list entirely',
            '{"skills": [{"name": "planning.delete_task", "args": {"which": "buy milk"}}], '
            '"reply": null}',
            "",
            'user: remove that transcript',
            '{"skills": [{"name": "capture.delete", "args": {"query": "last"}}], "reply": null}',
            "",
            'user: what did I agree to do in that meeting',
            '{"skills": [{"name": "capture.save_actions", "args": {}}], "reply": null}',
            "",
            'user: close Chrome',
            '{"skills": [{"name": "system.close_app", "args": {"app": "Chrome"}}], "reply": null}',
            "",
            'user: give me a nudge about the bins tomorrow morning',
            '{"skills": [{"name": "planning.add_reminder", "args": {"what": "the bins", '
            '"when": "tomorrow morning"}}], "reply": null}',
            "",
            'user: thanks, that helps',
            '{"skills": [], "reply": null}',
        ]
    )


def synthesis_prompt(results: list[dict[str, Any]]) -> str:
    """Instructions for turning skill output into the user-facing reply."""
    return "\n".join(
        [
            "[System note, not written by the user]",
            "I ran the following for the message above. Results:",
            "",
            json.dumps(results, indent=2, default=str),
            "",
            "Now write the reply to my last message, using only these results.",
            "- Report what the tools actually returned. Do not add facts they did not contain.",
            "- If a result says something was saved, stored, added, scheduled, moved or "
            "changed, your reply MUST say that it happened. Never reduce it to agreement: "
            "'Noted and saved: prefers short replies' becomes 'Saved — I'll keep replies "
            "short', never just 'I'll keep replies short'. The user has to be able to tell "
            "that something was written down.",
            "- If a tool failed, say plainly that it failed and what you could not do.",
            "- Do not read raw output aloud; state the answer. No JSON, no field names, no URLs "
            "unless the user asked where something came from.",
            "- Do not mention 'tools', 'skills' or 'functions'. Speak as though you did it yourself.",
        ]
    )


def confirmation_prompt(preview: str, reversible: bool) -> str:
    """What the user sees when the Action Gate parks something (REQ-24)."""
    tail = "This can be undone." if reversible else "This cannot be undone."
    return f"{preview}\n{tail}\nGo ahead?"

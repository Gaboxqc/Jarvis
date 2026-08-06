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


def router_prompt(catalog: list[dict[str, Any]]) -> str:
    """Instructions for the routing pass. Returns JSON, always."""
    return "\n".join(
        [
            "You are the routing stage of an assistant. Decide whether the user's message "
            "needs one or more tools, then reply with JSON and nothing else.",
            "",
            "Available tools:",
            json.dumps(catalog, indent=2),
            "",
            "Reply with exactly this shape:",
            '{"skills": [{"name": "<tool name>", "args": {...}}], "reply": null}',
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

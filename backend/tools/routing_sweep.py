"""Ask the router one plain question per skill and see what it picks.

This exists because of what the truncation bug revealed. The tool catalog was
being cut in half before the model ever saw it, so for most of the 48 skills
nobody had ever observed whether routing to them worked at all. Two of the
twelve cases in the first spot-check turned out to be hallucinated names. The
other thirty-six skills were simply never checked.

Unit tests cannot cover this. The question is not "does the code work" but
"does an 8B model, reading this catalog, pick this skill when a person asks for
it in their own words" -- which depends on the prompt, the model, and the
wording, and can only be answered by asking.

    python backend/tools/routing_sweep.py            # everything
    python backend/tools/routing_sweep.py --only mail
    python backend/tools/routing_sweep.py --model qwen2.5

IMPORTANT: this routes and never executes. It calls the routing pass directly
and throws the answer away, so it never reaches the Action Gate. Half of these
phrases describe destructive actions, and a sweep that ran them would delete the
user's files to find out whether it could.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.brain import llm, prompts  # noqa: E402
from app.skills.registry import catalog, load_skills  # noqa: E402

# Two phrasings per skill: one close to how the catalog describes it, one the
# way somebody would actually say it. The second is where routing tends to fail,
# and it is the one that matters.
#   ("phrase", "skill")                  exactly this skill
#   ("phrase", ("skill_a", "skill_b"))    either is acceptable
#   ("phrase", "")                        must not route at all
Want = str | tuple[str, ...]

CASES: list[tuple[str, Want]] = [
    # -- calendar ---------------------------------------------------------
    ("what's on my calendar today?", "calendar.agenda"),
    ("do I have anything on tomorrow", "calendar.agenda"),
    ("put lunch with Ana in my calendar tomorrow at 1pm", "calendar.create_event"),
    ("book a dentist appointment for Friday at 9", "calendar.create_event"),
    ("cancel the standup meeting", "calendar.cancel_event"),
    ("delete the lunch with Ana from my calendar", "calendar.cancel_event"),
    ("am I free on Thursday afternoon?", "calendar.find_free_time"),
    ("when could I fit in an hour this week", "calendar.find_free_time"),
    # -- mail -------------------------------------------------------------
    ("anything important in my inbox?", "mail.inbox"),
    ("do I have new email", "mail.inbox"),
    ("find the email from the landlord", "mail.read"),
    ("what did Ana say in her message about the invoice", "mail.read"),
    ("write a reply to Ana saying I'll be late", "mail.draft"),
    ("draft an email to bob@example.com about the invoice", "mail.draft"),
    # Routing these to mail.draft is correct, not a miss: mail.draft's own
    # description says "Always use this before mail.send", and REQ-14 requires
    # the user to see a message before it goes. Either answer is right.
    ("send that email to ana@example.com", ("mail.send", "mail.draft")),
    ("email bob@example.com the subject Invoice saying it is attached",
     ("mail.send", "mail.draft")),
    ("mark the landlord email as read", "mail.mark"),
    ("flag the message from Ana", "mail.mark"),
    # -- documents --------------------------------------------------------
    ("how much was the security deposit on my tenancy?", "documents.search"),
    ("when does my laptop warranty run out?", "documents.search"),
    ("have you finished indexing my documents", "documents.index_status"),
    ("how many files have you indexed", "documents.index_status"),
    ("scan my documents folder again", "documents.reindex"),
    ("re-index my files", "documents.reindex"),
    # -- memory -----------------------------------------------------------
    ("remember that I prefer short answers", "memory.remember"),
    ("note that my sister's birthday is in March", "memory.remember"),
    ("what do you know about me?", "memory.list"),
    ("what have you remembered so far", "memory.list"),
    ("forget what I told you about my sister", "memory.forget"),
    ("delete the thing you remembered about short answers", "memory.forget"),
    # -- planning ---------------------------------------------------------
    ("remind me to call the dentist at 5pm", "planning.add_reminder"),
    ("give me a nudge about the bins tomorrow morning", "planning.add_reminder"),
    ("what reminders do I have", "planning.list_reminders"),
    ("list my reminders", "planning.list_reminders"),
    ("cancel the dentist reminder", "planning.cancel_reminder"),
    ("drop the reminder about the bins", "planning.cancel_reminder"),
    ("add a task to buy milk", "planning.add_task"),
    ("put renewing my passport on my to-do list", "planning.add_task"),
    ("what's on my to-do list", "planning.list_tasks"),
    ("show me my tasks", "planning.list_tasks"),
    ("mark buying milk as done", "planning.complete_task"),
    ("I've finished the passport task", "planning.complete_task"),
    ("delete the milk task", "planning.delete_task"),
    ("remove buying milk from my list entirely", "planning.delete_task"),
    ("what does my day look like?", "planning.briefing"),
    ("give me my morning briefing", "planning.briefing"),
    # -- screen and clipboard ---------------------------------------------
    ("what does this mean? I just copied it", "screen.clipboard"),
    ("explain what I copied", "screen.clipboard"),
    ("explain what is on my screen right now", "screen.read"),
    ("what am I looking at", "screen.read"),
    ("copy that to my clipboard", "screen.copy"),
    ("put the address on my clipboard", "screen.copy"),
    # -- capture ----------------------------------------------------------
    ("record this meeting", "capture.start"),
    ("start recording", "capture.start"),
    ("stop recording", "capture.stop"),
    ("that's enough, end the recording", "capture.stop"),
    ("are you recording right now", "capture.status"),
    ("how long have you been recording", "capture.status"),
    ("what did we say about the budget in that meeting", "capture.recall"),
    ("find the part of the recording about deadlines", "capture.recall"),
    ("list my recordings", "capture.list"),
    ("what meetings have you transcribed", "capture.list"),
    ("delete the recording from Tuesday", "capture.delete"),
    ("remove that transcript", "capture.delete"),
    # Asking what you agreed to is a question about the recording; being told to
    # save the action items is an instruction. capture.recall answers the first
    # honestly, so both are acceptable and only the imperative must hit
    # save_actions.
    ("what did I agree to do in that meeting", ("capture.save_actions", "capture.recall")),
    ("pull the action items out of the recording", "capture.save_actions"),
    # -- system -----------------------------------------------------------
    ("open Spotify", "system.launch_app"),
    ("launch notepad", "system.launch_app"),
    ("close Chrome", "system.close_app"),
    ("quit Spotify", "system.close_app"),
    ("turn the volume down", "system.control"),
    ("mute the sound", "system.control"),
    ("find my tax return pdf", "system.find_files"),
    ("where did I save the invoice", "system.find_files"),
    ("tidy up my downloads folder", "system.organize_folder"),
    ("sort the files in Downloads into folders", "system.organize_folder"),
    ("start a focus session for an hour", "system.start_focus"),
    ("help me concentrate for 25 minutes", "system.start_focus"),
    ("stop the focus session", "system.end_focus"),
    ("end focus mode", "system.end_focus"),
    ("am I in a focus session", "system.focus_status"),
    ("how long is left on my focus session", "system.focus_status"),
    ("what have you done recently", "system.action_history"),
    ("show me your action history", "system.action_history"),
    # -- utils ------------------------------------------------------------
    ("what is 15% of 240?", "utils.calculate"),
    ("what's 1200 times 1.21", "utils.calculate"),
    ("how many kilometres is 12 miles", "utils.convert"),
    ("convert 80 fahrenheit to celsius", "utils.convert"),
    ("how much is 250 euros in dollars", "utils.currency"),
    ("convert 100 usd to pesos", "utils.currency"),
    ("what time is it?", "utils.time"),
    ("what time is it in Tokyo?", "utils.time"),
    # -- knowledge --------------------------------------------------------
    ("search the web for the tallest building in the world", "knowledge.web_search"),
    ("look up who won the world cup in 2022", "knowledge.web_search"),
    # -- and one that must route nowhere ----------------------------------
    ("thanks, that helps", ""),
    ("that's interesting", ""),
]


# Phrasings the router prompt was never tuned against, covering the boundaries
# the main sweep found broken. Run with --holdout.
#
# This set exists to keep the sweep honest. Once a failing phrase is fixed by
# adding a worked example for that phrase, re-running the sweep measures whether
# the model can recite the example, not whether it can route. These ask for the
# same things in words that appear nowhere in the prompt, so a score here is a
# score for the rule rather than for the demonstration.
HELDOUT: list[tuple[str, Want]] = [
    # source disambiguation -- the confusion documents.search used to swallow
    ("which folder is my passport scan in", "system.find_files"),
    ("locate the spreadsheet with the budget", "system.find_files"),
    ("what did the plumber write back", "mail.read"),
    ("check what my boss said about Friday", "mail.read"),
    ("what notice period does my contract require", "documents.search"),
    ("what was the excess on the insurance policy", "documents.search"),
    ("what came up about hiring on the call", "capture.recall"),
    # removal verbs
    ("get rid of the reminder about the car", "planning.cancel_reminder"),
    ("take the passport task off my list", "planning.delete_task"),
    ("throw away Tuesday's recording", "capture.delete"),
    ("wipe what you know about my diet", "memory.forget"),
    # read vs write
    ("remind me what you've got stored on me", "memory.list"),
    ("I sorted out the car insurance already", "planning.complete_task"),
    ("ticked off buying milk", "planning.complete_task"),
    # screen vs clipboard
    ("what's this window showing", "screen.read"),
    ("summarise what's in front of me", "screen.read"),
    ("what's in my clipboard", "screen.clipboard"),
    # app control
    ("shut down Firefox", "system.close_app"),
    ("fire up the calculator", "system.launch_app"),
    # calendar
    ("scrub the 3pm from Thursday", "calendar.cancel_event"),
    ("block out an hour Monday for the review", "calendar.create_event"),
    ("have I got much on this week", "calendar.agenda"),
    # must not route
    ("that makes sense", ""),
    ("ha, fair enough", ""),
]


def route_once(prompt: str, text: str, model: str, *, native: bool = False) -> tuple[str, dict]:
    """One routing call. Returns the chosen skill name and its arguments.

    `native` picks the tool-calling path instead of the JSON prompt, so the two
    mechanisms can be scored against each other on identical cases.
    """
    settings = llm.load_config().brain
    if model:
        settings = replace(settings, model=model)

    if native:
        messages = [
            {"role": "system", "content": prompts.native_router_prompt()},
            {"role": "user", "content": text},
        ]
        reply = llm.chat(messages, temperature=0.0, settings=settings,
                         tools=prompts.tool_schemas(catalog()))
        if not reply.tool_calls:
            return "", {}
        first = reply.tool_calls[0]
        return str(first.get("name") or "<unnamed>"), (first.get("args") or {})

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": text},
    ]
    reply = llm.chat(messages, json_mode=True, temperature=0.0, settings=settings)
    parsed = reply.as_json() or {}
    skills = parsed.get("skills") or []
    if not isinstance(skills, list) or not skills:
        return "", {}
    first = skills[0]
    if not isinstance(first, dict):
        return "<malformed>", {}
    return str(first.get("name") or "<unnamed>"), (first.get("args") or {})


def run(prompt, cases, model, known, required, *, verbose=True, native=False) -> tuple[list[dict], float]:
    """Route every case once and classify the answer."""
    rows = []
    started = time.perf_counter()
    for text, want in cases:
        got, got_args = route_once(prompt, text, model, native=native)

        acceptable = (want,) if isinstance(want, str) else want
        if got in acceptable:
            verdict = "ok"
        elif got and got not in known:
            # The failure mode truncation produced: a plausible name for a tool
            # that does not exist. Worth counting separately -- it means the
            # model could not see the real one.
            verdict = "invented"
        elif not got:
            verdict = "missed"      # should have routed, didn't
        elif not want:
            verdict = "overrouted"  # shouldn't have routed, did
        else:
            verdict = "wrong"

        missing = [a for a in required.get(got, []) if a not in got_args] if got in known else []
        rows.append({"text": text, "want": "|".join(acceptable), "got": got,
                     "verdict": verdict, "missing_args": missing})

        if verbose:
            mark = {"ok": "  ok  ", "wrong": " WRONG", "invented": " INVNT",
                    "missed": " MISS ", "overrouted": " OVER "}[verdict]
            note = f"  !! missing required {missing}" if missing else ""
            print(f"{mark}  {text[:46]:48} -> {got or '(none)':26} "
                  f"want {'|'.join(acceptable) or '(none)'}{note}")

    return rows, time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default="", help="Substring filter on the expected skill.")
    parser.add_argument("--model", default="", help="Override brain.model for this run.")
    parser.add_argument("--compare", default="",
                        help="Comma-separated models to score side by side, e.g. llama3,qwen2.5")
    parser.add_argument("--json", default="", help="Write full results to this path.")
    parser.add_argument("--native", action="store_true",
                        help="Route via the model's own tool-calling instead of the JSON prompt.")
    parser.add_argument("--holdout", action="store_true",
                        help="Run the held-out phrasings the prompt was not tuned on.")
    args = parser.parse_args()

    load_skills()
    known = {entry["name"] for entry in catalog()}
    required = {
        entry["name"]: [n for n, s in (entry["parameters"] or {}).items() if s.get("required")]
        for entry in catalog()
    }
    prompt = prompts.router_prompt(catalog())

    source = HELDOUT if args.holdout else CASES
    cases = [c for c in source if args.only in str(c[1])] if args.only else source
    label = "held-out" if args.holdout else "tuned"
    print(f"{len(cases)} {label} cases, {len(known)} skills, "
          f"prompt ~{llm.estimate_tokens([{'content': prompt}])} tokens\n")

    # -- comparing models -------------------------------------------------
    if args.compare:
        models = [m.strip() for m in args.compare.split(",") if m.strip()]
        results = {}
        for model in models:
            print(f"--- {model} ---")
            rows, elapsed = run(prompt, cases, model, known, required,
                                verbose=False, native=args.native)
            ok = sum(1 for r in rows if r["verdict"] == "ok")
            results[model] = rows
            print(f"    {ok}/{len(rows)}  ({ok / len(rows):.0%})  {elapsed:.0f}s")

        print(f"\n{'-' * 78}\nDisagreements:\n")
        first, *rest = models
        for i, (text, _) in enumerate(cases):
            answers = {m: results[m][i] for m in models}
            if len({a["got"] for a in answers.values()}) == 1:
                continue
            print(f"  {text}")
            print(f"      want {results[first][i]['want'] or '(none)'}")
            for model in models:
                row = answers[model]
                print(f"      {'OK ' if row['verdict'] == 'ok' else '   '} "
                      f"{model:12} {row['got'] or '(none)'}")

        if args.json:
            Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
            print(f"\nfull results -> {args.json}")
        return 0

    # -- a single model ---------------------------------------------------
    rows, elapsed = run(prompt, cases, args.model, known, required, native=args.native)
    counts = Counter(r["verdict"] for r in rows)
    covered = {r["got"] for r in rows if r["verdict"] == "ok" and r["got"]}
    unreachable = sorted(known - covered)

    print(f"\n{'-' * 78}")
    print(f"{counts['ok']}/{len(rows)} correct in {elapsed:.0f}s "
          f"({counts['wrong']} wrong, {counts['invented']} invented, "
          f"{counts['missed']} missed, {counts['overrouted']} over-routed)")

    if any(r["missing_args"] for r in rows):
        print("\nCalls missing a required argument (these fail at the gate):")
        for r in rows:
            if r["missing_args"]:
                print(f"  {r['got']:26} missing {r['missing_args']}  <- {r['text']!r}")

    # Only meaningful for the full sweep; the held-out set covers a subset.
    if unreachable and not args.holdout:
        print(f"\nNever reached by any phrasing ({len(unreachable)}):")
        for name in unreachable:
            print(f"  {name}")

    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nfull results -> {args.json}")

    return 1 if counts["ok"] < len(rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())

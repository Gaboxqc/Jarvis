"""Turn loop tests — REQ-1, REQ-24, REQ-25, REQ-27.

The model is stubbed throughout. These test the orchestration contract, not the
quality of llama3's routing: what happens to a parked action when the user says
"yes", what happens when the model is down, and what happens when the model
proposes something that does not exist.
"""

from __future__ import annotations

import json

import pytest
import yaml

from app.actions import journal
from app.brain import llm, orchestrator
from app.settings import reset_config_cache

from .conftest import make_files


def stub_llm(monkeypatch, *replies):
    """Queue LLM replies in call order; the last one repeats if exhausted."""
    queue = list(replies)
    calls = []

    def fake_chat(messages, *, json_mode=False, temperature=None, settings=None):
        calls.append({"json_mode": json_mode, "messages": messages})
        reply = queue.pop(0) if queue else replies[-1]
        return llm.LLMReply(text=reply, raw={})

    monkeypatch.setattr(llm, "chat", fake_chat)
    return calls


def route(skills):
    return json.dumps({"skills": skills, "reply": None})


NO_SKILLS = route([])


def speak_spanish(config_file):
    """Switch the persona to Spanish, as the Settings screen does.

    `persona.language: es` adds "Responde siempre en español" to the system
    prompt, so every reply the model writes from here on is in Spanish -- which
    is the whole reason the guard has to be able to read it.
    """
    raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    raw["persona"]["language"] = "es"
    config_file.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    reset_config_cache()


def test_a_plain_question_is_answered_without_any_action(workspace, monkeypatch):
    stub_llm(monkeypatch, NO_SKILLS, "Madrid is the capital of Spain.")

    result = orchestrator.handle_turn("what is the capital of spain", "t")

    assert "Madrid" in result.reply
    assert not result.needs_confirmation
    assert journal.history() == []


def test_a_routine_skill_runs_and_is_reported(workspace, monkeypatch):
    stub_llm(
        monkeypatch,
        route([{"name": "utils.calculate", "args": {"expression": "17*23"}}]),
        "That comes to 391.",
    )

    result = orchestrator.handle_turn("what's 17 times 23", "t")

    assert "391" in result.reply
    assert result.skill_calls[0]["status"] == "executed"


def test_a_consequential_skill_asks_before_doing_anything(workspace, monkeypatch):
    make_files(workspace, ["a.pdf", "b.jpg"])
    stub_llm(monkeypatch, route([
        {"name": "system.organize_folder", "args": {"folder": str(workspace)}}
    ]))

    result = orchestrator.handle_turn("tidy up that folder", "t")

    assert result.needs_confirmation
    assert "2 files" in result.reply
    assert "can be undone" in result.reply
    assert (workspace / "a.pdf").exists()  # nothing moved


def test_yes_only_works_when_the_client_returns_the_action_id(workspace, monkeypatch):
    make_files(workspace, ["a.pdf"])
    stub_llm(monkeypatch, route([
        {"name": "system.organize_folder", "args": {"folder": str(workspace)}}
    ]))
    parked = orchestrator.handle_turn("tidy that up", "t")
    action_id = parked.pending.action_id

    # A bare "yes" with no id in hand is just conversation — it must not execute.
    stub_llm(monkeypatch, NO_SKILLS, "Sure, about what?")
    orchestrator.handle_turn("yes", "t")
    assert journal.get(action_id).status == journal.STATUS_PENDING
    assert (workspace / "a.pdf").exists()

    # With the id, it goes through.
    confirmed = orchestrator.handle_turn("yes", "t", pending_action_id=action_id)
    assert journal.get(action_id).status == journal.STATUS_EXECUTED
    assert (workspace / "Documents" / "a.pdf").exists()
    assert "undo" in confirmed.reply.lower()


def test_saying_no_cancels_and_changes_nothing(workspace, monkeypatch):
    make_files(workspace, ["a.pdf"])
    stub_llm(monkeypatch, route([
        {"name": "system.organize_folder", "args": {"folder": str(workspace)}}
    ]))
    parked = orchestrator.handle_turn("tidy that up", "t")

    result = orchestrator.handle_turn("no", "t", pending_action_id=parked.pending.action_id)

    assert "Cancelled" in result.reply
    assert (workspace / "a.pdf").exists()
    assert journal.get(parked.pending.action_id).status == journal.STATUS_DECLINED


def test_undo_is_handled_without_consulting_the_model(workspace, monkeypatch):
    make_files(workspace, ["a.pdf", "b.jpg"])
    stub_llm(monkeypatch, route([
        {"name": "system.organize_folder", "args": {"folder": str(workspace)}}
    ]))
    parked = orchestrator.handle_turn("tidy that up", "t")
    orchestrator.handle_turn("yes", "t", pending_action_id=parked.pending.action_id)
    assert not (workspace / "a.pdf").exists()

    # If the model were consulted here it would raise; undo must not reach it.
    def exploding_chat(*args, **kwargs):
        raise AssertionError("undo must not depend on the model")

    monkeypatch.setattr(llm, "chat", exploding_chat)

    result = orchestrator.handle_turn("undo that", "t")

    assert (workspace / "a.pdf").exists()
    assert "back" in result.reply.lower()


def test_a_dead_model_is_reported_plainly_and_does_not_crash(workspace, monkeypatch):
    def unavailable(*args, **kwargs):
        raise llm.LLMUnavailable("I can't reach the language model at http://localhost:11434.")

    monkeypatch.setattr(llm, "chat", unavailable)

    result = orchestrator.handle_turn("hello", "t")

    assert result.error == "llm_unavailable"
    assert "can't reach" in result.reply
    assert not result.needs_confirmation


def test_a_hallucinated_skill_name_degrades_to_conversation(workspace, monkeypatch):
    stub_llm(
        monkeypatch,
        route([{"name": "system.delete_everything", "args": {}}]),
        "I'm not able to do that.",
    )

    result = orchestrator.handle_turn("wipe my drive", "t")

    assert result.skill_calls == []
    assert journal.history() == []


def test_unparseable_router_output_still_produces_an_answer(workspace, monkeypatch):
    stub_llm(monkeypatch, "I think the user wants help!", "How can I help?")

    result = orchestrator.handle_turn("hi", "t")

    assert result.reply
    assert not result.needs_confirmation


def test_nothing_queued_behind_a_parked_action_runs(workspace, monkeypatch):
    """A 'no' must not leave half the work already done."""
    make_files(workspace, ["a.pdf"])
    stub_llm(monkeypatch, route([
        {"name": "system.organize_folder", "args": {"folder": str(workspace)}},
        {"name": "planning.add_task", "args": {"text": "should not be created"}},
    ]))

    result = orchestrator.handle_turn("tidy up and add a task", "t")

    assert result.needs_confirmation
    executed = [r for r in journal.history() if r.status == journal.STATUS_EXECUTED]
    assert executed == []


def test_the_reply_is_written_back_into_conversation_memory(workspace, monkeypatch):
    from app.memory import short_term

    stub_llm(monkeypatch, NO_SKILLS, "Noted.")
    orchestrator.handle_turn("remember I like tea", "t")

    window = short_term.window("t")
    assert [t.role for t in window] == ["user", "assistant"]


def test_results_survive_a_model_failure_during_synthesis(workspace, monkeypatch):
    """The work already happened; it must be reported, not lost."""
    calls = {"n": 0}

    def flaky(messages, *, json_mode=False, temperature=None, settings=None):
        calls["n"] += 1
        if json_mode:
            return llm.LLMReply(
                text=route([{"name": "utils.calculate", "args": {"expression": "6*7"}}]), raw={}
            )
        raise llm.LLMUnavailable("model died mid-turn")

    monkeypatch.setattr(llm, "chat", flaky)

    result = orchestrator.handle_turn("what is 6 times 7", "t")

    assert "42" in result.reply


def test_an_ungated_write_is_always_disclosed(workspace, monkeypatch):
    """REQ-7 — memory writes run without asking, so the telling is mandatory.

    The synthesis pass reliably rewords "Noted and saved: X" into bare
    agreement. Since there was no confirmation prompt, that disclosure is the
    user's only signal, so it cannot depend on the model.
    """
    stub_llm(
        monkeypatch,
        route([{"name": "memory.remember", "args": {"text": "The user prefers short replies"}}]),
        "I'll keep my responses brief.",  # drops the fact that anything was stored
    )

    result = orchestrator.handle_turn("remember I prefer short replies", "t")

    assert "prefers short replies" in result.reply
    assert "brief" in result.reply  # the model's phrasing is kept too


def test_the_receipt_is_not_duplicated_when_already_stated(workspace, monkeypatch):
    stub_llm(
        monkeypatch,
        route([{"name": "memory.remember", "args": {"text": "The user prefers short replies"}}]),
        "Saved - the user prefers short replies.",
    )

    result = orchestrator.handle_turn("remember I prefer short replies", "t")

    assert result.reply.lower().count("prefers short replies") == 1


# -- the ungrounded-reply guard (REQ-27) ----------------------------------


def test_an_invented_answer_about_the_calendar_is_blocked(workspace, monkeypatch):
    """Found live: with no calendar connected and no tool called, llama3
    answered "you have a meeting with John at 10am" — entirely invented."""
    stub_llm(monkeypatch, NO_SKILLS,
             "You have a meeting with John at 10:00 and another with Sarah at 14:00.")

    result = orchestrator.handle_turn("what's on my calendar today?", "t")

    assert "John" not in result.reply
    assert "look that up" in result.reply


def test_claiming_to_have_done_something_undone_is_blocked(workspace, monkeypatch):
    """Also found live: it reported adding a calendar event, having added nothing."""
    stub_llm(monkeypatch, NO_SKILLS, "I've added \"Lunch with Ana\" to your calendar.")

    result = orchestrator.handle_turn("add lunch with Ana tomorrow at 1pm", "t")

    assert "didn't actually do that" in result.reply
    assert journal.history() == []


def test_a_grounded_answer_is_never_rewritten(workspace, monkeypatch):
    """The guard keys off there being no successful skill call, so a real
    calendar answer passes through untouched."""
    stub_llm(
        monkeypatch,
        route([{"name": "planning.add_task", "args": {"text": "buy milk"}}]),
        "I've added buy milk to your list.",
    )

    result = orchestrator.handle_turn("add buy milk to my tasks", "t")

    assert "I've added" in result.reply


def test_ordinary_conversation_is_not_touched(workspace, monkeypatch):
    stub_llm(monkeypatch, NO_SKILLS, "Madrid is the capital of Spain.")

    result = orchestrator.handle_turn("what is the capital of spain", "t")

    assert result.reply == "Madrid is the capital of Spain."


def test_answering_from_stored_memory_is_not_blocked(workspace, monkeypatch):
    """Memory facts live in the system prompt, so answering from them without
    a tool call is legitimate and must not trip the guard."""
    stub_llm(monkeypatch, NO_SKILLS, "You prefer short replies.")

    result = orchestrator.handle_turn("what do you remember about me?", "t")

    assert result.reply == "You prefer short replies."


# -- the same guard, in Spanish (REQ-27, REQ-28) --------------------------
#
# These are the assertions that were missing. The guard's patterns were
# English-only for as long as it existed, while the app shipped a Spanish
# persona and told the model to answer in Spanish -- so for half the supported
# languages the check could not fire at all, and the failure it exists to
# prevent went through untouched. Every English case above has a counterpart
# here from now on.


def test_an_invented_answer_about_the_calendar_is_blocked_in_spanish(
    workspace, config_file, monkeypatch
):
    speak_spanish(config_file)
    stub_llm(monkeypatch, NO_SKILLS,
             "Tienes una reunión con Juan a las 10:00 y otra con Sara a las 14:00.")

    result = orchestrator.handle_turn("¿qué tengo en el calendario hoy?", "t")

    assert "Juan" not in result.reply
    assert "prefiero no adivinar" in result.reply


def test_claiming_to_have_done_something_undone_is_blocked_in_spanish(
    workspace, config_file, monkeypatch
):
    speak_spanish(config_file)
    stub_llm(monkeypatch, NO_SKILLS, 'He añadido "Comida con Ana" a tu calendario.')

    result = orchestrator.handle_turn("añade comida con Ana mañana a la una", "t")

    assert "no lo he hecho" in result.reply
    assert journal.history() == []


def test_the_spanish_claim_is_caught_without_its_accents(
    workspace, config_file, monkeypatch
):
    """Small models drop accents. A guard that only catches correctly-spelled
    lies is not a guard."""
    speak_spanish(config_file)
    stub_llm(monkeypatch, NO_SKILLS, "Ya he anadido la cita y te he enviado el correo.")

    result = orchestrator.handle_turn("apunta la cita", "t")

    assert "no lo he hecho" in result.reply


def test_the_spanish_preterite_is_caught_too(workspace, config_file, monkeypatch):
    """"Guardé" rather than "he guardado" -- the ordinary past tense across
    most of the Spanish-speaking world."""
    speak_spanish(config_file)
    stub_llm(monkeypatch, NO_SKILLS, "Guardé la nota y cancelé el recordatorio.")

    result = orchestrator.handle_turn("guarda esa nota", "t")

    assert "no lo he hecho" in result.reply


def test_the_spanish_impersonal_claim_is_caught(workspace, config_file, monkeypatch):
    """The phrasing it reaches for when hedging, which is when it invents."""
    speak_spanish(config_file)
    stub_llm(monkeypatch, NO_SKILLS, "El mensaje ya está enviado.")

    result = orchestrator.handle_turn("manda el mensaje", "t")

    assert "no lo he hecho" in result.reply


def test_a_grounded_spanish_answer_is_never_rewritten(
    workspace, config_file, monkeypatch
):
    """The guard keys off there being no successful skill call, in either
    language. A real one passes through untouched, claim or no claim."""
    speak_spanish(config_file)
    stub_llm(
        monkeypatch,
        route([{"name": "planning.add_task", "args": {"text": "comprar leche"}}]),
        "He añadido comprar leche a tu lista.",
    )

    result = orchestrator.handle_turn("añade comprar leche a mis tareas", "t")

    assert "He añadido" in result.reply


def test_ordinary_spanish_conversation_is_not_touched(
    workspace, config_file, monkeypatch
):
    speak_spanish(config_file)
    stub_llm(monkeypatch, NO_SKILLS, "Madrid es la capital de España.")

    result = orchestrator.handle_turn("¿cuál es la capital de España?", "t")

    assert result.reply == "Madrid es la capital de España."


@pytest.mark.parametrize(
    "offer",
    [
        "¿Quieres que guarde esto en tus notas?",
        "¿Quieres que programe la reunión?",
        "Dime si quieres que cancele el recordatorio.",
        "Puedo hacerlo si quieres que lo elimine.",
    ],
)
def test_a_spanish_offer_is_not_an_action_claim(
    workspace, config_file, monkeypatch, offer
):
    """The accent on the preterite is load-bearing, and this is why.

    For every -ar verb in the pattern, the first-person preterite and the
    present subjunctive differ by nothing but the accent: `guardé` is "I saved
    it", `guarde` is "that I save it". The subjunctive is how the assistant
    *offers* to do something -- "¿Quieres que guarde esto?" is the most useful
    sentence it can say -- so with the accent optional, every offer came back
    as "I didn't actually do that".

    Found by writing the pattern the obvious way and then testing what it
    refused, which is the only way this kind of mistake shows up.
    """
    speak_spanish(config_file)
    stub_llm(monkeypatch, NO_SKILLS, offer)

    result = orchestrator.handle_turn("guarda esto", "t")

    assert result.reply == offer


@pytest.mark.parametrize(
    "denial",
    [
        "No he enviado nada todavía.",
        "No guardé nada porque no tengo acceso.",
        "Nunca he borrado un archivo tuyo.",
        "Tampoco he mirado eso.",
    ],
)
def test_a_spanish_denial_is_not_an_action_claim(
    workspace, config_file, monkeypatch, denial
):
    """English did not need this and Spanish does.

    "I haven't added it" puts a word the pattern does not know between "I" and
    "added"; "no he enviado nada" leaves "he enviado" sitting there intact. An
    honest refusal is the one reply that must not be replaced by a refusal --
    the user would be told the assistant was unsure about the thing it had
    just correctly said it could not do.

    The prompt names no data source on purpose. "manda el correo" would trip
    the *other* half of the guard -- a question about mail, answered with no
    tool run -- and replace the denial for a different and correct reason,
    which would make this assertion pass for the wrong one.
    """
    speak_spanish(config_file)
    stub_llm(monkeypatch, NO_SKILLS, denial)

    result = orchestrator.handle_turn("hazlo ya", "t")

    assert result.reply == denial


def test_cree_is_a_verb_before_it_is_a_claim(workspace, config_file, monkeypatch):
    """The sharpest case of the accent rule: unaccented `cree` is the ordinary
    Spanish word for "believes", so matching it would refuse a reply for
    containing a common verb."""
    speak_spanish(config_file)
    stub_llm(monkeypatch, NO_SKILLS, "Mucha gente cree que sí, pero depende.")

    result = orchestrator.handle_turn("¿es verdad?", "t")

    assert result.reply == "Mucha gente cree que sí, pero depende."


def test_a_spanish_observation_is_not_an_action_claim(
    workspace, config_file, monkeypatch
):
    """The English pattern is careful that "I've noticed" and "I've got" do not
    match. The Spanish one has to be just as careful, or every reply beginning
    "he visto" becomes a refusal."""
    speak_spanish(config_file)
    stub_llm(monkeypatch, NO_SKILLS, "He visto que preguntas mucho por eso.")

    result = orchestrator.handle_turn("¿por qué?", "t")

    assert result.reply == "He visto que preguntas mucho por eso."

"""The intent tables — REQ-27, REQ-28.

These lived at the top of orchestrator.py: six regexes and sets encoding what a
sentence *means* in a particular language, inside the module that decides what
to *do*. The cost of that coupling was paid before anyone noticed it existed --
two of the six were never translated, so a Spanish persona had no
invented-answer guard at all, and there was nowhere for a test to say that was
wrong.

This is that nowhere, filled in. Most of it is about the one structural
property worth protecting: there are two different selection rules, and mixing
them up breaks the app in opposite directions.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.brain import intents


# -- every locale is whole ------------------------------------------------


@pytest.mark.parametrize("language", sorted(intents.LOCALES))
def test_a_locale_fills_in_every_field(language):
    """The assertion that would have caught the original gap.

    A locale with a hole in it is worse than no locale: the turn loop carries
    on, the missing check never fires, and nothing says so.
    """
    locale = intents.for_language(language)

    for field in dataclasses.fields(locale):
        value = getattr(locale, field.name)
        assert value, f"{language} has no {field.name}"
        if isinstance(value, str):
            assert value.strip() == value, f"{language}.{field.name} has loose whitespace"


def test_an_unknown_language_falls_back_rather_than_going_unguarded():
    """A persona set to something nothing here knows about should still get the
    checks that do exist. Being told the truth in English beats being told a
    lie in the right language."""
    assert intents.for_language("fr") is intents.LOCALES["en"]
    assert intents.for_language("") is intents.LOCALES["en"]
    assert intents.for_language("EN") is intents.LOCALES["en"]


def test_the_refusals_differ_between_locales():
    """A guard that fires in Spanish and answers in English is a different bug
    in the same place, and a locale that forgot to translate its messages
    would look exactly like one that had."""
    english, spanish = intents.LOCALES["en"], intents.LOCALES["es"]

    assert english.unperformed != spanish.unperformed
    assert english.ungrounded != spanish.ungrounded
    assert english.busy != spanish.busy


# -- what the user typed is matched in every language --------------------


@pytest.mark.parametrize("yes", ["yes", "go ahead", "sí", "vale", "dale"])
def test_agreement_is_recognised_whatever_the_persona_speaks(yes):
    """A persona setting is about how the assistant *speaks*. It is not a
    promise about what the person will type, and somebody running an English
    persona may well answer "sí".

    This is why the user-facing sets are a union rather than per-language, and
    it is the thing a naive split by language would have broken -- silently,
    since a "sí" that stops being recognised does not fail, it just routes as
    ordinary conversation and the confirmation stays unanswered.
    """
    assert intents.affirms(yes)


@pytest.mark.parametrize("no", ["no", "cancel", "nope", "cancela", "déjalo"])
def test_refusal_is_recognised_whatever_the_persona_speaks(no):
    assert intents.declines(no)


@pytest.mark.parametrize("text", ["undo", "revert that", "deshaz", "anula eso"])
def test_undo_is_recognised_whatever_the_persona_speaks(text):
    assert intents.is_undo(text)


@pytest.mark.parametrize("text", ["thanks", "good morning", "gracias", "buenas"])
def test_a_pleasantry_is_recognised_whatever_the_persona_speaks(text):
    assert intents.is_pleasantry(text)


def test_nothing_is_agreement_and_refusal_at_once():
    """Across the union, since that is what the turn loop reads. An overlap
    would make the answer depend on which check runs first, which is exactly
    the kind of thing that is decided by accident."""
    def across(attribute):
        return {
            phrase
            for locale in intents.LOCALES.values()
            for phrase in getattr(locale, attribute)
        }

    both = across("affirmative") & across("negative")
    assert not both, f"read as both yes and no: {sorted(both)}"


def test_a_real_request_is_not_mistaken_for_a_pleasantry():
    """The set is deliberately narrow: skipping the router on something that
    did need a tool produces a confident answer with nothing behind it."""
    for request in (
        "hello, what is on my calendar",
        "thanks, now delete that file",
        "hola, qué tengo hoy",
        "gracias, manda el correo",
    ):
        assert not intents.is_pleasantry(request)


# -- what the model wrote is matched in the persona's language -----------


def test_the_guard_patterns_are_per_language():
    """The other selection rule. The guard reads the assistant's own output,
    which follows the system prompt's instruction about which language to
    answer in -- so there is exactly one locale it could be in, and matching
    every language at once would import another language's false positives."""
    english, spanish = intents.LOCALES["en"], intents.LOCALES["es"]

    assert english.action_claim.search("I've added it to your calendar")
    assert not english.action_claim.search("He añadido eso a tu calendario")

    assert spanish.action_claim.search("He añadido eso a tu calendario")
    assert spanish.external_data.search("¿qué tengo en el calendario?")
    assert english.external_data.search("what is on my calendar")

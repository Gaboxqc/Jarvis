"""What a user means, and what the model claims — REQ-27, REQ-28.

These tables used to sit at the top of orchestrator.py: six regexes and sets
encoding what a sentence *means* in a particular language, inside the module
that decides what to *do*. Adding a third language meant editing six literals
in the middle of the turn loop, and the cost of that coupling was already paid
once -- `_EXTERNAL_DATA` and `_ACTION_CLAIM` were never translated, so a
Spanish persona had no invented-answer guard at all and nothing pointed at the
omission.

Two selection rules, and the difference is the reason this module exists
rather than being a straight split by language.

**What the user typed is matched in every language at once.** Someone running
an English persona may well answer a confirmation with "sí", or type "deshaz",
and the persona setting is about how the assistant *speaks* -- it is not a
promise about what the person will type. So `affirms`, `declines`, `is_undo`
and `is_pleasantry` read the union of every locale. That is what the old merged
sets did; it was correct, and splitting them per language would have quietly
broken it.

**What the model wrote is matched in the persona's language.** The guard
patterns look at the assistant's own output, which follows the system prompt's
instruction about which language to answer in -- so there is exactly one locale
they could be in. The refusals live beside the patterns for the same reason: a
guard that fires in Spanish and answers in English is a different bug in the
same place.

Adding a language means adding a module here and listing it in `LOCALES`. The
test in tests/test_intents.py asserts every listed locale fills in every field,
which is the assertion that would have caught the original gap -- and that a
locale is its own set of false positives, not a translation of someone else's,
is what writing the Spanish one taught us: the first draft refused every offer
the assistant makes ("¿Quieres que guarde esto?") and every honest denial ("no
he enviado nada").
"""

from __future__ import annotations

from . import en, es
from .locale import Locale

#: Every language with a locale behind it. The persona may be set to something
#: else; `for_language` says what happens then.
LOCALES: dict[str, Locale] = {
    "en": en.LOCALE,
    "es": es.LOCALE,
}

DEFAULT = "en"


def for_language(language: str) -> Locale:
    """The locale for a persona language, falling back to English.

    English rather than "no locale": a persona set to a language nothing here
    knows about should still be protected by the checks that do exist, even
    though its refusals will be in the wrong language. Being told the truth in
    English beats being told a lie in the right one.
    """
    return LOCALES.get((language or DEFAULT).lower(), LOCALES[DEFAULT])


# -- what the user typed, in any language --------------------------------


def _union(attribute: str) -> frozenset[str]:
    merged: set[str] = set()
    for locale in LOCALES.values():
        merged |= getattr(locale, attribute)
    return frozenset(merged)


_AFFIRMATIVE = _union("affirmative")
_NEGATIVE = _union("negative")
_PLEASANTRIES = _union("pleasantries")


def affirms(normalized: str) -> bool:
    """Whether this is a bare yes.

    Never authorises anything on its own -- the caller still has to hand back
    the action id it was given, and the gate re-checks that id's state. This
    only decides whether the turn is answering a confirmation at all.
    """
    return normalized in _AFFIRMATIVE


def declines(normalized: str) -> bool:
    return normalized in _NEGATIVE


def is_pleasantry(normalized: str) -> bool:
    """A turn that cannot possibly need a tool, so it skips the routing call.

    Whole phrases rather than a pattern, because the cost of being wrong is
    asymmetric: skipping the router on something that did need a tool produces
    a confident answer with nothing behind it, which is the exact failure the
    guard exists to catch. Skipping it on "thanks" saves half the turn.
    """
    return normalized in _PLEASANTRIES


def is_undo(text: str) -> bool:
    """Whether the user is asking to take the last thing back.

    Checked against every locale's pattern, not the persona's: "undo" typed at
    a Spanish persona means undo.
    """
    return any(locale.undo.match(text) for locale in LOCALES.values())

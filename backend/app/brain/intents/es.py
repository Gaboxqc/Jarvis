"""Spanish.

Written second, and it is the reason `Locale` requires every field. The guard
patterns here did not exist for as long as the guard did: a persona set to
Spanish -- a language this app ships, and whose system prompt tells the model
to answer in it -- had no invented-answer check at all, because both patterns
were English and nothing pointed at the omission.

What writing them taught us, and what the next locale should expect: a locale
is not a translation of someone else's patterns, it is its own set of false
positives. The first draft of the two below refused every offer the assistant
makes and every honest denial. Both causes are explained where they are
handled.
"""

from __future__ import annotations

import re

from .locale import Locale

AFFIRMATIVE = frozenset({"sí", "si", "vale", "dale", "hazlo", "adelante", "claro"})

NEGATIVE = frozenset({
    "no", "cancela", "cancelar", "para", "déjalo", "dejalo", "olvídalo",
    "olvidalo", "no gracias", "mejor no", "nada",
})

PLEASANTRIES = frozenset({
    "hola", "buenos días", "buenos dias", "buenas tardes", "buenas noches",
    "gracias", "muchas gracias", "vale", "genial", "perfecto", "entendido",
    "adiós", "adios", "hasta luego", "buenas",
})

UNDO = re.compile(r"^\s*(deshaz|deshacer|revierte|anula)\b", re.IGNORECASE)

# Articles as well as possessives, matching the English pattern's `the`:
# "¿qué tengo en el calendario?" is the ordinary way to ask, and requiring
# "mi" would miss most of the question shapes people use.
#
# A determiner is still required, which leaves "¿qué correos tengo?"
# unmatched. That is the English pattern's limitation too ("what emails do I
# have" does not match either) and it is kept deliberately rather than fixed
# here: matching a bare noun would refuse "¿qué es un calendario?", and a
# guard that blocks honest general answers gets switched off.
EXTERNAL_DATA = re.compile(
    r"\b(mis?|el|la|los|las)\s+("
    r"calendario|agenda|horario|reuni[oó]n(?:es)?|citas?|"
    r"bandeja(?:\s+de\s+entrada)?|buz[oó]n|correos?|e-?mails?|mensajes?|"
    r"documentos?|archivos?|ficheros?|carpetas?|"
    r"tareas?|pendientes|recordatorios?"
    r")\b",
    re.IGNORECASE,
)

# Three forms rather than English's two. The perfect ("he añadido") is what the
# model writes most often; the preterite ("guardé") is how half the
# Spanish-speaking world says it; and the impersonal ("se ha enviado", "ya está
# guardado") is the phrasing it falls back on when hedging, which is exactly
# when it is inventing.
#
# The accent on the preterite is load-bearing. For every -ar verb here the
# first-person preterite and the present subjunctive differ by nothing else:
# `guardé` is "I saved it" and `guarde` is "that I save it" -- and the
# subjunctive is how the assistant *offers* to do something.
# "¿Quieres que guarde esto?" is the most useful sentence it can say, and with
# the accent optional every single offer was answered with "I didn't actually
# do that". So the preterite is matched accented only, and the cost is a
# misspelled claim slipping through. `creé` is the sharpest case: unaccented
# `cree` is the ordinary verb "believes". The perfect form carries no such
# ambiguity and stays accent-free, which is where most of the coverage is.
#
# Negation has to be excluded explicitly, which English did not need: "I
# haven't added" puts a word the pattern does not know between "I" and
# "added", while "no he enviado" leaves "he enviado" sitting there intact. An
# honest refusal is the one reply that must not be replaced by a refusal.
# Three fixed-width lookbehinds cover the forms that appear in practice; "no te
# he enviado", with a pronoun in between, still slips, and the cost of that is
# one plain denial swapped for another.
ACTION_CLAIM = re.compile(
    r"\b(?<!no )(?<!nunca )(?<!tampoco )("
    r"(?:ya\s+)?he\s+"
    r"(a[ñn]adido|agregado|creado|programado|reservado|configurado|puesto|apuntado|"
    r"enviado|mandado|borrado|eliminado|quitado|cancelado|movido|guardado|"
    r"almacenado|actualizado|marcado|organizado)"
    r"|(a[ñn]ad[ií]|agregué|creé|programé|reservé|configuré|puse|apunté|envié|"
    r"mandé|borré|eliminé|quité|cancelé|moví|guardé|almacené|actualicé|marqué|"
    r"organicé)"
    r"|(?:se\s+ha|ha\s+sido|ya\s+est[áa]|queda)\s+"
    r"(a[ñn]adid[oa]|agregad[oa]|cread[oa]|programad[oa]|reservad[oa]|configurad[oa]|"
    r"enviad[oa]|mandad[oa]|borrad[oa]|eliminad[oa]|cancelad[oa]|movid[oa]|"
    r"guardad[oa]|actualizad[oa]|marcad[oa]|organizad[oa])"
    r")\b",
    re.IGNORECASE,
)

LOCALE = Locale(
    affirmative=AFFIRMATIVE,
    negative=NEGATIVE,
    pleasantries=PLEASANTRIES,
    undo=UNDO,
    external_data=EXTERNAL_DATA,
    action_claim=ACTION_CLAIM,
    unperformed=(
        "En realidad no lo he hecho: no he sabido qué acción ejecutar. "
        "¿Puedes decírmelo de otra forma?"
    ),
    ungrounded=(
        "Tendría que consultarlo y no he podido, así que prefiero no adivinar. "
        "Comprueba que la cuenta o la carpeta esté conectada y vuelve a preguntarme."
    ),
    busy="Todavía estoy con lo anterior. Dame un momento.",
)

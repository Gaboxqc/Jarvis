"""English."""

from __future__ import annotations

import re

from .locale import Locale

AFFIRMATIVE = frozenset({
    "y", "yes", "yeah", "yep", "yup", "ok", "okay", "sure", "do it", "go ahead",
    "go for it", "confirm", "confirmed", "please do", "proceed", "affirmative",
})

NEGATIVE = frozenset({
    "n", "no", "nope", "cancel", "stop", "don't", "dont", "nevermind",
    "never mind", "forget it", "no thanks", "nah",
})

# Only phrases that are complete in themselves and carry no request --
# anything longer still routes. See `intents.is_pleasantry` for why the set is
# deliberately narrow.
PLEASANTRIES = frozenset({
    "hi", "hello", "hey", "yo", "good morning", "good afternoon", "good evening",
    "thanks", "thank you", "thanks a lot", "thank you very much", "ta", "cheers",
    "ok", "okay", "cool", "nice", "great", "perfect", "got it", "understood",
    "never mind", "nevermind", "no worries", "sorry",
    "bye", "goodbye", "good night", "see you", "later",
})

UNDO = re.compile(
    r"^\s*(undo|revert|roll ?back|take that back|put (them|it|those|that) back)\b",
    re.IGNORECASE,
)

# Sources the model cannot possibly know about without calling a tool. Memory
# facts are deliberately absent: those are injected into the system prompt, so
# answering from them without a tool call is legitimate.
EXTERNAL_DATA = re.compile(
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
#
# Negation needs no special handling here, which is a property of English
# rather than a decision: "I haven't added" puts a word this pattern does not
# know between "I" and "added". Spanish leaves "he enviado" intact inside "no
# he enviado" and has to exclude it explicitly -- see es.py.
ACTION_CLAIM = re.compile(
    r"\b(i(?:'ve| have)?\s+(?:just\s+)?"
    r"(added|created|scheduled|booked|set(?: up)?|sent|deleted|removed|cancelled|"
    r"canceled|moved|saved|stored|updated|marked|organi[sz]ed|reminded)"
    r"|(?:has|have) been (added|created|scheduled|sent|deleted|removed|cancelled|"
    r"canceled|moved|saved|updated|marked|set))\b",
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
        "I didn't actually do that — I couldn't work out which action to run. "
        "Could you say it again more plainly?"
    ),
    ungrounded=(
        "I'd have to look that up and I wasn't able to — so I don't want to guess. "
        "Check that the account or folder is connected, then ask me again."
    ),
    busy="I'm still working on the last thing you asked. Give me a moment.",
)

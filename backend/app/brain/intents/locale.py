"""One language's worth of intent recognition.

Every field is required. A locale with a hole in it is worse than no locale:
the turn loop would carry on, the missing check would never fire, and nothing
would say so -- which is precisely how `_EXTERNAL_DATA` and `_ACTION_CLAIM`
stayed English-only while the app shipped a Spanish persona.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Locale:
    #: Bare agreement to a confirmation the user was just shown.
    affirmative: frozenset[str]
    #: Bare refusal of one.
    negative: frozenset[str]
    #: Complete phrases that carry no request, so the router can be skipped.
    pleasantries: frozenset[str]
    #: "Take that back." Matched from the start of the message.
    undo: re.Pattern[str]

    #: Data sources the model cannot know about without calling a tool. Memory
    #: facts are deliberately absent: those are injected into the system
    #: prompt, so answering from them without a tool call is legitimate.
    external_data: re.Pattern[str]
    #: First-person claims that something was carried out.
    action_claim: re.Pattern[str]

    #: Said when the model claimed an action that never ran.
    unperformed: str
    #: Said when it answered about a source it never looked at.
    ungrounded: str
    #: Said when a turn for this session is already running.
    busy: str

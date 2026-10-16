"""Where a turn's milliseconds went — REQ-27.

There was no instrumentation in the turn loop at all. Not a thin layer, not a
disabled one: no `perf_counter` and no `monotonic` anywhere in `brain/` or the
API layer. Every performance number this project can quote was measured once,
by hand, and written into a comment -- index/search.py has a table ending at
666ms for twenty thousand chunks, db.py has one comparing 2.50s against 0.062s
per thousand commits -- and none of them say anything about the app that is
running now. "It feels slow today" had no answer, and the honest reply to it
was a shrug.

So each turn leaves three numbers behind: how long routing took, how long the
skills took, how long the synthesis pass took. Those are the three stages the
turn loop already names to the UI (`{"type": "stage"}`), which is the right
split because it is the one the user is already being shown.

Diagnostics, not telemetry
--------------------------

Nothing here is persisted and nothing leaves the machine. A ring of the last
`CAPACITY` turns in memory, gone when the process exits, served only to a
local caller that already holds the API token. That is the same posture
diagnostics.py takes with the log: it will say the file is 2.1MB and it will
not say what is in it.

A record holds six numbers and nothing else. No user text, no skill names, no
session id -- not because any of those would be dangerous in a process the
user owns, but because a diagnostics surface that grows a field every time
somebody has a question is how a counter turns into a profile. These numbers
answer "which stage"; the log answers "which call".

The three stages do not sum to the total, on purpose
----------------------------------------------------

`total_ms` is the whole turn; `routing_ms + skills_ms + synthesis_ms` is less
than it, and the difference is not rounding. Measured against a local qwen2.5
over five real turns, the first instrumented run this had:

    prompt                            route   skills   synth    total
    "thanks"                              0        0    7224    10716
    "what is the capital of spain"      3170        3     758     4563
    "what's 17 times 23"                1153        1     734     2242
    "remind me to call the dentist"     1502        6    1401     3322
    "what my documents say about X"     1077        5    1272     2784

Three things in that table were worth the whole exercise. The skills stage is
noise -- the Action Gate plus a real skill is single-digit milliseconds against
a model call of seconds, so "it feels slow" is never about the work being done.
Routing costs *more* than synthesis, often two or three times more, where the
comments in brain/orchestrator.py put a routing call at "half the latency of a
turn". And 350-600ms of every turn lands outside all three stages: 318ms of it
is `long_term.relevant()`, which embeds the user's text through Ollama before
routing begins, measured separately at a 318.5ms median on this machine, plus
140ms once for skill discovery.

The gap is left visible rather than papered over. A fourth stage would be the
honest fix and it is a change to this response's shape, so it is a follow-up
rather than something smuggled in here -- and in the meantime a reader who
subtracts and finds half a second missing is looking at a real cost, not at a
bug in the arithmetic.

`perf_counter`, not `time()`
----------------------------

Wall clock steps when NTP corrects it and when the machine wakes from sleep,
and a lid closed mid-turn is not a rare event for something meant to sit in the
background all day. A clock that can go backwards produces negative durations,
which read as a bug in whatever is displaying them. `at` is wall clock, because
a timestamp is for a human; every duration comes from `perf_counter`.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

# How many turns are kept.
#
# Fifty, which at ordinary use is somewhere between half an hour and an
# afternoon of conversation -- long enough that "it got slow after I indexed
# that folder" is still in the window, short enough that the whole thing is a
# few kilobytes and the panel rendering it needs no paging. Bounded by
# `deque(maxlen=...)` rather than by a trim on write, so there is no path where
# a forgotten prune lets it grow for the life of the process.
CAPACITY = 50

# Stage names, in the order a turn goes through them. Fixed here rather than
# taken from whatever a caller passes: the response model and the Settings
# panel both have to agree about the set, and a mistyped stage name would
# otherwise arrive as a number that is silently always zero.
ROUTING = "routing"
SKILLS = "skills"
SYNTHESIS = "synthesis"
STAGES = (ROUTING, SKILLS, SYNTHESIS)


@dataclass(frozen=True)
class TurnTiming:
    """One finished turn, in milliseconds."""

    at: float
    routing_ms: float
    skills_ms: float
    synthesis_ms: float
    total_ms: float
    #: Skills the gate actually ran. Zero on a fast path, and zero for a turn
    #: that parked an action for approval before running anything.
    skills: int
    #: Routing passes. Two means the router asked for a follow-up round (see
    #: MAX_ROUNDS in brain/orchestrator.py), which costs a whole model call --
    #: so this is the field that says whether that feature is being paid for.
    rounds: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "routing_ms": self.routing_ms,
            "skills_ms": self.skills_ms,
            "synthesis_ms": self.synthesis_ms,
            "total_ms": self.total_ms,
            "skills": self.skills,
            "rounds": self.rounds,
        }


_ring: deque[TurnTiming] = deque(maxlen=CAPACITY)
_lock = threading.Lock()


class Turn:
    """A stopwatch for one turn, with one accumulator per stage.

    Accumulating rather than timing each stage once, because a turn can route
    twice and run skills in both rounds. Three stage totals plus a wall for the
    whole turn survives that; three timestamps would not.

    Nothing is recorded until `finish()`, and `finish()` is idempotent -- the
    turn loop is a generator, and a client that disconnects mid-stream closes
    it, so the call site is a `finally` that can also be reached by an ordinary
    return.
    """

    def __init__(self) -> None:
        self._started = time.perf_counter()
        self._at = time.time()
        self._stages: dict[str, float] = {name: 0.0 for name in STAGES}
        self._skills = 0
        self._rounds = 0
        self._done = False

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Time a block into `name`'s total, whatever it raises.

        The `finally` is the point. Routing raises LLMUnavailable on a dead
        model and a skill can raise anything at all; a stage that recorded only
        on success would report a fast turn for exactly the case somebody is
        most likely to be complaining about.
        """
        began = time.perf_counter()
        try:
            yield
        finally:
            elapsed = (time.perf_counter() - began) * 1000.0
            self._stages[name] = self._stages.get(name, 0.0) + elapsed

    def routed(self) -> None:
        self._rounds += 1

    def ran_skill(self) -> None:
        self._skills += 1

    def finish(self) -> None:
        if self._done:
            return
        self._done = True
        total = (time.perf_counter() - self._started) * 1000.0
        record(
            TurnTiming(
                at=self._at,
                routing_ms=round(self._stages[ROUTING], 1),
                skills_ms=round(self._stages[SKILLS], 1),
                synthesis_ms=round(self._stages[SYNTHESIS], 1),
                total_ms=round(total, 1),
                skills=self._skills,
                rounds=self._rounds,
            )
        )


def record(timing: TurnTiming) -> None:
    with _lock:
        _ring.append(timing)


def recent() -> list[TurnTiming]:
    """The ring, newest first."""
    with _lock:
        return list(reversed(_ring))


def clear() -> None:
    with _lock:
        _ring.clear()


def _median(values: list[float]) -> float:
    """The middle value, or the mean of the two middle ones.

    A median rather than a mean, because turn latencies are not a distribution
    anybody should average: a cold model load, a reindex and a timed-out skill
    sit in the same series as a one-word greeting, and any one of them moves
    the mean of fifty by seconds.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 1)
    return round((ordered[middle - 1] + ordered[middle]) / 2.0, 1)


def summary() -> dict[str, Any]:
    """Everything the panel needs, so the UI does no arithmetic.

    The medians are computed here rather than in TypeScript for the same reason
    the log's `total_bytes` is: two implementations of one number is one too
    many, and the CLI should be able to ask the same question.
    """
    turns = recent()
    return {
        "capacity": CAPACITY,
        "count": len(turns),
        "turns": [turn.to_dict() for turn in turns],
        "median": {
            "routing_ms": _median([turn.routing_ms for turn in turns]),
            "skills_ms": _median([turn.skills_ms for turn in turns]),
            "synthesis_ms": _median([turn.synthesis_ms for turn in turns]),
            "total_ms": _median([turn.total_ms for turn in turns]),
        },
        # The worst turn in the window, which is the one somebody is thinking
        # of when they say it felt slow. Null on a fresh start, where there is
        # no worst turn rather than a zero one.
        "slowest": max(turns, key=lambda turn: turn.total_ms).to_dict() if turns else None,
    }

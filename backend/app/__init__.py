"""Kai's backend.

The version lives here because something had to own it. `/health` reported
"0.1.0" while the app shipped as 0.3.5, which meant that after an update there
was no way to tell which backend was actually running -- the first question
worth asking when an update misbehaves, and the one the app could not answer.

installer/publish.py refuses to publish when the version files disagree, and
this is one of them now.
"""

__version__ = "0.3.5"

"""Shared query-parameter types.

Here rather than in each router because two of them need the same bounds, and
a limit that means 200 on one endpoint and something else on another is not a
contract anyone can rely on.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query



# Bounds for the query parameters below.
#
# Every one of these used to be a bare `int` with a default, which is a
# different thing from a bound: `?limit=100000000` was a valid request that
# would read the whole table into a list of dicts and serialise it. The token
# keeps the open internet out, and the bound keeps an honest mistake -- a UI
# that computes a page size, a script with an off-by-a-lot -- from taking the
# assistant down on the machine it is supposed to be helping.
#
# They are also the first thing the generated TypeScript client will carry
# across, so a constraint stated here is a constraint the UI cannot violate
# without a type error.
Limit = Annotated[int, Query(ge=1, le=200)]
SearchQuery = Annotated[str, Query(min_length=1, max_length=500)]

"""The HTTP surface, one module per area.

`main.py` was 1,413 lines and seventy endpoints, which is more than anyone can
own. Nothing moved but the lines: every path, response shape, function name and
docstring is the one it was, because the OpenAPI snapshot in
tests/test_openapi_contract.py would say so otherwise -- the schema carries
each endpoint's docstring as its description and its function name as its
operationId.

No router takes a prefix or a tag. A prefix would move the paths and a tag
would add a key to every operation, and both would show up in that snapshot as
a change to the API, which this is not.
"""

from __future__ import annotations

from . import (
    actions,
    avatar,
    capture,
    connectors,
    conversation,
    documents,
    memory,
    privacy,
    scheduling,
    system,
    voice,
)

#: Included in this order. It has no effect on routing -- every path is
#: complete and unambiguous -- and reads as the order the areas are described
#: in the README.
ROUTERS = [
    conversation.router,
    actions.router,
    memory.router,
    scheduling.router,
    documents.router,
    connectors.router,
    voice.router,
    avatar.router,
    capture.router,
    system.router,
    privacy.router,
]

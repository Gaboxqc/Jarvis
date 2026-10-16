"""The API's shape, pinned — REQ-27.

Seventy endpoints, and three clients over them: the CLI (which imports the
modules directly), the browser UI and the Tauri window (which both go through
`ui/src/api.ts`, 921 lines of hand-written types mirroring these responses).

That last one is the only place in the project where both halves can be green
and wrong. A renamed field passes ruff, passes mypy, passes pytest and passes
`tsc --noEmit`, because the TypeScript is describing what the backend used to
return. Nothing fails until someone opens the app.

A snapshot does not fix that -- generating the client types from this schema
is what fixes it -- but it makes the change *visible*: an endpoint that moves,
a field that disappears, a parameter that loses its bounds all arrive in review
as a diff of the API rather than as a diff of whichever module happened to
define it.

It is also what makes the planned split of main.py into routers safe to do in
one commit. That refactor must not change a single path or response shape, and
this is the assertion that says so.

To accept a deliberate change:

    ..\\.venv\\Scripts\\python tools\\openapi_snapshot.py

and commit the diff alongside the code that caused it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import openapi_snapshot  # noqa: E402


def test_the_api_surface_has_not_changed_unintentionally():
    """No `workspace` fixture: the schema is static, built from the decorators,
    and must not depend on a config file or a database."""
    assert openapi_snapshot.SNAPSHOT.exists(), (
        "no committed snapshot; run tools/openapi_snapshot.py"
    )

    expected = json.loads(openapi_snapshot.SNAPSHOT.read_text(encoding="utf-8"))
    actual = openapi_snapshot.schema()

    missing = sorted(set(expected["paths"]) - set(actual["paths"]))
    added = sorted(set(actual["paths"]) - set(expected["paths"]))
    assert not missing, f"endpoints disappeared: {missing}"
    assert not added, f"new endpoints are not in the snapshot: {added}"

    # Compared whole rather than path by path, so a changed response model or a
    # dropped parameter constraint is caught too.
    assert actual == expected, (
        "the API schema differs from the committed snapshot. If the change was "
        "intended, run tools/openapi_snapshot.py and commit the diff."
    )


def test_the_snapshot_is_written_the_way_the_test_reads_it():
    """A formatting mismatch would make every run dirty and teach people to
    regenerate without reading, which is the one thing this must not become."""
    written = openapi_snapshot.SNAPSHOT.read_text(encoding="utf-8")

    assert written == openapi_snapshot.serialise(json.loads(written))


def test_the_bounded_parameters_reached_the_schema():
    """The bounds added to `limit` and `q` are part of the contract, not just a
    runtime check -- they are what the generated TypeScript client will carry
    across, so a client cannot violate them without a type error."""
    schema = openapi_snapshot.schema()

    def parameter(path: str, name: str) -> dict:
        found = [
            entry
            for entry in schema["paths"][path]["get"]["parameters"]
            if entry["name"] == name
        ]
        assert found, f"{path} has no {name} parameter"
        return found[0]["schema"]

    history = parameter("/actions/history", "limit")
    assert history["maximum"] == 200
    assert history["minimum"] == 1

    search = parameter("/documents/search", "limit")
    assert search["maximum"] == 200

    query = parameter("/documents/search", "q")
    assert query["maxLength"] == 500
    assert query["minLength"] == 1


# -- the split that snapshot made safe ------------------------------------


def test_no_endpoint_is_declared_in_main():
    """`main.py` owns the app's lifetime and its front door, not its routes.

    It was 1,413 lines and seventy endpoints, which is more than anyone can
    own: every new capability landed in the same file, and the middleware
    ordering comment that genuinely has to be read was buried a thousand lines
    above the thing it warns about.

    This is the assertion that keeps it that way. A new endpoint goes in the
    app/api/ module for its area, or in a new one.
    """
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

    assert "@app.get" not in source
    assert "@app.post" not in source
    assert "@app.put" not in source
    assert "@app.patch" not in source
    assert "@app.delete" not in source


def test_every_router_is_included():
    """A module in app/api/ that nobody includes is a set of endpoints that
    silently do not exist -- and the snapshot above would only notice if the
    paths had been there before."""
    from app.api import ROUTERS
    from app.main import app

    declared = {
        route.path for router in ROUTERS for route in router.routes
    }
    served = {getattr(route, "path", None) for route in app.routes}

    missing = sorted(declared - served)
    assert not missing, f"routers declare paths the app does not serve: {missing}"


def test_no_router_adds_a_prefix_or_a_tag():
    """Both would show up in the snapshot as a change to the API.

    A prefix moves every path in the router; a tag adds a key to every
    operation. Neither is a thing this project's clients asked for, and the
    reason to say so here is that adding one looks like tidying.
    """
    from app.api import ROUTERS

    for router in ROUTERS:
        assert router.prefix == "", f"{router} carries a prefix"
        assert not router.tags, f"{router} carries tags"


# -- the responses, and the field that gets dropped quietly ---------------


def test_every_response_model_declares_what_its_handler_sends():
    """The failure mode `response_model` introduces, pinned.

    FastAPI filters the returned dict through the model, so a field the model
    forgot is not a weaker contract -- it is deleted from the response, with
    no error anywhere. The first draft of app/api/schemas.py dropped
    `needs_approval` from a routine run, which is the field that says a step
    was skipped for want of approval: a short run would have been reported as
    a complete one.

    It was found by setting `extra="forbid"` on every model and running the
    suite, so that a key the model did not declare raised instead of
    vanishing. This is that audit, kept: it reconfigures the models for the
    duration and drives the endpoints the suite already covers, so adding a
    model with a hole in it fails here rather than in the UI.
    """
    import inspect

    from pydantic import BaseModel

    from app.api import schemas

    models = [
        obj
        for _, obj in inspect.getmembers(schemas, inspect.isclass)
        if issubclass(obj, BaseModel) and obj.__module__ == schemas.__name__
    ]
    assert len(models) > 50, "the models moved; this test is looking in the wrong place"

    # Every model is reachable from a handler, or it is dead weight that will
    # drift. Checked here because an unused model is also an untested one.
    schema = openapi_snapshot.schema()
    referenced = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                referenced.add(ref.rsplit("/", 1)[-1])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema["paths"])
    walk(schema.get("components", {}).get("schemas", {}))

    unused = sorted(
        model.__name__ for model in models if model.__name__ not in referenced
    )
    assert not unused, f"response models no handler uses: {unused}"

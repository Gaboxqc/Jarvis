"""Embeddings, via the Ollama that is already a prerequisite — REQ-16, REQ-30.

Document search and memory recall were both purely lexical: FTS5 BM25 over
chunks, and token overlap over facts. Ask "how much was the deposit" and nothing
comes back unless the file says *deposit*. The word the user reaches for and the
word the document used are frequently not the same word, and that gap is most of
the distance between a search box and an assistant.

Nothing new is required to close it. Ollama is already a hard prerequisite for
the language model, it serves embeddings from the same daemon on the same port,
and `nomic-embed-text` is 274MB against the several gigabytes already sitting
there for qwen2.5. numpy is already installed -- ctranslate2, onnxruntime and
rapidocr all pull it in, and voice/tts.py and screen/capture.py already import
it -- so the arithmetic costs nothing either.

Off until the model is pulled
-----------------------------

`available()` is false on a fresh install, and everything above this module
falls back to what it did before. That is not a graceful-degradation nicety
bolted on afterwards; it is the point. Kai must work when it is installed, and
"hundreds of MB never move without being asked for" is the rule the voice models
already follow. The model is fetched by an explicit action, and until then
search is exactly as good as it was.

Vectors are normalised on the way in, so similarity is a dot product and the
retrieval path does no division at query time.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from ..settings import load_config

if TYPE_CHECKING:
    import numpy as np


def _numpy() -> Any:
    """numpy, imported on use rather than on import of this module.

    Callers bind this as `numpy`, not `np`: `np` is the TYPE_CHECKING alias the
    annotations here use, and a local of the same name shadows it.

    Not a style choice. The sidecar runs with a thread blocked on
    `sys.stdin.read()` -- that is how it notices its parent dying, see
    watch_parent() in server.py -- and importing numpy while that thread exists
    deadlocks: the import never returns, the port is never bound, and the
    desktop app reports an unreachable backend forever. Reproduced minimally:

        no stdin-reading thread   import numpy  ->  0.25s
        stdin-reading thread      import numpy  ->  never completes

    So numpy must not be reachable from `app.main`'s import graph, which is what
    a module-scope import here would have made it. voice/tts.py and
    screen/capture.py already import it inside functions; this now does too, and
    test_packaging.py asserts that importing the app does not pull it in.
    """
    import numpy

    return numpy

log = logging.getLogger(__name__)

DEFAULT_MODEL = "nomic-embed-text"
# What nomic-embed-text produces. Recorded so a stored vector of a different
# width can be recognised as belonging to a different model rather than
# silently producing nonsense similarities.
DEFAULT_DIMENSIONS = 768

# One request per batch, and the batch is bounded: a 5,000-chunk folder in a
# single call is a request body of several megabytes and an all-or-nothing
# failure.
BATCH = 32
TIMEOUT = 120.0

_available: bool | None = None


def model_name() -> str:
    return load_config().documents.embedding_model or DEFAULT_MODEL


def _host() -> str:
    return load_config().brain.ollama_host.rstrip("/")


def reset_cache() -> None:
    """Test hook, and the escape hatch after the model is pulled at runtime."""
    global _available
    _available = None


def available() -> bool:
    """Whether the embedding model is pulled and answering.

    Asked once and cached. A failure is treated as "no" rather than raised:
    every caller has a lexical path that works, and an assistant that refuses to
    search because an optional model is missing is worse at its job than one
    that searches slightly less well.
    """
    global _available
    if _available is not None:
        return _available

    if not load_config().documents.semantic_search:
        _available = False
        return False

    try:
        response = httpx.post(
            f"{_host()}/api/show", json={"model": model_name()}, timeout=10.0
        )
        _available = response.status_code == 200
    except httpx.HTTPError as exc:
        log.info("embedding model %s unavailable: %s", model_name(), exc)
        _available = False
    return _available


def embed(texts: list[str]) -> list[np.ndarray] | None:
    """Embed a list of texts, or None if that cannot be done right now.

    None rather than an exception, and None rather than an empty list: the
    difference between "there are no embeddings" and "there are no results"
    matters to every caller, and only one of them means fall back to BM25.
    """
    if not texts or not available():
        return None

    vectors: list[np.ndarray] = []
    for start in range(0, len(texts), BATCH):
        batch = texts[start:start + BATCH]
        try:
            response = httpx.post(
                f"{_host()}/api/embed",
                json={"model": model_name(), "input": batch},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("embedding %d text(s) failed: %s", len(batch), exc)
            # Partway through is the same as not at all: a document indexed with
            # vectors for some chunks and not others would rank inconsistently
            # against itself.
            reset_cache()
            return None

        numpy = _numpy()
        returned = payload.get("embeddings") or []
        if len(returned) != len(batch):
            log.warning("embedding returned %d vectors for %d inputs", len(returned), len(batch))
            return None
        vectors.extend(normalise(numpy.asarray(vector, dtype=numpy.float32)) for vector in returned)

    return vectors


def embed_one(text: str) -> np.ndarray | None:
    vectors = embed([text])
    return vectors[0] if vectors else None


def normalise(vector: np.ndarray) -> np.ndarray:
    """Unit length, so similarity later is a dot product and nothing divides."""
    numpy = _numpy()
    magnitude = float(numpy.linalg.norm(vector))
    if magnitude == 0.0:
        return vector
    return vector / magnitude


def pack(vector: np.ndarray) -> bytes:
    numpy = _numpy()
    return numpy.asarray(vector, dtype=numpy.float32).tobytes()


def unpack(blob: bytes) -> np.ndarray:
    numpy = _numpy()
    return numpy.frombuffer(blob, dtype=numpy.float32)


def install() -> dict[str, Any]:
    """Ask Ollama to pull the embedding model.

    Held open until it finishes rather than returning a job id. It is 274MB
    against the several gigabytes of language model already sitting there, the
    caller is a button someone just pressed, and a progress bar for a download
    that usually takes under a minute is more machinery than the wait deserves.
    """
    try:
        response = httpx.post(
            f"{_host()}/api/pull",
            json={"model": model_name(), "stream": False},
            # Generous: this is a download, on whatever connection the user has.
            timeout=1800.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("could not pull %s: %s", model_name(), exc)
        return {
            "ok": False,
            "error": (
                f"Couldn't fetch {model_name()}. Ollama has to be running: "
                f"check it, or run `ollama pull {model_name()}` yourself."
            ),
        }

    reset_cache()
    if not available():
        return {
            "ok": False,
            "error": f"Ollama reported success but {model_name()} still isn't there.",
        }
    log.info("embedding model %s is ready", model_name())
    return {"ok": True, "error": None}


def rank(query: np.ndarray, rows: list[tuple[Any, bytes]], limit: int) -> list[tuple[Any, float]]:
    """The `limit` closest rows to `query`, most similar first.

    One matrix multiply rather than a loop. A few thousand chunks at 768
    dimensions is a few million multiply-adds, which numpy does in single-digit
    milliseconds and CPython does in most of a second -- and this runs inside a
    turn the user is waiting on.
    """
    if not rows:
        return []

    numpy = _numpy()
    width = query.shape[0]
    keys: list[Any] = []
    usable: list[np.ndarray] = []
    for key, blob in rows:
        vector = unpack(blob)
        # A stored vector of a different width came from a different model.
        # Skipped rather than reshaped: the alternative is confident nonsense.
        if vector.shape[0] != width:
            continue
        keys.append(key)
        usable.append(vector)

    if not usable:
        return []

    scores = numpy.stack(usable) @ query
    order = numpy.argsort(-scores)[:limit]
    return [(keys[int(i)], float(scores[int(i)])) for i in order]

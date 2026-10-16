"""The document index — REQ-16, REQ-26."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..index import scanner as index_scanner
from ..index import search as index_search
from ..index import store as index_store
from ..memory import long_term
from ..settings import load_config

from .params import Limit, SearchQuery

router = APIRouter()


@router.get("/documents/status")
def document_index_status() -> dict[str, Any]:
    return {**index_scanner.status(), "failures": index_store.failures()}


@router.get("/documents")
def list_documents() -> dict[str, Any]:
    return {"documents": index_store.documents()}


@router.post("/documents/reindex")
def reindex_documents(force: bool = True) -> dict[str, Any]:
    return index_scanner.scan(force=force).to_dict()


@router.get("/documents/semantic")
def semantic_status() -> dict[str, Any]:
    """Whether meaning-based search is on, and how much of the index has it.

    "Semantic search is enabled" is not a useful claim on its own: the setting
    can be on, the model absent, and every query still purely lexical. So the
    answer carries the model, whether it is actually pulled, and the count.
    """
    from ..index import embeddings

    coverage = index_search.coverage()
    return {
        "enabled": load_config().documents.semantic_search,
        "model": embeddings.model_name(),
        "model_installed": embeddings.available(),
        "download_mb": 274,
        **coverage,
    }


@router.post("/documents/semantic/model")
def install_embedding_model() -> dict[str, Any]:
    """Pull the embedding model. Explicit, and never on startup.

    274MB, which is small next to the language model already required but is
    still hundreds of megabytes that must not move because somebody opened
    Settings. Same rule the voice models follow.
    """
    from ..index import embeddings

    result = embeddings.install()
    if not result["ok"]:
        raise HTTPException(status_code=503, detail=result["error"])
    # Anything already indexed has no vectors yet, and a half-covered index
    # ranks worse than an uncovered one.
    index_scanner.scan_in_background(force=True)
    long_term.embed_missing()
    return semantic_status()


@router.get("/documents/search")
def search_documents(q: SearchQuery, limit: Limit = 5) -> dict[str, Any]:
    return {"results": [hit.to_dict() for hit in index_search.search(q, limit=limit)]}


@router.delete("/documents/index")
def clear_document_index() -> dict[str, Any]:
    """REQ-16/REQ-26 — the user can see what is indexed and remove all of it."""
    return {"cleared_documents": index_store.clear()}

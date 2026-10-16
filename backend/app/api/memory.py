"""Durable facts — REQ-7. Reviewable, and individually deletable."""

from __future__ import annotations

from typing import Any


from fastapi import APIRouter, HTTPException

from ..memory import long_term
from . import schemas

router = APIRouter()


@router.get("/memory", response_model=schemas.MemoryFacts)
def list_memory() -> dict[str, Any]:
    return {"facts": [f.to_dict() for f in long_term.all_facts()]}


@router.delete("/memory/{fact_id}", response_model=schemas.DeletedFact)
def delete_memory(fact_id: str) -> dict[str, Any]:
    fact = long_term.delete(fact_id)
    if fact is None:
        raise HTTPException(status_code=404, detail="No such memory")
    return {"deleted": fact.to_dict()}


@router.delete("/memory", response_model=schemas.DeletedFacts)
def delete_all_memory() -> dict[str, Any]:
    return {"deleted": long_term.delete_all()}

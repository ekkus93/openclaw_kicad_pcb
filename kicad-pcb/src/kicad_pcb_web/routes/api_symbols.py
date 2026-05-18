"""Symbol search API routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query

from ..schemas import SymbolSearchResponse
from ..services.symbols import search_symbols

router = APIRouter()


@router.get("/symbols/search", response_model=SymbolSearchResponse)
def symbol_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    symbols_dir: str | None = None,
) -> SymbolSearchResponse:
    """Search symbol libraries by free-text query."""

    return search_symbols(
        query=q,
        symbols_dir=Path(symbols_dir).expanduser().resolve() if symbols_dir else None,
        limit=limit,
    )

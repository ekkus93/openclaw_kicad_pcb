"""KiCad symbol search services."""

from __future__ import annotations

from pathlib import Path

from kicad_pcb.commands.search import _keywords, _scan_dir_with_cache, _score
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.symbol_cache import SymbolCache
from kicad_pcb.symbol_index import resolve_symbol_dirs

from ..schemas import SymbolSearchResponse, SymbolSearchResult


def search_symbols(
    *,
    query: str,
    symbols_dir: Path | None,
    limit: int = 20,
) -> SymbolSearchResponse:
    """Search symbol libraries and return stable API results."""

    stripped_query = query.strip()
    if not stripped_query:
        raise UserError("Query must not be empty.", code=ErrorCode.USER_ERROR)
    if not 1 <= limit <= 100:
        raise UserError(
            f"Limit must be between 1 and 100, got {limit}.",
            code=ErrorCode.USER_ERROR,
            details={"limit": limit},
        )

    match_keywords = _keywords(stripped_query)
    cache = SymbolCache()
    directories = (
        (symbols_dir,)
        if symbols_dir is not None
        else resolve_symbol_dirs(symbols_dir=None).dirs
    )
    scored: list[tuple[int, SymbolSearchResult]] = []

    for directory in directories:
        for cached_symbol in _scan_dir_with_cache(directory, match_keywords, cache):
            library = cached_symbol.lib_file.stem
            qualified_name = f"{library}:{cached_symbol.sym_name}"
            score = _score(match_keywords, qualified_name, cached_symbol.description)
            if score <= 0:
                continue
            scored.append(
                (
                    score,
                    SymbolSearchResult(
                        library=library,
                        name=cached_symbol.sym_name,
                        qualified_name=qualified_name,
                        aliases=[],
                        keywords=match_keywords,
                    ),
                )
            )

    scored.sort(key=lambda item: (-item[0], item[1].qualified_name))
    return SymbolSearchResponse(
        query=stripped_query,
        results=[result for _, result in scored[:limit]],
    )

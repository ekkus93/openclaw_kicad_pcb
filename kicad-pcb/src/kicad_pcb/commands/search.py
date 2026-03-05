"""search-symbols and build-symbol-index commands.

Symbol search uses a two-phase approach:

1. **grep pre-screen** — skip library files whose raw text contains none of
   the query keywords (fast, O(file bytes) but done by grep).
2. **Cache lookup** — for pre-screened files, read metadata from a persistent
   SQLite cache (``~/.openclaw/kicad-pcb/symbol_index.db``) instead of
   reparsing the file.  A cache miss causes the file to be parsed once and
   the result stored; subsequent queries for any keyword in the same file are
   instant.

The ``build-symbol-index`` command pre-populates the cache for all symbol
files in the resolved directories so the first ``search-symbols`` call is
also fast.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..errors import ErrorCode, UserError
from ..results import BuildSymbolIndexResult, DebugSymbolResult, SearchSymbolsResult, SymbolMatch
from ..sch_doc import read_lib_symbol_pins
from ..symbol_cache import CachedSymbol, SymbolCache
from ..symbol_index import resolve_symbol_dirs

# ---------------------------------------------------------------------------
# Helpers for extracting individual symbol blocks from raw library text
# ---------------------------------------------------------------------------

# Matches a top-level symbol header line, e.g.:  (symbol "R" ...
# Top-level entries start with exactly two spaces of indentation.
_TOP_SYM_HEADER_RE = re.compile(r'^\s{0,2}\(symbol\s+"([^"]+)"', re.MULTILINE)

# Sub-unit suffix: "R_0_1", "C_Polarized_1_2", etc.
_SUB_UNIT_RE = re.compile(r"_\d+_\d+$")

# Matches  (extends "BaseSymbolName")  inside a symbol block.
_EXTENDS_NAME_RE = re.compile(r'\(extends\s+"([^"]+)"')


def _extract_symbol_blocks(raw_text: str) -> list[tuple[str, str]]:
    """Return ``[(sym_name, block_text), ...]`` for all top-level symbols.

    Each *block_text* is the complete s-expression string for that symbol,
    extracted by tracking parenthesis depth from the header ``(``.  Sub-unit
    entries (e.g. ``"R_0_1"``) are excluded.

    This avoids parsing the whole library file and is significantly faster
    on large libraries like ``Device.kicad_sym``.
    """
    results: list[tuple[str, str]] = []
    for m in _TOP_SYM_HEADER_RE.finditer(raw_text):
        sym_name = m.group(1)
        if _SUB_UNIT_RE.search(sym_name):
            continue
        start = m.start()
        depth = 0
        i = start
        n = len(raw_text)
        in_string = False
        while i < n:
            ch = raw_text[i]
            if in_string:
                if ch == "\\" and i + 1 < n:
                    i += 2
                    continue
                if ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    results.append((sym_name, raw_text[start : i + 1]))
                    break
            i += 1
    return results


def _get_ki_description_from_block(block_text: str) -> str:
    """Return the ``ki_description`` property value from a symbol block string.

    Uses a quick regex scan rather than full s-expression parsing.
    """
    m = re.search(r'\(property\s+"ki_description"\s+"([^"]*)"', block_text)
    return m.group(1) if m else ""


def _count_pins_in_block(block_text: str) -> int:
    """Count all ``(pin ...`` entries in a symbol block string."""
    return len(re.findall(r"\(pin\s+", block_text))


def _resolve_pin_count(
    block_text: str,
    sym_blocks: dict[str, str],
    max_depth: int = 8,
) -> int:
    """Return the pin count for a symbol, walking ``(extends ...)`` chains.

    For symbols that declare ``(extends "Base")``, the own block has no
    ``(pin ...`` entries.  This function follows the extends chain using the
    already-extracted *sym_blocks* mapping (built from the same file), so no
    extra I/O or re-parsing is needed.
    """
    current = block_text
    for _ in range(max_depth):
        count = _count_pins_in_block(current)
        if count > 0:
            return count
        m = _EXTENDS_NAME_RE.search(current)
        if m is None:
            return 0
        base_name = m.group(1)
        current = sym_blocks.get(base_name, "")
        if not current:
            return 0
    return 0


def _keywords(text: str) -> list[str]:
    """Lower-case word tokens from *text* for fuzzy matching."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _score(match_kws: list[str], sym_id: str, description: str) -> int:
    """Return a relevance score ≥ 1 if *sym_id* or *description* match all *match_kws*.

    Returns 0 if any keyword is absent.  Higher score = more specific match.
    """
    haystack = " ".join(_keywords(sym_id) + _keywords(description))
    total = 0
    for kw in match_kws:
        if kw not in haystack:
            return 0
        # Exact word boundary in sym_id gets a bonus
        total += 2 if re.search(r"\b" + re.escape(kw) + r"\b", sym_id.lower()) else 1
    return total


def _grep_matching_files(sym_dir: Path, match_kws: list[str]) -> list[Path]:
    """Return library files in *sym_dir* that contain at least one keyword.

    Uses ``grep -li`` (case-insensitive file-list mode) for speed.
    Only ``.kicad_sym`` files are considered.

    Fail-fast behavior: grep command failures/timeouts are surfaced as
    :class:`UserError` instead of silently degrading to slower fallback scans.
    """
    all_files = list(sym_dir.glob("*.kicad_sym"))
    if not all_files:
        return []

    # grep pre-screen — orders of magnitude faster than Python I/O for many files
    try:
        # Build a single alternation pattern so grep only runs once
        pattern = "|".join(re.escape(kw) for kw in match_kws)
        result = subprocess.run(
            ["grep", "-ril", "--include=*.kicad_sym", "-E", pattern, str(sym_dir)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode in (0, 1):  # 0 = found, 1 = no match
            return [Path(p) for p in result.stdout.splitlines() if p.endswith(".kicad_sym")]
        raise UserError(
            f"grep failed while scanning symbol libraries in '{sym_dir}'",
            code=ErrorCode.IO_ERROR,
            details={
                "path": str(sym_dir),
                "returncode": result.returncode,
                "stderr": result.stderr.strip(),
            },
        )
    except subprocess.TimeoutExpired as exc:
        raise UserError(
            f"grep timed out while scanning symbol libraries in '{sym_dir}'",
            code=ErrorCode.IO_ERROR,
            details={
                "path": str(sym_dir),
                "timeout_seconds": 10,
            },
        ) from exc
    except OSError as exc:
        raise UserError(
            f"failed to execute grep while scanning symbol libraries in '{sym_dir}': {exc}",
            code=ErrorCode.IO_ERROR,
            details={
                "path": str(sym_dir),
            },
        ) from exc


def _parse_file_to_cached(lib_file: Path) -> list[CachedSymbol]:
    """Parse *lib_file* and return one :class:`CachedSymbol` per top-level symbol.

    This is the slow path — O(file size) Python parsing.  Results are stored
    in the :class:`~kicad_pcb.symbol_cache.SymbolCache` so this is only called
    once per file per install (or when the file changes).
    """
    try:
        raw_text = lib_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    symbols: list[CachedSymbol] = []
    blocks = _extract_symbol_blocks(raw_text)
    # Build a name→block map so extends-chain lookups stay in-memory.
    sym_blocks: dict[str, str] = {name: text for name, text in blocks}
    for sym_name, block_text in blocks:
        symbols.append(
            CachedSymbol(
                lib_file=lib_file,
                sym_name=sym_name,
                description=_get_ki_description_from_block(block_text),
                pin_count=_resolve_pin_count(block_text, sym_blocks),
            )
        )
    return symbols


def _scan_dir_with_cache(
    sym_dir: Path,
    match_kws: list[str],
    cache: SymbolCache,
) -> list[CachedSymbol]:
    """Return all :class:`CachedSymbol` entries from *sym_dir* whose file
    text contains at least one of *match_kws*.

    grep pre-screens which ``.kicad_sym`` files are candidates; the cache is
    then consulted for each candidate.  Files absent from or stale in the
    cache are parsed and stored before the result is returned.
    """
    candidate_files = _grep_matching_files(sym_dir, match_kws)
    results: list[CachedSymbol] = []
    for lib_file in sorted(candidate_files):
        cached = cache.get_symbols(lib_file)
        if cached is None:
            # Cache miss — parse and populate.
            cached = _parse_file_to_cached(lib_file)
            cache.store_symbols(lib_file, cached)
        results.extend(cached)
    return results


def cmd_search_symbols(args) -> SearchSymbolsResult:
    """Search installed KiCad symbol libraries for symbols matching a keyword query.

    Uses a two-phase approach for speed:

    1. **grep pre-screen** — only consider library files whose raw text
       contains at least one keyword.
    2. **Cache lookup** — reads symbol metadata from
       ``~/.openclaw/kicad-pcb/symbol_index.db`` instead of reparsing the
       file.  A cache miss triggers a one-time file parse that populates the
       cache for future queries.

    Returns every symbol whose ``Lib:Name`` or ``ki_description`` property
    contains *all* query keywords (case-insensitive), sorted by relevance then
    alphabetically.

    Run ``build-symbol-index`` once after installing KiCad to pre-populate the
    cache so the very first search is also fast.

    Args:
        args: Parsed CLI namespace.  Expected attributes:

            * ``query`` — space-separated keyword string (required)
            * ``symbols_dir`` — optional path to an explicit library dir
            * ``limit`` — maximum number of results (default 20)
    """
    raw_query: str = args.query.strip()
    symbols_dir_raw = getattr(args, "symbols_dir", None)
    symbols_dir: Path | None = Path(symbols_dir_raw) if symbols_dir_raw else None
    limit: int = getattr(args, "limit", 20)

    match_kws = _keywords(raw_query)
    if not match_kws:
        return SearchSymbolsResult(
            query=raw_query,
            matches=(),
            symbols_dirs=(),
        )

    # When the caller provides an explicit --symbols-dir, scope the search to
    # that directory only — do not append system/repo-local directories behind
    # it.  The explicit flag means "search HERE", not "search here then fall
    # through to system libs".  Without an explicit dir we use the full
    # resolution chain (env var → repo-local → system KiCad candidates).
    if symbols_dir is not None:
        dirs: tuple[Path, ...] = (symbols_dir,)
    else:
        dirs = resolve_symbol_dirs(symbols_dir=None).dirs

    searched_dirs: list[str] = [str(d) for d in dirs]
    cache = SymbolCache()
    scored: list[tuple[int, SymbolMatch]] = []

    for sym_dir in dirs:
        for cached_sym in _scan_dir_with_cache(sym_dir, match_kws, cache):
            lib_name = cached_sym.lib_file.stem
            sym_id = f"{lib_name}:{cached_sym.sym_name}"
            score = _score(match_kws, sym_id, cached_sym.description)
            if score > 0:
                scored.append(
                    (score, SymbolMatch(sym_id, cached_sym.description, cached_sym.pin_count))
                )

    # Sort: descending score, then ascending symbol_id for determinism
    scored.sort(key=lambda t: (-t[0], t[1].symbol_id))
    matches = tuple(m for _, m in scored[:limit])

    return SearchSymbolsResult(
        query=raw_query,
        matches=matches,
        symbols_dirs=tuple(searched_dirs),
    )


def cmd_debug_symbol(args) -> DebugSymbolResult:
    """Resolve and display full pin list and extends chain for a single symbol.

    Parses the named ``.kicad_sym`` library file, follows any ``(extends ...)``
    chain, and returns the complete set of inherited pin numbers together with
    metadata about the extends relationship.

    Useful for diagnosing broken extends chains or verifying pin numbers before
    writing Circuit IR JSON.

    Args:
        args: Parsed CLI namespace.  Expected attributes:

            * ``symbol`` — ``"LibName:SymName"`` string (required)
            * ``symbols_dir`` — optional path to an explicit library dir
    """
    symbol: str = getattr(args, "symbol", "").strip()
    symbols_dir_raw = getattr(args, "symbols_dir", None)
    symbols_dir: Path | None = Path(symbols_dir_raw) if symbols_dir_raw else None

    if ":" not in symbol:
        raise UserError(
            f"Symbol must be in 'LibName:SymName' format, got: {symbol!r}",
            code=ErrorCode.USER_ERROR,
        )
    lib_name, sym_name = symbol.split(":", 1)

    if symbols_dir is not None:
        dirs: tuple[Path, ...] = (symbols_dir,)
    else:
        dirs = resolve_symbol_dirs(symbols_dir=None).dirs

    # Locate the library file in the first matching directory.
    lib_file: Path | None = None
    resolved_dir: Path | None = None
    for sym_dir in dirs:
        candidate = sym_dir / f"{lib_name}.kicad_sym"
        if candidate.exists():
            lib_file = candidate
            resolved_dir = sym_dir
            break

    if lib_file is None or resolved_dir is None:
        raise UserError(
            f"Library '{lib_name}' not found in any symbols directory.",
            code=ErrorCode.SYMBOL_NOT_FOUND,
        )

    # Check symbol exists before trying pin resolution (pins list is empty
    # for both "not found" and "broken extends chain"; we must distinguish).
    raw_text = lib_file.read_text(encoding="utf-8")
    sym_blocks = dict(_extract_symbol_blocks(raw_text))
    if sym_name not in sym_blocks:
        raise UserError(
            f"Symbol '{sym_name}' not found in library '{lib_name}'.",
            code=ErrorCode.SYMBOL_NOT_FOUND,
        )

    # Resolve pin list through the full extends chain.
    pins = read_lib_symbol_pins(lib_name, sym_name, symbols_dir=resolved_dir)

    # Derive extends_base from the raw block text.
    extends_base: str | None = None
    m = _EXTENDS_NAME_RE.search(sym_blocks[sym_name])
    if m:
        extends_base = f"{lib_name}:{m.group(1)}"

    return DebugSymbolResult(
        symbol_id=symbol,
        extends_base=extends_base,
        pin_numbers=tuple(pins),
        pin_count=len(pins),
    )


def cmd_build_symbol_index(args) -> BuildSymbolIndexResult:
    """Pre-populate the symbol cache for all ``.kicad_sym`` files.

    Scans all library files in the resolved symbol directories and parses any
    that are absent from or stale in the cache.  Run this once after
    installing or upgrading KiCad so that subsequent ``search-symbols`` calls
    are instant.

    Already-fresh cache entries are skipped, so re-running is cheap.

    Args:
        args: Parsed CLI namespace.  Expected attributes:

            * ``symbols_dir`` — optional path to an explicit library dir
    """
    symbols_dir_raw = getattr(args, "symbols_dir", None)
    symbols_dir: Path | None = Path(symbols_dir_raw) if symbols_dir_raw else None

    if symbols_dir is not None:
        dirs: tuple[Path, ...] = (symbols_dir,)
    else:
        dirs = resolve_symbol_dirs(symbols_dir=None).dirs

    cache = SymbolCache()
    total_files = 0
    updated_files = 0

    for sym_dir in dirs:
        for lib_file in sorted(sym_dir.glob("*.kicad_sym")):
            total_files += 1
            if cache.get_symbols(lib_file) is not None:
                continue  # already fresh
            symbols = _parse_file_to_cached(lib_file)
            cache.store_symbols(lib_file, symbols)
            updated_files += 1

    stats = cache.stats()
    return BuildSymbolIndexResult(
        dirs_scanned=tuple(str(d) for d in dirs),
        files_scanned=total_files,
        files_updated=updated_files,
        total_indexed_symbols=stats["indexed_symbols"],
    )

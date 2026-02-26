"""search-symbols command: discover available KiCad library symbols by keyword."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..results import SearchSymbolsResult, SymbolMatch
from ..symbol_index import resolve_symbol_dirs

# ---------------------------------------------------------------------------
# Helpers for extracting individual symbol blocks from raw library text
# ---------------------------------------------------------------------------

# Matches a top-level symbol header line, e.g.:  (symbol "R" ...
# Top-level entries start with exactly two spaces of indentation.
_TOP_SYM_HEADER_RE = re.compile(r'^\s{0,2}\(symbol\s+"([^"]+)"', re.MULTILINE)

# Sub-unit suffix: "R_0_1", "C_Polarized_1_2", etc.
_SUB_UNIT_RE = re.compile(r"_\d+_\d+$")


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

    Uses ``grep -li`` (case-insensitive file-list mode) for speed when
    available, falling back to a Python read-and-search if grep is absent.
    Only ``.kicad_sym`` files are considered.
    """
    all_files = list(sym_dir.glob("*.kicad_sym"))
    if not all_files:
        return []

    # Try grep first — orders of magnitude faster than Python I/O for many files
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
    except (OSError, subprocess.TimeoutExpired):
        pass

    # Fallback: Python-based scan
    pre = re.compile("|".join(re.escape(kw) for kw in match_kws), re.IGNORECASE)
    matched: list[Path] = []
    for f in all_files:
        try:
            raw = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if pre.search(raw):
            matched.append(f)
    return matched


def cmd_search_symbols(args) -> SearchSymbolsResult:
    """Search installed KiCad symbol libraries for symbols matching a keyword query.

    Scans all ``.kicad_sym`` files in the resolved symbol directories.  Uses a
    fast two-phase approach:

    1. **Pre-screen** each library file with a regex scan — skip files whose
       raw text contains none of the keywords.
    2. **Block extraction** — extract individual top-level symbol blocks from
       the raw text without parsing the whole library file.

    Returns every symbol whose ``Lib:Name`` or ``ki_description`` property
    contains all of the query keywords (case-insensitive), sorted by relevance
    then alphabetically.

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

    scored: list[tuple[int, SymbolMatch]] = []

    for sym_dir in dirs:
        candidate_files = _grep_matching_files(sym_dir, match_kws)
        for lib_file in sorted(candidate_files):
            lib_name = lib_file.stem
            try:
                raw_text = lib_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for sym_name, block_text in _extract_symbol_blocks(raw_text):
                sym_id = f"{lib_name}:{sym_name}"
                description = _get_ki_description_from_block(block_text)
                score = _score(match_kws, sym_id, description)
                if score > 0:
                    pin_count = _count_pins_in_block(block_text)
                    scored.append((score, SymbolMatch(sym_id, description, pin_count)))

    # Sort: descending score, then ascending symbol_id for determinism
    scored.sort(key=lambda t: (-t[0], t[1].symbol_id))

    matches = tuple(m for _, m in scored[:limit])

    return SearchSymbolsResult(
        query=raw_query,
        matches=matches,
        symbols_dirs=tuple(searched_dirs),
    )

"""Symbol metadata index (memoized pin lookup) for deterministic IR validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import SYMBOLS_CANDIDATES
from .errors import ErrorCode, UserError
from .sch_doc import read_lib_symbol_pins

REPO_LOCAL_SYMBOLS_DIR = Path(__file__).resolve().parent / "resources" / "symbols"


@dataclass(frozen=True)
class SymbolsResolution:
    """Resolved symbol directories in effective lookup order."""

    dirs: tuple[Path, ...]


def resolve_symbol_dirs(*, symbols_dir: Path | None = None) -> SymbolsResolution:
    """Resolve symbol lookup directories by precedence.

    Order:
    1) explicit ``symbols_dir`` (if provided)
    2) repo-local ``kicad_pcb/resources/symbols`` if present
    3) system KiCad candidates
    """
    ordered: list[Path] = []

    if symbols_dir is not None:
        ordered.append(symbols_dir)

    if REPO_LOCAL_SYMBOLS_DIR.is_dir() and any(REPO_LOCAL_SYMBOLS_DIR.glob("*.kicad_sym")):
        ordered.append(REPO_LOCAL_SYMBOLS_DIR)

    for candidate in SYMBOLS_CANDIDATES:
        if candidate.is_dir():
            ordered.append(candidate)

    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in ordered:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            deduped.append(resolved)

    return SymbolsResolution(dirs=tuple(deduped))


class SymbolIndex:
    """Memoized symbol pin lookup facade around ``read_lib_symbol_pins``."""

    def __init__(
        self,
        *,
        symbols_dir: Path | None = None,
        fallback_dirs: list[Path] | None = None,
    ) -> None:
        resolved = resolve_symbol_dirs(symbols_dir=symbols_dir).dirs
        if fallback_dirs:
            resolved = resolved + tuple(path.resolve() for path in fallback_dirs if path.is_dir())
        self._dirs: tuple[Path, ...] = resolved
        if not self._dirs:
            raise UserError(
                "No KiCad symbol libraries found. Cannot resolve symbol pins.",
                code=ErrorCode.SYMBOL_DIR_MISSING,
                details={
                    "searched_candidates": [str(p) for p in SYMBOLS_CANDIDATES],
                    "repo_local_dir": str(REPO_LOCAL_SYMBOLS_DIR),
                    "hint": "Pass --symbols-dir <path> or install KiCad symbol libraries.",
                },
            )
        self._cache: dict[str, set[str]] = {}

    @property
    def directories(self) -> tuple[Path, ...]:
        """Effective lookup directories in precedence order."""
        return self._dirs

    def get_pins(self, symbol_id: str) -> set[str]:
        """Return the set of valid pin numbers for *symbol_id*.

        Raises ``UserError(SYMBOL_NOT_FOUND)`` if the symbol is not present in
        any searched library file.

        Raises ``UserError(SYMBOL_HAS_NO_PINS)`` if the symbol is declared in a
        library file but resolves to 0 pins (e.g. a broken ``extends`` chain
        where the base symbol does not exist).
        """
        if symbol_id in self._cache:
            return self._cache[symbol_id]

        if ":" not in symbol_id:
            raise UserError(
                f"Invalid symbol id '{symbol_id}' (expected Lib:Symbol)",
                code=ErrorCode.SYMBOL_NOT_FOUND,
                details={"symbol": symbol_id},
            )

        lib_name, sym_name = symbol_id.split(":", 1)
        for directory in self._dirs:
            pins = read_lib_symbol_pins(lib_name, sym_name, symbols_dir=directory)
            if pins:
                pin_set = set(pins)
                self._cache[symbol_id] = pin_set
                return pin_set

        # Distinguish "declared in file but 0 pins" (broken extends chain, or
        # genuinely pin-free symbol) from "name not in any library file at all".
        # A regex text scan avoids a second full s-expression parse.
        _sym_header = re.compile(r'\(symbol\s+"' + re.escape(sym_name) + r'"')
        for directory in self._dirs:
            lib_file = directory / f"{lib_name}.kicad_sym"
            if lib_file.exists():
                try:
                    text = lib_file.read_text(encoding="utf-8", errors="replace")
                    if _sym_header.search(text):
                        raise UserError(
                            f"Symbol '{symbol_id}' is declared in library "
                            f"'{lib_name}' but resolves to 0 pins "
                            f"(possible broken extends chain or pin-free symbol)",
                            code=ErrorCode.SYMBOL_HAS_NO_PINS,
                            details={
                                "symbol": symbol_id,
                                "lib_file": str(lib_file),
                                "hint": (
                                    "Check the (extends ...) chain in the "
                                    "library file — the base symbol may be "
                                    "missing or misspelled."
                                ),
                            },
                        )
                except OSError as exc:
                    raise UserError(
                        f"Failed to read symbol library while probing '{symbol_id}': {exc}",
                        code=ErrorCode.IO_ERROR,
                        details={
                            "symbol": symbol_id,
                            "lib_file": str(lib_file),
                        },
                    ) from exc

        raise UserError(
            f"Symbol not found in libraries: {symbol_id}",
            code=ErrorCode.SYMBOL_NOT_FOUND,
            details={
                "symbol": symbol_id,
                "searched_dirs": [str(path) for path in self._dirs],
            },
        )

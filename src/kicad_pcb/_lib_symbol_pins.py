"""KiCad symbol pin readers (pins, pin-at, unit pins, power unit)."""

from __future__ import annotations

from pathlib import Path

from ._lib_symbol_def import read_lib_symbol_def_flat
from ._lib_symbol_primitives import (
    _UNIT_SUBSYMBOL_RE,
    _collect_pin_at,
    _collect_pin_electrical_types,
    _collect_pin_electrical_types_by_number,
    _collect_pin_numbers,
    _collect_subsymbols,
    _find_lib_symbol,
    _resolve_sym_chain,
    _symbol_id,
)


def read_lib_symbol_pins(
    lib_name: str,
    sym_name: str,
    *,
    symbols_dir: Path | None = None,
) -> list[str]:
    """Return deduplicated pin number strings for a library symbol.

    Returns an empty list when the library file, symbol, or pin data is not
    available. Raises on library parse/read failures. The search is performed
    on the full AST subtree of the symbol, so sub-unit pins are included
    without any character-window heuristics.

    Parameters
    ----------
    lib_name:    Library name (e.g. ``"Device"``).
    sym_name:    Symbol name within the library (e.g. ``"R"``).
    symbols_dir: Directory containing ``.kicad_sym`` files.
    """
    resolved = _resolve_sym_chain(lib_name, sym_name, symbols_dir)
    if resolved is None:
        return []
    lib_root, chain, _complete = resolved

    # Collect pins base-first.  Derived symbols may redefine pins from the
    # base; ``seen`` deduplicates by pin number so each appears only once.
    seen: set[str] = set()
    result: list[str] = []
    for name in reversed(chain):  # reversed = base first
        node = _find_lib_symbol(lib_root, name)
        if node is None:
            continue
        for pin_num in _collect_pin_numbers(node):
            if pin_num not in seen:
                seen.add(pin_num)
                result.append(pin_num)
    return result


def read_lib_symbol_pin_at(
    lib_name: str,
    sym_name: str,
    *,
    symbols_dir: Path | None = None,
) -> dict[str, tuple[float, float, float]]:
    """Return pin connection-point coordinates for *lib_name:sym_name*.

    Follows ``(extends ...)`` chains so inherited pin positions are included.
    Base-symbol positions take priority when a derived symbol redefines a pin.

    Returns a dict of ``{pin_number: (x, y, angle)}`` where:

    * *(x, y)* — pin endpoint in library-local coordinates (mm).
    * *angle* — KiCad pin direction in degrees: 0=right, 90=down, 180=left,
      270=up.  This is the direction **from** the endpoint **toward** the
      symbol body; wire extensions should point in the **opposite** direction.

    Returns an empty dict when the library file or symbol cannot be found.
    Raises on library parse/read failures.

    Parameters
    ----------
    lib_name:    Library name (e.g. ``"Device"``).
    sym_name:    Symbol name within the library (e.g. ``"R"``).
    symbols_dir: Directory containing ``.kicad_sym`` files.
    """
    resolved = _resolve_sym_chain(lib_name, sym_name, symbols_dir)
    if resolved is None:
        return {}
    lib_root, chain, _complete = resolved

    # Collect pin positions base-first; first occurrence wins (same as pins).
    result: dict[str, tuple[float, float, float]] = {}
    for name in reversed(chain):  # reversed = base first
        node = _find_lib_symbol(lib_root, name)
        if node is None:
            continue
        for pin_num, coords in _collect_pin_at(node).items():
            if pin_num not in result:
                result[pin_num] = coords
    return result


def read_lib_symbol_pin_electrical_types(
    lib_name: str,
    sym_name: str,
    *,
    symbols_dir: Path | None = None,
) -> dict[str, str]:
    """Return ``{pin_number: electrical_type}`` for *lib_name:sym_name*."""
    sym_def = read_lib_symbol_def_flat(lib_name, sym_name, symbols_dir=symbols_dir)
    if sym_def is None:
        return {}
    return _collect_pin_electrical_types_by_number(sym_def)


def read_lib_symbol_unit_pins(
    lib_name: str,
    sym_name: str,
    *,
    symbols_dir: Path | None = None,
) -> dict[str, list[str]]:
    """Return ``{unit_number: [pin_numbers...]}`` for a library symbol."""
    sym_def = read_lib_symbol_def_flat(lib_name, sym_name, symbols_dir=symbols_dir)
    if sym_def is None:
        return {}

    unit_pins: dict[str, list[str]] = {}
    for subsymbol in _collect_subsymbols(sym_def):
        subsymbol_id = _symbol_id(subsymbol)
        if subsymbol_id is None:
            continue
        match = _UNIT_SUBSYMBOL_RE.match(subsymbol_id)
        if match is None:
            continue
        unit = match.group(1)
        subsymbol_pins = _collect_pin_numbers(subsymbol)
        if not subsymbol_pins:
            continue
        pins = unit_pins.setdefault(unit, [])
        for pin_num in subsymbol_pins:
            if pin_num not in pins:
                pins.append(pin_num)
    return unit_pins


def read_lib_symbol_unit_pin_at(
    lib_name: str,
    sym_name: str,
    *,
    symbols_dir: Path | None = None,
) -> dict[str, dict[str, tuple[float, float, float]]]:
    """Return ``{unit_number: {pin_number: (x, y, angle)}}`` for a library symbol."""
    sym_def = read_lib_symbol_def_flat(lib_name, sym_name, symbols_dir=symbols_dir)
    if sym_def is None:
        return {}

    unit_pin_at: dict[str, dict[str, tuple[float, float, float]]] = {}
    for subsymbol in _collect_subsymbols(sym_def):
        subsymbol_id = _symbol_id(subsymbol)
        if subsymbol_id is None:
            continue
        match = _UNIT_SUBSYMBOL_RE.match(subsymbol_id)
        if match is None:
            continue
        unit = match.group(1)
        subsymbol_pin_at = _collect_pin_at(subsymbol)
        if not subsymbol_pin_at:
            continue
        pins = unit_pin_at.setdefault(unit, {})
        for pin_num, coords in subsymbol_pin_at.items():
            pins.setdefault(pin_num, coords)
    return unit_pin_at


def read_lib_symbol_power_unit(
    lib_name: str,
    sym_name: str,
    *,
    symbols_dir: Path | None = None,
) -> str | None:
    """Return the unit number for a dedicated power-only sub-unit, if present.

    A symbol is considered to have a separate power unit when exactly one
    numbered sub-symbol contains pins and every pin in that unit uses a
    ``power_*`` electrical type, while at least one other numbered sub-symbol
    contains a non-power pin type.
    """
    sym_def = read_lib_symbol_def_flat(lib_name, sym_name, symbols_dir=symbols_dir)
    if sym_def is None:
        return None

    power_only_units: list[str] = []
    non_power_units = 0
    for subsymbol in _collect_subsymbols(sym_def):
        subsymbol_id = _symbol_id(subsymbol)
        if subsymbol_id is None:
            continue
        match = _UNIT_SUBSYMBOL_RE.match(subsymbol_id)
        if match is None:
            continue
        unit = match.group(1)
        pin_types = _collect_pin_electrical_types(subsymbol)
        if not pin_types:
            continue
        if all(pin_type.startswith("power_") for pin_type in pin_types):
            power_only_units.append(unit)
        else:
            non_power_units += 1

    if len(power_only_units) == 1 and non_power_units >= 1:
        return power_only_units[0]
    return None

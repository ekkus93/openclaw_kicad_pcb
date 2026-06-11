"""Constants, result types, and shared helpers for circuit generation patterns."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .sch_doc import SchematicDoc, read_lib_symbol_def_flat, read_lib_symbol_pins
from .sexpr.builder import L, atom, string

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

#: Vertical offset (mm) from symbol centre to each pin endpoint.
#: Matches the standard KiCad ``Device:R / C / LED`` symbol geometry.
PIN_OFFSET: Final[float] = 2.54

#: Horizontal spacing (mm) between components placed side-by-side.
H_SPACING: Final[float] = 10.16

#: Vertical spacing (mm) between consecutive series components
#: (= 2 × PIN_OFFSET so adjacent pin endpoints coincide).
V_SPACING: Final[float] = PIN_OFFSET * 2  # 5.08 mm


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlacedComponent:
    """Describes a single component placed by a pattern."""

    ref: str
    lib_sym: str
    value: str
    x: float
    y: float
    pins: tuple[str, ...]


@dataclass(frozen=True)
class PatternOutcome:
    """Structured outcome returned by every pattern function.

    Callers (e.g. ``cmd_apply_pattern``) summarise this into an
    :class:`~kicad_pcb.results.ApplyPatternResult` for CLI/JSON output.
    """

    #: Each component that was added to the schematic.
    components: tuple[PlacedComponent, ...]
    #: Net names that were referenced by the pattern (labels placed or used).
    nets: tuple[str, ...]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _new_uuid() -> str:
    """Return a fresh UUID4 hex string (no hyphens — KiCad format)."""
    return str(uuid.uuid4())


def _place_component(  # noqa: PLR0913
    doc: SchematicDoc,
    lib_sym: str,
    ref: str,
    value: str,
    footprint: str,
    x: float,
    y: float,
    *,
    symbols_dir: Path | None,
    project_name: str,
) -> PlacedComponent:
    """Resolve library data and insert a placed symbol into *doc*."""
    lib_name, sym_name = lib_sym.split(":", 1)

    pin_nums = read_lib_symbol_pins(lib_name, sym_name, symbols_dir=symbols_dir)
    if not pin_nums:
        pin_nums = ["1", "2"]

    sym_def = read_lib_symbol_def_flat(lib_name, sym_name, symbols_dir=symbols_dir)
    if sym_def is not None:
        doc.embed_lib_symbol(sym_def)
    else:
        doc.embed_lib_symbol(L(atom("symbol"), string(lib_sym)))

    pin_uuids = [_new_uuid() for _ in pin_nums]
    sym_uuid = _new_uuid()
    doc.add_symbol(
        lib_sym, ref, value, footprint, x, y, sym_uuid, pin_nums, pin_uuids, project_name
    )

    return PlacedComponent(
        ref=ref,
        lib_sym=lib_sym,
        value=value,
        x=x,
        y=y,
        pins=tuple(pin_nums),
    )


def _place_label(doc: SchematicDoc, name: str, x: float, y: float) -> None:
    """Insert a net label at *(x, y)* with a fresh UUID."""
    doc.add_label(name, x, y, _new_uuid())

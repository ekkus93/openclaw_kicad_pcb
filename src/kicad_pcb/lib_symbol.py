"""KiCad symbol library reader helpers.

This module handles all I/O against ``.kicad_sym`` library files: parsing,
caching, extends-chain resolution, pin enumeration, and symbol-definition
extraction.  It has no dependency on :class:`~kicad_pcb.sch_doc.SchematicDoc`
or the AST emitters in :mod:`kicad_pcb.sch_nodes`.

Public API (also re-exported from :mod:`kicad_pcb.sch_doc` for backward
compatibility):

:func:`read_lib_symbol_def`       — extract a single symbol (no extends chain).
:func:`read_lib_symbol_def_chain` — load a symbol plus its full extends chain.
:func:`read_lib_symbol_def_flat`  — load a fully-merged self-contained symbol.
:func:`read_lib_symbol_pins`      — extract pin numbers from a library symbol.
:func:`read_lib_symbol_pin_at`    — extract pin connection-point coordinates.
"""

from __future__ import annotations

from ._lib_symbol_def import (  # noqa: F401
    read_lib_symbol_def,
    read_lib_symbol_def_chain,
    read_lib_symbol_def_flat,
)
from ._lib_symbol_pins import (  # noqa: F401
    read_lib_symbol_pin_at,
    read_lib_symbol_pin_electrical_types,
    read_lib_symbol_pins,
    read_lib_symbol_power_unit,
    read_lib_symbol_unit_pin_at,
    read_lib_symbol_unit_pins,
)
from ._lib_symbol_primitives import _symbol_id  # noqa: F401

__all__ = [
    "read_lib_symbol_def",
    "read_lib_symbol_def_chain",
    "read_lib_symbol_def_flat",
    "read_lib_symbol_pin_at",
    "read_lib_symbol_pin_electrical_types",
    "read_lib_symbol_pins",
    "read_lib_symbol_power_unit",
    "read_lib_symbol_unit_pin_at",
    "read_lib_symbol_unit_pins",
]

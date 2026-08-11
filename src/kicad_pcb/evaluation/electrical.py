"""Compatibility re-exports for electrical equivalence.

The production-safe implementation lives in :mod:`kicad_pcb.electrical_equivalence`.
Corpus evaluation deliberately consumes the same implementation.
"""

from __future__ import annotations

from kicad_pcb.electrical_equivalence import (  # noqa: F401
    ElectricalEquivalenceReport,
    ElectricalMismatch,
    compare_circuit_ir_equivalence,
)

__all__ = [
    "ElectricalEquivalenceReport",
    "ElectricalMismatch",
    "compare_circuit_ir_equivalence",
]

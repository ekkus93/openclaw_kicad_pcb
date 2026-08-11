"""Safe schematic visual-refinement primitives."""

from .electrical import (
    SchematicElectricalBaseline,
    SchematicElectricalVerificationReport,
    build_schematic_electrical_baseline,
    verify_schematic_electrical_invariance,
)
from .transaction import CandidateState, SchematicCandidateTransaction

__all__ = [
    "CandidateState",
    "SchematicCandidateTransaction",
    "SchematicElectricalBaseline",
    "SchematicElectricalVerificationReport",
    "build_schematic_electrical_baseline",
    "verify_schematic_electrical_invariance",
]

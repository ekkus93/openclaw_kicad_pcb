"""Op-amp advisory lint checks (feedback, output, AC-coupling, headphone, speaker).

Implementation is split across:
* :mod:`kicad_pcb.commands._validate_opamp_feedback`  — helpers + feedback/output-sanity
* :mod:`kicad_pcb.commands._validate_opamp_load`      — AC-coupled load, headphone, speaker
* :mod:`kicad_pcb.commands._validate_opamp_topology`  — interstage coupling + stage topology
"""

from __future__ import annotations

from ..circuit_ir import CircuitIR
from ..symbol_index import SymbolIndex
from ._validate_connectivity import (
    _input_coupling_bypass_warnings,
    _output_coupling_bypass_warnings,
)
from ._validate_opamp_feedback import _opamp_feedback_warnings, _opamp_output_sanity_warnings
from ._validate_opamp_load import (
    _headphone_output_impedance_warnings,
    _opamp_ac_coupled_output_load_warnings,
    _speaker_driver_advisories,
)
from ._validate_opamp_topology import (
    _opamp_stage_topology_warnings,
    _split_rail_interstage_coupling_warnings,
)


def _audio_opamp_warnings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    warnings = _input_coupling_bypass_warnings(ir)
    warnings.extend(_output_coupling_bypass_warnings(ir))
    warnings.extend(_opamp_feedback_warnings(ir, symbol_index))
    warnings.extend(_opamp_output_sanity_warnings(ir, symbol_index))
    warnings.extend(_opamp_ac_coupled_output_load_warnings(ir, symbol_index))
    warnings.extend(_headphone_output_impedance_warnings(ir, symbol_index))
    warnings.extend(_speaker_driver_advisories(ir, symbol_index))
    warnings.extend(_split_rail_interstage_coupling_warnings(ir, symbol_index))
    warnings.extend(_opamp_stage_topology_warnings(ir, symbol_index))
    return warnings

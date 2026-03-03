"""Shared validation helpers for netlist commands.

Two public functions are provided:

:func:`full_validate`       — run all three IR validation layers and return
                              the parsed :class:`~kicad_pcb.circuit_ir.CircuitIR`.
:func:`advisory_warnings`   — return non-blocking advisory warnings for
                              common IR mistakes (floating components, single-pin
                              nets).

These are extracted from ``netlist.py`` to avoid duplicating the 3-layer
validation sequence across ``cmd_validate_netlist``, ``cmd_fix_netlist``, and
``cmd_new_from_netlist``.
"""

from __future__ import annotations

from pathlib import Path

from ..circuit_ir import CircuitIR
from ..ir.validate import validate_circuit_ir, validate_ir_symbols
from ..symbol_index import SymbolIndex


def full_validate(path: Path, symbol_index: SymbolIndex) -> CircuitIR:
    """Run all three validation layers on *path* and return the parsed IR.

    Layers (run in order; each raises on first failure):

    1. **Schema** — Pydantic validation of the JSON structure.
       Raises :class:`~kicad_pcb.errors.UserError` (``IR_SCHEMA_INVALID``).
    2. **Semantic** — duplicate refs/nets, zero-pin nets, unknown component refs.
       Raises :class:`~kicad_pcb.errors.UserError` (``IR_SEMANTIC_INVALID``).
    3. **Symbol + pin** — every symbol exists in *symbol_index*, every pin is
       valid for its symbol.
       Raises :class:`~kicad_pcb.errors.UserError` (``SYMBOL_NOT_FOUND`` /
       ``PIN_INVALID``).
    """
    ir = CircuitIR.load(path)  # Layer 1: schema
    validate_circuit_ir(ir)  # Layer 2: semantic
    validate_ir_symbols(ir, symbol_index)  # Layer 3: symbol + pin
    return ir


def advisory_warnings(ir: CircuitIR) -> list[dict[str, object]]:
    """Return non-blocking advisory warnings for common IR mistakes.

    Checks performed:

    * **COMPONENT_NOT_IN_ANY_NET** — components absent from every net will
      appear as floating symbols in the generated schematic.
    * **SINGLE_PIN_NET** — nets with only one connected pin usually indicate
      a missing wire or a copy-paste error in the netlist.

    None of these warnings raise; callers decide whether to surface them.
    """
    warnings: list[dict[str, object]] = []

    refs_in_nets: set[str] = {pin_ref.ref for net in ir.nets for pin_ref in net.pins}
    component_refs = {component.ref for component in ir.components}
    unreferenced = sorted(component_refs - refs_in_nets)
    if unreferenced:
        warnings.append(
            {
                "code": "COMPONENT_NOT_IN_ANY_NET",
                "message": (
                    f"{len(unreferenced)} component(s) are not referenced in any net "
                    "and will be floating in the schematic."
                ),
                "details": {"refs": unreferenced},
            }
        )

    single_pin_nets = [net.name for net in ir.nets if len(net.pins) == 1]
    if single_pin_nets:
        warnings.append(
            {
                "code": "SINGLE_PIN_NET",
                "message": (
                    f"{len(single_pin_nets)} net(s) have only one connected pin. "
                    "This is usually a wiring mistake."
                ),
                "details": {"nets": single_pin_nets},
            }
        )

    return warnings

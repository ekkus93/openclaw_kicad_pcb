"""Electrical equivalence comparison for corpus evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.corpus.kicadxml import canonicalize_circuit_ir


@dataclass(frozen=True)
class ElectricalMismatch:
    field: str
    expected: str
    actual: str


@dataclass(frozen=True)
class ElectricalEquivalenceReport:
    status: str
    mismatches: tuple[ElectricalMismatch, ...] = field(default_factory=tuple)


def compare_circuit_ir_equivalence(
    source: CircuitIR,
    generated: CircuitIR,
) -> ElectricalEquivalenceReport:
    """Return a hard pass/fail electrical equivalence report."""

    source_ir = canonicalize_circuit_ir(source)
    generated_ir = canonicalize_circuit_ir(generated)
    mismatches: list[ElectricalMismatch] = []

    source_components = {
        component.ref: component for component in source_ir.components
    }
    generated_components = {
        component.ref: component for component in generated_ir.components
    }
    if source_components.keys() != generated_components.keys():
        mismatches.append(
            ElectricalMismatch(
                field="component_refs",
                expected=",".join(sorted(source_components)),
                actual=",".join(sorted(generated_components)),
            )
        )

    for ref in sorted(source_components.keys() & generated_components.keys()):
        expected_component = source_components[ref]
        actual_component = generated_components[ref]
        if (expected_component.value or "") != (actual_component.value or ""):
            mismatches.append(
                ElectricalMismatch(
                    field=f"component_value:{ref}",
                    expected=expected_component.value or "",
                    actual=actual_component.value or "",
                )
            )
        if expected_component.symbol != actual_component.symbol:
            mismatches.append(
                ElectricalMismatch(
                    field=f"component_symbol:{ref}",
                    expected=expected_component.symbol,
                    actual=actual_component.symbol,
                )
            )

    source_nets = {
        net.name: tuple((pin.ref, pin.pin, pin.unit or "") for pin in net.pins)
        for net in source_ir.nets
    }
    generated_nets = {
        net.name: tuple((pin.ref, pin.pin, pin.unit or "") for pin in net.pins)
        for net in generated_ir.nets
    }
    if source_nets.keys() != generated_nets.keys():
        mismatches.append(
            ElectricalMismatch(
                field="net_names",
                expected=",".join(sorted(source_nets)),
                actual=",".join(sorted(generated_nets)),
            )
        )
    for name in sorted(source_nets.keys() & generated_nets.keys()):
        if source_nets[name] != generated_nets[name]:
            mismatches.append(
                ElectricalMismatch(
                    field=f"net_pins:{name}",
                    expected=str(list(source_nets[name])),
                    actual=str(list(generated_nets[name])),
                )
            )

    status = "passed" if not mismatches else "failed"
    return ElectricalEquivalenceReport(status=status, mismatches=tuple(mismatches))

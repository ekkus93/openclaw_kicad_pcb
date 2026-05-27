"""Electrical equivalence comparison for corpus evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field

from kicad_pcb.circuit_ir import CircuitIR, NetIR
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

    source_ir = canonicalize_circuit_ir(_normalize_sheet_scoped_net_names(source))
    generated_ir = canonicalize_circuit_ir(_normalize_sheet_scoped_net_names(generated))
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


def _normalize_sheet_scoped_net_names(ir: CircuitIR) -> CircuitIR:
    """Flatten safe KiCad sheet-path net prefixes like ``/Sheet/NET`` -> ``NET``."""

    candidate_names = {net.name: _sheet_scoped_tail(net.name) for net in ir.nets}
    candidate_pins: dict[str, set[tuple[tuple[str, str, str], ...]]] = {}
    for net in ir.nets:
        pin_key = tuple((pin.ref, pin.pin, pin.unit or "") for pin in net.pins)
        candidate_pins.setdefault(candidate_names[net.name], set()).add(pin_key)

    normalized_nets: list[NetIR] = []
    seen_names: dict[str, tuple[tuple[str, str, str], ...]] = {}
    for net in ir.nets:
        pin_key = tuple((pin.ref, pin.pin, pin.unit or "") for pin in net.pins)
        candidate_name = candidate_names[net.name]
        normalized_name = net.name
        if candidate_name != net.name and len(candidate_pins[candidate_name]) == 1:
            normalized_name = candidate_name
        existing_pin_key = seen_names.get(normalized_name)
        if existing_pin_key == pin_key:
            continue
        normalized_nets.append(NetIR(name=normalized_name, pins=net.pins))
        seen_names[normalized_name] = pin_key

    return CircuitIR(
        version=ir.version,
        components=ir.components,
        nets=normalized_nets,
        options=ir.options,
    )


def _sheet_scoped_tail(net_name: str) -> str:
    if not net_name.startswith("/"):
        return net_name
    tail = net_name.rsplit("/", 1)[-1].strip()
    return tail or net_name

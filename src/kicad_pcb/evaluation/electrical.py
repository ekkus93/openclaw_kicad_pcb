"""Electrical equivalence comparison for corpus evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR
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
    generated_ir = canonicalize_circuit_ir(
        _normalize_multi_unit_generated_refs(
            source_ir,
            _normalize_sheet_scoped_net_names(generated),
        )
    )
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


def _normalize_multi_unit_generated_refs(source: CircuitIR, generated: CircuitIR) -> CircuitIR:
    """Collapse generated split refs like ``U1A`` back to logical refs for comparison."""

    source_refs = {component.ref for component in source.components}
    source_units_by_ref: dict[str, set[str]] = {}
    for net in source.nets:
        for pin in net.pins:
            if pin.unit:
                source_units_by_ref.setdefault(pin.ref, set()).add(pin.unit)

    generated_units_by_ref: dict[str, set[str]] = {}
    for net in generated.nets:
        for pin in net.pins:
            if pin.unit:
                generated_units_by_ref.setdefault(pin.ref, set()).add(pin.unit)

    base_group_counts: dict[str, int] = {}
    for component in generated.components:
        base_ref = _logical_multi_unit_base_ref(component.ref, source_refs)
        if base_ref is not None:
            base_group_counts[base_ref] = base_group_counts.get(base_ref, 0) + 1

    alias_map = {
        component.ref: _canonical_generated_ref(
            component.ref,
            source_refs=source_refs,
            source_units_by_ref=source_units_by_ref,
            generated_units_by_ref=generated_units_by_ref,
            base_group_counts=base_group_counts,
        )
        for component in generated.components
    }

    components_by_ref: dict[str, ComponentIR] = {}
    for component in generated.components:
        canonical_ref = alias_map[component.ref]
        components_by_ref.setdefault(
            canonical_ref,
            component.model_copy(update={"ref": canonical_ref}, deep=True),
        )

    nets = [
        NetIR(
            name=net.name,
            pins=[
                pin.model_copy(update={"ref": alias_map.get(pin.ref, pin.ref)}, deep=True)
                for pin in net.pins
            ],
        )
        for net in generated.nets
    ]
    return CircuitIR(
        version=generated.version,
        components=list(components_by_ref.values()),
        nets=nets,
        options=generated.options,
    )


def _canonical_generated_ref(
    ref: str,
    *,
    source_refs: set[str],
    source_units_by_ref: dict[str, set[str]],
    generated_units_by_ref: dict[str, set[str]],
    base_group_counts: dict[str, int],
) -> str:
    if ref in source_refs:
        return ref
    base_ref = _logical_multi_unit_base_ref(ref, source_refs)
    if base_ref is None:
        return ref

    generated_units = generated_units_by_ref.get(ref, set())
    source_units = source_units_by_ref.get(base_ref, set())
    if generated_units and source_units and not generated_units <= source_units:
        return ref
    if generated_units or base_group_counts.get(base_ref, 0) > 1:
        return base_ref
    return ref


def _logical_multi_unit_base_ref(ref: str, source_refs: set[str]) -> str | None:
    if len(ref) < 2 or not ref[-1].isalpha():
        return None
    base_ref = ref[:-1]
    if base_ref in source_refs:
        return base_ref
    return None


def _sheet_scoped_tail(net_name: str) -> str:
    if not net_name.startswith("/"):
        return net_name
    tail = net_name.rsplit("/", 1)[-1].strip()
    return tail or net_name

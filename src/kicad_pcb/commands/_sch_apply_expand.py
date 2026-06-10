"""Schematic apply: multi-unit component expansion."""

from __future__ import annotations

from ..circuit_ir import CircuitIR
from ..component_types import is_power_net
from ..errors import ErrorCode, UserError
from ..symbol_index import SymbolIndex
from ._sch_apply_types import _PlacedSymbolSpec, _UnitSplitDebugEntry


def _sorted_unit_keys(unit_pins: dict[str, list[str]]) -> list[str]:
    return sorted(unit_pins, key=lambda key: (int(key), key))


def _symbol_unit_pins(symbol: str, symbol_index: SymbolIndex) -> dict[str, list[str]]:
    return {unit: list(pins) for unit, pins in symbol_index.get_unit_pins(symbol).items()}


def _pin_to_unit_map(symbol: str, unit_pins: dict[str, list[str]]) -> dict[str, str]:
    pin_to_unit: dict[str, str] = {}
    for unit, pins in unit_pins.items():
        for pin_num in pins:
            existing_unit = pin_to_unit.get(pin_num)
            if existing_unit is not None and existing_unit != unit:
                raise UserError(
                    "Multi-unit symbol pin belongs to multiple KiCad units",
                    code=ErrorCode.IR_SEMANTIC_INVALID,
                    details={
                        "symbol": symbol,
                        "pin": pin_num,
                        "units": sorted({existing_unit, unit}),
                    },
                )
            pin_to_unit[pin_num] = unit
    return pin_to_unit


def _component_unit_nets(
    ref: str,
    pin_to_unit: dict[str, str],
    nets,
    used_units: list[str],
) -> dict[str, set[str]]:
    unit_nets: dict[str, set[str]] = {unit: set() for unit in used_units}
    for net in nets:
        for pin_ref in net.pins:
            if pin_ref.ref == ref:
                unit_nets[pin_to_unit[pin_ref.pin]].add(net.name)
    return unit_nets


def _suffix_by_unit(used_units: list[str], unit_nets: dict[str, set[str]]) -> dict[str, str]:
    power_units = {
        unit
        for unit in used_units
        if unit_nets[unit] and all(is_power_net(net_name) for net_name in unit_nets[unit])
    }
    suffix_by_unit: dict[str, str] = {}
    next_letter = ord("A")
    for unit in used_units:
        if len(power_units) == 1 and unit in power_units:
            suffix_by_unit[unit] = "P"
            continue
        suffix_by_unit[unit] = chr(next_letter)
        next_letter += 1
    return suffix_by_unit


def _expand_generation_ir(
    ir: CircuitIR,
    symbol_index: SymbolIndex,
) -> tuple[CircuitIR, dict[str, _PlacedSymbolSpec]]:
    """Expand multi-unit devices into explicit placed refs for generation."""
    ref_to_used_pins: dict[str, set[str]] = {component.ref: set() for component in ir.components}
    for net in ir.nets:
        for pin_ref in net.pins:
            used_pins = ref_to_used_pins.get(pin_ref.ref)
            if used_pins is not None:
                used_pins.add(pin_ref.pin)

    multi_unit_pin_to_unit: dict[str, dict[str, str]] = {}
    placed_symbol_specs: dict[str, _PlacedSymbolSpec] = {}
    expanded_components = []
    ref_rewrite: dict[tuple[str, str], str] = {}

    for component in ir.components:
        all_symbol_pins = tuple(sorted(symbol_index.get_pins(component.symbol)))
        unit_pins = _symbol_unit_pins(component.symbol, symbol_index)
        if len(unit_pins) <= 1:
            expanded_components.append(component.model_copy(deep=True))
            placed_symbol_specs[component.ref] = _PlacedSymbolSpec(
                unit=1,
                pin_nums=all_symbol_pins,
                logical_ref=component.ref,
            )
            continue

        (
            component_copies,
            component_specs,
            pin_to_unit,
            component_ref_rewrite,
        ) = _expand_multi_unit_component(
            component=component,
            used_pins=ref_to_used_pins[component.ref],
            unit_pins=unit_pins,
            nets=ir.nets,
            fallback_pins=all_symbol_pins,
        )
        expanded_components.extend(component_copies)
        placed_symbol_specs.update(component_specs)
        if pin_to_unit:
            multi_unit_pin_to_unit[component.ref] = pin_to_unit
            ref_rewrite.update(component_ref_rewrite)

    expanded_nets = []
    for net in ir.nets:
        expanded_pins = []
        for pin_ref in net.pins:
            placed_pin_to_unit = multi_unit_pin_to_unit.get(pin_ref.ref)
            if placed_pin_to_unit is None:
                expanded_pins.append(pin_ref.model_copy(deep=True))
                continue
            unit = placed_pin_to_unit[pin_ref.pin]
            expanded_ref = ref_rewrite[(pin_ref.ref, unit)]
            expanded_pins.append(pin_ref.model_copy(update={"ref": expanded_ref}))
        expanded_nets.append(net.model_copy(update={"pins": expanded_pins}, deep=True))

    return ir.model_copy(
        update={"components": expanded_components, "nets": expanded_nets},
        deep=True,
    ), placed_symbol_specs


def _expand_multi_unit_component(
    *,
    component,
    used_pins: set[str],
    unit_pins: dict[str, list[str]],
    nets,
    fallback_pins: tuple[str, ...],
) -> tuple[list, dict[str, _PlacedSymbolSpec], dict[str, str], dict[tuple[str, str], str]]:
    pin_to_unit = _pin_to_unit_map(component.symbol, unit_pins)
    if not used_pins:
        return (
            [component.model_copy(deep=True)],
            {
                component.ref: _PlacedSymbolSpec(
                    unit=1,
                    pin_nums=fallback_pins,
                    logical_ref=component.ref,
                )
            },
            {},
            {},
        )

    missing_pins = sorted(pin_num for pin_num in used_pins if pin_num not in pin_to_unit)
    if missing_pins:
        raise UserError(
            "Multi-unit symbol pins could not be assigned to KiCad units",
            code=ErrorCode.IR_SEMANTIC_INVALID,
            details={
                "ref": component.ref,
                "symbol": component.symbol,
                "missing_pins": missing_pins,
                "known_unit_pins": {unit: sorted(pins) for unit, pins in unit_pins.items()},
            },
        )

    used_units = _sorted_unit_keys(
        {unit: unit_pins[unit] for unit in {pin_to_unit[pin_num] for pin_num in used_pins}}
    )
    unit_nets = _component_unit_nets(component.ref, pin_to_unit, nets, used_units)
    suffix_by_unit = _suffix_by_unit(used_units, unit_nets)

    component_copies = []
    component_specs: dict[str, _PlacedSymbolSpec] = {}
    ref_rewrite: dict[tuple[str, str], str] = {}
    for unit in used_units:
        expanded_ref = f"{component.ref}{suffix_by_unit[unit]}"
        component_copies.append(component.model_copy(update={"ref": expanded_ref}, deep=True))
        component_specs[expanded_ref] = _PlacedSymbolSpec(
            unit=int(unit),
            pin_nums=tuple(sorted(unit_pins[unit])),
            logical_ref=component.ref,
        )
        ref_rewrite[(component.ref, unit)] = expanded_ref
    return component_copies, component_specs, pin_to_unit, ref_rewrite


def _build_unit_splitting_debug(
    *,
    source_ir: CircuitIR,
    generation_ir: CircuitIR,
    placed_symbol_specs: dict[str, _PlacedSymbolSpec],
) -> dict[str, object]:
    """Summarize how logical device refs expanded into placed KiCad units."""
    source_refs = {component.ref for component in source_ir.components}
    grouped: dict[str, list[_UnitSplitDebugEntry]] = {}
    for component in generation_ir.components:
        placed_ref = component.ref
        source_ref = placed_ref
        if (
            placed_ref not in source_refs
            and placed_ref[:-1] in source_refs
            and placed_ref[-1].isalpha()
        ):
            source_ref = placed_ref[:-1]
        spec = placed_symbol_specs[placed_ref]
        grouped.setdefault(source_ref, []).append(
            {
                "placed_ref": placed_ref,
                "pin_nums": list(spec.pin_nums),
                "symbol": component.symbol,
                "unit": spec.unit,
            }
        )

    expanded_devices = []
    for source_ref in sorted(grouped):
        units = grouped[source_ref]
        if len(units) <= 1 and units[0]["placed_ref"] == source_ref:
            continue
        expanded_devices.append(
            {
                "source_ref": source_ref,
                "placed_refs": [entry["placed_ref"] for entry in units],
                "units": list(units),
            }
        )

    return {
        "original_component_count": len(source_ir.components),
        "placed_component_count": len(generation_ir.components),
        "expanded_device_count": len(expanded_devices),
        "expanded_devices": expanded_devices,
    }

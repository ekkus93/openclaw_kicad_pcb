"""Generic connectivity and audio-connector advisory lint checks."""

from __future__ import annotations

from ..circuit_ir import CircuitIR, ComponentIR
from ..errors import UserError
from ..symbol_index import SymbolIndex
from ._validate_helpers import (
    _collect_two_pin_component_bridges,
    _expected_footprint_category,
    _footprint_looks_placeholder_or_symbol_id,
    _footprint_matches_category,
    _looks_connector,
    _looks_input_net,
    _looks_output_net,
)


def _is_supported_mono_trs_usage(
    component: ComponentIR,
    used_pins: set[str],
    unused_pins: list[str],
) -> bool:
    if component.symbol != "Connector:AudioJack3":
        return False
    if len(unused_pins) != 1:
        return False
    supported_unused = {"R": {"T", "S"}, "T": {"R", "S"}}
    return used_pins == supported_unused.get(unused_pins[0], set())


def _footprint_quality_warnings(ir: CircuitIR) -> list[dict[str, object]]:
    warnings: list[dict[str, object]] = []

    for component in ir.components:
        footprint = component.footprint
        if footprint is None or not footprint.strip():
            continue

        expected_category = _expected_footprint_category(component)
        if _footprint_looks_placeholder_or_symbol_id(component):
            warnings.append(
                {
                    "code": "FOOTPRINT_LOOKS_PLACEHOLDER_OR_SYMBOL_ID",
                    "message": (
                        f"Component {component.ref} uses footprint {footprint}, which looks like "
                        "a symbol id or placeholder footprint rather than a concrete package."
                    ),
                    "details": {
                        "ref": component.ref,
                        "symbol": component.symbol,
                        "footprint": footprint,
                        "expected_category": expected_category,
                    },
                }
            )
            continue

        if expected_category is None or _footprint_matches_category(footprint, expected_category):
            continue

        warnings.append(
            {
                "code": "FOOTPRINT_CLASS_MISMATCH",
                "message": (
                    f"Component {component.ref} uses footprint {footprint}, which does not look "
                    f"compatible with its {expected_category.replace('_', ' ')} symbol class."
                ),
                "details": {
                    "ref": component.ref,
                    "symbol": component.symbol,
                    "footprint": footprint,
                    "expected_category": expected_category,
                },
            }
        )

    return warnings


def _generic_connectivity_warnings(
    ir: CircuitIR,
    _symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
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

    warnings.extend(_footprint_quality_warnings(ir))

    return warnings


def _ambiguous_connector_usage_warnings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    if symbol_index is None:
        return []

    pins_in_nets: dict[str, set[str]] = {}
    for net in ir.nets:
        for pin_ref in net.pins:
            pins_in_nets.setdefault(pin_ref.ref, set()).add(pin_ref.pin)

    warnings: list[dict[str, object]] = []
    for component in ir.components:
        if not _looks_connector(component.ref, component.symbol):
            continue

        try:
            valid_pins = symbol_index.get_pins(component.symbol)
        except UserError:
            continue

        used_pins = pins_in_nets.get(component.ref, set())
        if not used_pins:
            continue

        unused_pins = sorted(valid_pins - used_pins)
        if not unused_pins:
            continue
        if len(valid_pins) <= 1:
            continue
        if _is_supported_mono_trs_usage(component, used_pins, unused_pins):
            continue

        warnings.append(
            {
                "code": "CONNECTOR_UNUSED_PINS_AMBIGUOUS",
                "message": (
                    f"Connector {component.ref} leaves symbol pin(s) {', '.join(unused_pins)} "
                    "unconnected without explicit no-connect handling."
                ),
                "details": {
                    "ref": component.ref,
                    "symbol": component.symbol,
                    "used_pins": sorted(used_pins),
                    "unused_pins": unused_pins,
                },
            }
        )

    return warnings


def _incomplete_stereo_trs_warnings(ir: CircuitIR) -> list[dict[str, object]]:
    warnings: list[dict[str, object]] = []

    for component in ir.components:
        if component.symbol != "Connector:AudioJack3":
            continue

        used_nets_by_pin: dict[str, str] = {}
        for net in ir.nets:
            for pin_ref in net.pins:
                if pin_ref.ref == component.ref and pin_ref.pin in {"T", "R"}:
                    used_nets_by_pin[pin_ref.pin] = net.name

        if set(used_nets_by_pin) != {"T", "R"}:
            continue

        leftish = any(token in used_nets_by_pin["T"].upper() for token in ("LEFT", "_L", "L_"))
        rightish = any(token in used_nets_by_pin["R"].upper() for token in ("RIGHT", "_R", "R_"))
        if leftish and rightish:
            continue

        warnings.append(
            {
                "code": "TRS_STEREO_IMPLEMENTATION_INCOMPLETE",
                "message": (
                    f"TRS connector {component.ref} uses both tip and ring, but the connected "
                    "nets do not read as a complete left/right stereo pair."
                ),
                "details": {
                    "ref": component.ref,
                    "symbol": component.symbol,
                    "value": component.value,
                    "tip_net": used_nets_by_pin["T"],
                    "ring_net": used_nets_by_pin["R"],
                    "used_signal_pins": sorted(used_nets_by_pin),
                },
            }
        )

    return warnings


def _audio_connector_warnings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    warnings = _ambiguous_connector_usage_warnings(ir, symbol_index)
    warnings.extend(_incomplete_stereo_trs_warnings(ir))
    return warnings


def _input_coupling_bypass_warnings(ir: CircuitIR) -> list[dict[str, object]]:
    warnings: list[dict[str, object]] = []

    for net_pair, bridges in sorted(_collect_two_pin_component_bridges(ir).items()):
        if not any(_looks_input_net(net_name) for net_name in net_pair):
            continue

        capacitor_refs = sorted(item["ref"] for item in bridges if item["kind"] == "capacitor")
        resistor_refs = sorted(item["ref"] for item in bridges if item["kind"] == "resistor")
        if not capacitor_refs or not resistor_refs:
            continue

        warnings.append(
            {
                "code": "INPUT_COUPLING_BYPASSED_BY_RESISTOR",
                "message": (
                    "Input-path coupling capacitor appears to be paralleled by resistor(s) "
                    f"between {net_pair[0]} and {net_pair[1]}."
                ),
                "details": {
                    "nets": list(net_pair),
                    "capacitor_refs": capacitor_refs,
                    "resistor_refs": resistor_refs,
                    "bridge_component_refs": sorted(capacitor_refs + resistor_refs),
                },
            }
        )

    return warnings


def _output_coupling_bypass_warnings(ir: CircuitIR) -> list[dict[str, object]]:
    warnings: list[dict[str, object]] = []

    for net_pair, bridges in sorted(_collect_two_pin_component_bridges(ir).items()):
        if not any(_looks_output_net(net_name) for net_name in net_pair):
            continue

        capacitor_refs = sorted(item["ref"] for item in bridges if item["kind"] == "capacitor")
        resistor_refs = sorted(item["ref"] for item in bridges if item["kind"] == "resistor")
        if not capacitor_refs or not resistor_refs:
            continue

        warnings.append(
            {
                "code": "OUTPUT_COUPLING_BYPASSED_BY_RESISTOR",
                "message": (
                    "Output-path coupling capacitor appears to be paralleled by resistor(s) "
                    f"between {net_pair[0]} and {net_pair[1]}."
                ),
                "details": {
                    "nets": list(net_pair),
                    "capacitor_refs": capacitor_refs,
                    "resistor_refs": resistor_refs,
                    "bridge_component_refs": sorted(capacitor_refs + resistor_refs),
                },
            }
        )

    return warnings

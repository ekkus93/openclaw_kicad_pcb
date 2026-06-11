"""Op-amp helpers and feedback/output-sanity warnings."""

from __future__ import annotations

from ..circuit_ir import CircuitIR, ComponentIR
from ..component_types import power_rail_polarity
from ..symbol_index import SymbolIndex
from ._validate_helpers import (
    _PIN_ROLE_INVERTING_INPUT,
    _PIN_ROLE_OUTPUT,
    _collect_pin_to_net,
    _collect_two_pin_component_bridges,
    _component_pin_roles,
    _looks_output_net,
    _looks_power_like_net,
    _sorted_net_pair,
)


def _connector_output_intent(net_name: str, component: ComponentIR) -> bool:
    value_text = (component.value or "").lower()
    return _looks_output_net(net_name) or "out" in value_text or "headphone" in value_text


def _looks_speaker_output(net_name: str, component: ComponentIR) -> bool:
    name = net_name.upper()
    value_text = (component.value or "").lower()
    return "SPK" in name or "SPEAKER" in name or "speaker" in value_text


def _component_has_split_supply(
    component: ComponentIR,
    component_nets: dict[str, set[str]],
) -> bool:
    nets = component_nets.get(component.ref, set())
    has_positive = any(power_rail_polarity(net_name) == "positive" for net_name in nets)
    has_negative = any(power_rail_polarity(net_name) == "negative" for net_name in nets)
    return has_positive and has_negative


def _opamp_feedback_warnings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    if symbol_index is None:
        return []

    pin_to_net = _collect_pin_to_net(ir)
    bridges = _collect_two_pin_component_bridges(ir)
    warnings: list[dict[str, object]] = []

    for component in ir.components:
        pin_roles = _component_pin_roles(component.symbol, symbol_index)
        if not pin_roles:
            continue

        output_nets = sorted(
            {
                pin_to_net[(component.ref, pin_number)]
                for pin_number, role in pin_roles.items()
                if role == _PIN_ROLE_OUTPUT and (component.ref, pin_number) in pin_to_net
            }
        )
        if not output_nets:
            continue

        for pin_number, role in sorted(pin_roles.items()):
            if role != _PIN_ROLE_INVERTING_INPUT:
                continue

            inverting_net = pin_to_net.get((component.ref, pin_number))
            if inverting_net is None or _looks_power_like_net(inverting_net):
                continue
            if inverting_net in output_nets:
                continue

            feedback_refs: list[str] = []
            for output_net in output_nets:
                bridge_pair = _sorted_net_pair(inverting_net, output_net)
                for bridge in bridges.get(bridge_pair, []):
                    if bridge["kind"] in {"resistor", "capacitor"}:
                        feedback_refs.append(bridge["ref"])

            if feedback_refs:
                continue

            warnings.append(
                {
                    "code": "OPAMP_FEEDBACK_MISSING_OR_NONLOCAL",
                    "message": (
                        f"Op-amp {component.ref} inverting input pin {pin_number} on "
                        f"{inverting_net} does not appear to have a local feedback path "
                        "from any output net."
                    ),
                    "details": {
                        "ref": component.ref,
                        "symbol": component.symbol,
                        "inverting_input_pin": pin_number,
                        "inverting_input_net": inverting_net,
                        "output_nets": output_nets,
                    },
                }
            )

    return warnings


def _opamp_output_sanity_warnings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    if symbol_index is None:
        return []

    pin_to_net = _collect_pin_to_net(ir)
    nets_by_name = {net.name: net for net in ir.nets}
    warnings: list[dict[str, object]] = []

    for component in ir.components:
        pin_roles = _component_pin_roles(component.symbol, symbol_index)
        if not pin_roles:
            continue

        for pin_number, role in sorted(pin_roles.items()):
            if role != _PIN_ROLE_OUTPUT:
                continue

            output_net = pin_to_net.get((component.ref, pin_number))
            if output_net is None:
                warnings.append(
                    {
                        "code": "OPAMP_OUTPUT_FLOATING",
                        "message": (
                            f"Op-amp {component.ref} output pin {pin_number} is not connected "
                            "to any net."
                        ),
                        "details": {
                            "ref": component.ref,
                            "symbol": component.symbol,
                            "output_pin": pin_number,
                        },
                    }
                )
                continue

            if _looks_power_like_net(output_net):
                warnings.append(
                    {
                        "code": "OPAMP_OUTPUT_SHORTED_TO_RAIL",
                        "message": (
                            f"Op-amp {component.ref} output pin {pin_number} is connected "
                            f"directly to rail-like net {output_net}."
                        ),
                        "details": {
                            "ref": component.ref,
                            "symbol": component.symbol,
                            "output_pin": pin_number,
                            "output_net": output_net,
                        },
                    }
                )
                continue

            external_connections = sorted(
                {
                    f"{pin.ref}:{pin.pin}"
                    for pin in nets_by_name[output_net].pins
                    if pin.ref != component.ref
                }
            )
            if external_connections:
                continue

            warnings.append(
                {
                    "code": "OPAMP_OUTPUT_FLOATING",
                    "message": (
                        f"Op-amp {component.ref} output pin {pin_number} only connects on "
                        f"{output_net} to pins on the same package."
                    ),
                    "details": {
                        "ref": component.ref,
                        "symbol": component.symbol,
                        "output_pin": pin_number,
                        "output_net": output_net,
                    },
                }
            )

    return warnings


__all__ = [
    "_component_has_split_supply",
    "_connector_output_intent",
    "_looks_speaker_output",
    "_opamp_feedback_warnings",
    "_opamp_output_sanity_warnings",
]

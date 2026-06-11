"""Op-amp split-rail interstage coupling and stage topology warnings."""

from __future__ import annotations

from ..circuit_ir import CircuitIR
from ..symbol_index import SymbolIndex
from ._validate_helpers import (
    _PIN_ROLE_INVERTING_INPUT,
    _PIN_ROLE_NONINVERTING_INPUT,
    _PIN_ROLE_OUTPUT,
    _collect_component_nets,
    _collect_pin_to_net,
    _collect_two_pin_component_bridges,
    _component_pin_roles,
    _looks_power_like_net,
)
from ._validate_opamp_feedback import _component_has_split_supply


def _split_rail_interstage_coupling_warnings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    if symbol_index is None:
        return []

    pin_to_net = _collect_pin_to_net(ir)
    bridges = _collect_two_pin_component_bridges(ir)
    component_nets = _collect_component_nets(ir)
    warnings: list[dict[str, object]] = []
    warned_paths: set[tuple[str, str, str]] = set()

    input_targets_by_net: dict[str, list[tuple[str, str]]] = {}
    for component in ir.components:
        pin_roles = _component_pin_roles(component.symbol, symbol_index)
        if not pin_roles:
            continue
        for pin_number, role in pin_roles.items():
            if role not in {_PIN_ROLE_INVERTING_INPUT, _PIN_ROLE_NONINVERTING_INPUT}:
                continue
            net_name = pin_to_net.get((component.ref, pin_number))
            if net_name is None or _looks_power_like_net(net_name):
                continue
            input_targets_by_net.setdefault(net_name, []).append((component.ref, pin_number))

    for component in ir.components:
        pin_roles = _component_pin_roles(component.symbol, symbol_index)
        if not pin_roles or not _component_has_split_supply(component, component_nets):
            continue

        for pin_number, role in sorted(pin_roles.items()):
            if role != _PIN_ROLE_OUTPUT:
                continue

            output_net = pin_to_net.get((component.ref, pin_number))
            if output_net is None or _looks_power_like_net(output_net):
                continue

            for net_pair, bridge_members in sorted(bridges.items()):
                if output_net not in net_pair:
                    continue

                capacitor_refs = sorted(
                    item["ref"] for item in bridge_members if item["kind"] == "capacitor"
                )
                if not capacitor_refs:
                    continue

                coupled_net = net_pair[1] if net_pair[0] == output_net else net_pair[0]
                target_inputs = input_targets_by_net.get(coupled_net, [])
                if not target_inputs:
                    continue

                warning_key = (component.ref, output_net, coupled_net)
                if warning_key in warned_paths:
                    continue
                warned_paths.add(warning_key)

                warnings.append(
                    {
                        "code": "SPLIT_RAIL_INTERSTAGE_AC_COUPLING_PRESENT",
                        "message": (
                            f"Split-rail op-amp stage {component.ref} AC-couples {output_net} "
                            f"into downstream stage net {coupled_net}. This is a design choice, "
                            "not a split-rail requirement."
                        ),
                        "details": {
                            "ref": component.ref,
                            "symbol": component.symbol,
                            "output_pin": pin_number,
                            "output_net": output_net,
                            "coupled_net": coupled_net,
                            "capacitor_refs": capacitor_refs,
                            "target_inputs": [
                                f"{ref}:{input_pin}" for ref, input_pin in target_inputs
                            ],
                        },
                    }
                )

    return warnings


def _opamp_stage_topology_warnings(
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
        noninverting_signal_nets = sorted(
            {
                pin_to_net[(component.ref, pin_number)]
                for pin_number, role in pin_roles.items()
                if role == _PIN_ROLE_NONINVERTING_INPUT
                and (component.ref, pin_number) in pin_to_net
                and not _looks_power_like_net(pin_to_net[(component.ref, pin_number)])
                and pin_to_net[(component.ref, pin_number)] not in output_nets
            }
        )
        if not output_nets or not noninverting_signal_nets:
            continue

        for pin_number, role in sorted(pin_roles.items()):
            if role != _PIN_ROLE_INVERTING_INPUT:
                continue

            inverting_net = pin_to_net.get((component.ref, pin_number))
            if inverting_net is None or _looks_power_like_net(inverting_net):
                continue
            if inverting_net in output_nets:
                continue

            feedback_resistor_refs: set[str] = set()
            shunt_resistor_refs: set[str] = set()
            other_stage_resistor_refs: set[str] = set()
            for net_pair, bridge_members in bridges.items():
                if inverting_net not in net_pair:
                    continue

                other_net = net_pair[1] if net_pair[0] == inverting_net else net_pair[0]
                resistor_refs = {
                    item["ref"] for item in bridge_members if item["kind"] == "resistor"
                }
                if not resistor_refs:
                    continue

                if other_net in output_nets:
                    feedback_resistor_refs.update(resistor_refs)
                    continue
                if _looks_power_like_net(other_net):
                    shunt_resistor_refs.update(resistor_refs)
                    continue
                other_stage_resistor_refs.update(resistor_refs)

            if not feedback_resistor_refs or shunt_resistor_refs or other_stage_resistor_refs:
                continue

            warnings.append(
                {
                    "code": "OPAMP_STAGE_TOPOLOGY_LIKELY_MISTAKEN",
                    "message": (
                        f"Op-amp {component.ref} looks like a non-inverting stage because its "
                        "non-inverting input carries signal net(s) "
                        f"{', '.join(noninverting_signal_nets)}, but inverting input pin "
                        f"{pin_number} on {inverting_net} has feedback resistor(s) "
                        f"{', '.join(sorted(feedback_resistor_refs))} and no "
                        "resistor-defined shunt/reference path."
                    ),
                    "details": {
                        "ref": component.ref,
                        "symbol": component.symbol,
                        "inverting_input_pin": pin_number,
                        "inverting_input_net": inverting_net,
                        "noninverting_signal_nets": noninverting_signal_nets,
                        "output_nets": output_nets,
                        "feedback_resistor_refs": sorted(feedback_resistor_refs),
                    },
                }
            )

    return warnings

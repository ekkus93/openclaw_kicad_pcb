"""Op-amp advisory lint checks (feedback, output, AC-coupling, headphone, speaker)."""

from __future__ import annotations

from ..circuit_ir import CircuitIR, ComponentIR
from ..component_types import power_rail_polarity
from ..symbol_index import SymbolIndex
from ._validate_connectivity import (
    _input_coupling_bypass_warnings,
    _output_coupling_bypass_warnings,
)
from ._validate_helpers import (
    _PIN_ROLE_INVERTING_INPUT,
    _PIN_ROLE_NONINVERTING_INPUT,
    _PIN_ROLE_OUTPUT,
    _collect_component_nets,
    _collect_pin_to_net,
    _collect_two_pin_component_bridges,
    _component_pin_roles,
    _is_audio_jack,
    _looks_connector,
    _looks_output_net,
    _looks_power_like_net,
    _parse_scalar_value,
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


def _opamp_ac_coupled_output_load_warnings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    if symbol_index is None:
        return []

    pin_to_net = _collect_pin_to_net(ir)
    bridges = _collect_two_pin_component_bridges(ir)
    component_map = {component.ref: component for component in ir.components}
    nets_by_name = {net.name: net for net in ir.nets}
    warnings: list[dict[str, object]] = []
    warned_paths: set[tuple[str, str, str]] = set()

    for component in ir.components:
        pin_roles = _component_pin_roles(component.symbol, symbol_index)
        if not pin_roles:
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
                if _looks_power_like_net(coupled_net):
                    continue
                if not _looks_output_net(coupled_net):
                    coupled_net_record = nets_by_name.get(coupled_net)
                    if coupled_net_record is None:
                        continue
                    coupled_components = [
                        component_map.get(pin.ref) for pin in coupled_net_record.pins
                    ]
                    if not any(
                        part is not None and _looks_connector(part.ref, part.symbol)
                        for part in coupled_components
                    ):
                        continue

                bleed_resistor_refs: set[str] = set()
                for other_pair, other_members in bridges.items():
                    if coupled_net not in other_pair:
                        continue
                    reference_net = other_pair[1] if other_pair[0] == coupled_net else other_pair[0]
                    if not _looks_power_like_net(reference_net):
                        continue
                    bleed_resistor_refs.update(
                        item["ref"] for item in other_members if item["kind"] == "resistor"
                    )

                if bleed_resistor_refs:
                    continue

                warning_key = (component.ref, output_net, coupled_net)
                if warning_key in warned_paths:
                    continue
                warned_paths.add(warning_key)

                warnings.append(
                    {
                        "code": "OUTPUT_CAP_NO_DEFINED_LOAD_OR_BLEED",
                        "message": (
                            f"Op-amp {component.ref} output pin {pin_number} is AC-coupled from "
                            f"{output_net} to {coupled_net} without a resistor-defined bleed or "
                            "load path on the output side."
                        ),
                        "details": {
                            "ref": component.ref,
                            "symbol": component.symbol,
                            "output_pin": pin_number,
                            "output_net": output_net,
                            "coupled_output_net": coupled_net,
                            "capacitor_refs": capacitor_refs,
                        },
                    }
                )

    return warnings


def _headphone_output_impedance_warnings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    if symbol_index is None:
        return []

    pin_to_net = _collect_pin_to_net(ir)
    bridges = _collect_two_pin_component_bridges(ir)
    component_map = {component.ref: component for component in ir.components}
    warnings: list[dict[str, object]] = []
    warned_paths: set[tuple[str, str, str]] = set()

    connector_output_nets = {
        net.name
        for net in ir.nets
        if any(
            (component := component_map.get(pin.ref)) is not None
            and _is_audio_jack(component)
            and _connector_output_intent(net.name, component)
            for pin in net.pins
        )
    }

    for component in ir.components:
        pin_roles = _component_pin_roles(component.symbol, symbol_index)
        if not pin_roles:
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

                resistor_refs = sorted(
                    item["ref"] for item in bridge_members if item["kind"] == "resistor"
                )
                if not resistor_refs:
                    continue

                downstream_net = net_pair[1] if net_pair[0] == output_net else net_pair[0]
                candidate_output_nets = {downstream_net}
                for other_pair, other_members in bridges.items():
                    if downstream_net not in other_pair:
                        continue
                    if not any(item["kind"] == "capacitor" for item in other_members):
                        continue
                    candidate_output_nets.add(
                        other_pair[1] if other_pair[0] == downstream_net else other_pair[0]
                    )

                if candidate_output_nets.isdisjoint(connector_output_nets):
                    continue

                resolved_values = [
                    _parse_scalar_value(component_map[ref].value)
                    for ref in resistor_refs
                    if ref in component_map
                ]
                numeric_values = [value for value in resolved_values if value is not None]
                if not numeric_values:
                    continue

                series_ohms = max(numeric_values)
                if series_ohms < 22.0:
                    continue

                warning_key = (component.ref, output_net, downstream_net)
                if warning_key in warned_paths:
                    continue
                warned_paths.add(warning_key)

                warnings.append(
                    {
                        "code": "HEADPHONE_OUTPUT_IMPEDANCE_HIGH",
                        "message": (
                            f"Op-amp {component.ref} drives headphone/output path {output_net} "
                            f"through {series_ohms:.0f} ohm series resistance, which is high "
                            "for many low-impedance headphone loads."
                        ),
                        "details": {
                            "ref": component.ref,
                            "symbol": component.symbol,
                            "output_pin": pin_number,
                            "output_net": output_net,
                            "downstream_net": downstream_net,
                            "connector_output_nets": sorted(
                                candidate_output_nets & connector_output_nets
                            ),
                            "resistor_refs": resistor_refs,
                            "series_ohms": series_ohms,
                        },
                    }
                )

    return warnings


def _speaker_driver_advisories(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    if symbol_index is None:
        return []

    pin_to_net = _collect_pin_to_net(ir)
    component_map = {component.ref: component for component in ir.components}
    warnings: list[dict[str, object]] = []

    speaker_nets = {
        net.name
        for net in ir.nets
        if any(
            (component := component_map.get(pin.ref)) is not None
            and _looks_connector(component.ref, component.symbol)
            and _looks_speaker_output(net.name, component)
            for pin in net.pins
        )
    }
    if not speaker_nets:
        return []

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
        for output_net in output_nets:
            if output_net not in speaker_nets:
                continue
            warnings.append(
                {
                    "code": "OPAMP_PRESENTED_AS_SPEAKER_POWER_STAGE",
                    "message": (
                        f"Op-amp {component.ref} appears to drive speaker-designated net "
                        f"{output_net}. Treat small-signal op-amps as preamp or light-load "
                        "drivers, not speaker power stages."
                    ),
                    "details": {
                        "ref": component.ref,
                        "symbol": component.symbol,
                        "output_net": output_net,
                    },
                }
            )

    return warnings


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

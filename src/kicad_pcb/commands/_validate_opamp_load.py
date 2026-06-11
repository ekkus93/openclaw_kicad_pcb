"""Op-amp AC-coupled output load, headphone impedance, and speaker driver warnings."""

from __future__ import annotations

from ..circuit_ir import CircuitIR
from ..symbol_index import SymbolIndex
from ._validate_helpers import (
    _PIN_ROLE_OUTPUT,
    _collect_pin_to_net,
    _collect_two_pin_component_bridges,
    _component_pin_roles,
    _is_audio_jack,
    _looks_connector,
    _looks_output_net,
    _looks_power_like_net,
    _parse_scalar_value,
)
from ._validate_opamp_feedback import _connector_output_intent, _looks_speaker_output


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

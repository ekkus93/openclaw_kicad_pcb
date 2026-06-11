"""555-timer gate/load, frequency, and PWM orchestration."""

from __future__ import annotations

from ..circuit_ir import CircuitIR, ComponentIR
from ._validate_helpers import (
    _collect_component_nets,
    _collect_pin_to_net,
    _collect_two_pin_component_bridges,
    _component_kind,
    _is_ground_net,
    _looks_timer_555,
    _net_pair_bridge_refs,
    _parse_scalar_value,
)
from ._validate_timer555_context import (
    _build_timer555_context,
    _looks_timer555_pwm_topology,
    _Timer555Context,
)
from ._validate_timer555_pin_ctrl import (
    _append_timer555_control_and_timing_warnings,
    _append_timer555_pin_role_warnings,
    _append_timer555_steering_warnings,
)


def _timer555_gate_to_oscillator_refs(
    context: _Timer555Context,
    gate_net: str,
    bridges: dict[tuple[str, str], list[dict[str, str]]],
) -> dict[str, list[str]]:
    oscillator_nets = {
        net_name
        for net_name in (
            context.timing_net,
            context.trigger_net,
            context.threshold_net,
            context.discharge_net,
            context.ctrl_net,
        )
        if net_name is not None and net_name not in {gate_net, context.ground_net}
    }
    offending: dict[str, list[str]] = {}
    for net_name in sorted(oscillator_nets):
        resistor_refs = _net_pair_bridge_refs(
            bridges,
            gate_net,
            net_name,
            kind="resistor",
        )
        if resistor_refs:
            offending[net_name] = resistor_refs
    return offending


def _append_timer555_gate_and_load_warnings(
    context: _Timer555Context,
    pin_to_net: dict[tuple[str, str], str],
    bridges: dict[tuple[str, str], list[dict[str, str]]],
    warnings: list[dict[str, object]],
) -> None:
    if context.mosfet is None:
        return

    gate_net = pin_to_net.get((context.mosfet.ref, "1"))
    source_net = pin_to_net.get((context.mosfet.ref, "2"))
    drain_net = pin_to_net.get((context.mosfet.ref, "3"))
    if context.output_net is not None and gate_net is not None:
        gate_resistor_refs = _net_pair_bridge_refs(
            bridges,
            context.output_net,
            gate_net,
            kind="resistor",
        )
        if not gate_resistor_refs:
            warnings.append(
                {
                    "code": "TIMER555_GATE_RESISTOR_MISSING",
                    "message": (
                        f"555 timer {context.timer.ref} output should drive the "
                        "MOSFET gate through a resistor."
                    ),
                    "details": {
                        "ref": context.timer.ref,
                        "output_net": context.output_net,
                        "gate_net": gate_net,
                        "mosfet_ref": context.mosfet.ref,
                    },
                }
            )

    if gate_net is not None and context.ground_net is not None:
        gate_pulldown_refs = _net_pair_bridge_refs(
            bridges,
            gate_net,
            context.ground_net,
            kind="resistor",
        )
        if not gate_pulldown_refs:
            warnings.append(
                {
                    "code": "TIMER555_GATE_PULLDOWN_MISSING",
                    "message": (
                        f"MOSFET {context.mosfet.ref} gate should have a pull-down resistor to GND."
                    ),
                    "details": {"mosfet_ref": context.mosfet.ref, "gate_net": gate_net},
                }
            )
        gate_to_oscillator_refs = _timer555_gate_to_oscillator_refs(context, gate_net, bridges)
        if gate_to_oscillator_refs:
            warnings.append(
                {
                    "code": "TIMER555_GATE_PULLDOWN_TOUCHES_TIMING_NODE",
                    "message": (
                        f"MOSFET {context.mosfet.ref} gate pull-down must not touch "
                        "555 timing or oscillator nets."
                    ),
                    "details": {
                        "mosfet_ref": context.mosfet.ref,
                        "gate_net": gate_net,
                        "oscillator_nets": sorted(gate_to_oscillator_refs),
                    },
                }
            )

    if context.connector is None:
        return

    positive_net = pin_to_net.get((context.connector.ref, "1"))
    negative_net = pin_to_net.get((context.connector.ref, "2"))
    if (
        positive_net != context.vcc_net
        or negative_net != drain_net
        or not _is_ground_net(source_net)
    ):
        warnings.append(
            {
                "code": "TIMER555_LOW_SIDE_LOAD_TOPOLOGY_INVALID",
                "message": (
                    "555 PWM load path should read supply -> connector pin 1, "
                    "connector pin 2 -> MOSFET drain, MOSFET source -> GND."
                ),
                "details": {
                    "connector_ref": context.connector.ref,
                    "positive_net": positive_net,
                    "negative_net": negative_net,
                    "drain_net": drain_net,
                    "source_net": source_net,
                    "vcc_net": context.vcc_net,
                },
            }
        )


def _timer555_frequency_capacitor_refs(
    context: _Timer555Context,
    bridges: dict[tuple[str, str], list[dict[str, str]]],
    component_map: dict[str, ComponentIR],
    component_nets: dict[str, set[str]],
) -> list[str]:
    if context.timing_net is not None:
        timing_refs = _net_pair_bridge_refs(
            bridges,
            context.timing_net,
            context.ground_net or "",
            kind="capacitor",
        )
        if timing_refs:
            return timing_refs

    fallback_refs = [
        component_ref
        for component_ref, nets in component_nets.items()
        if component_ref in component_map
        and _component_kind(component_ref, component_map[component_ref].symbol) == "capacitor"
        and (context.ground_net is not None and context.ground_net in nets)
        and (context.ctrl_net is None or context.ctrl_net not in nets)
    ]
    return sorted(
        fallback_refs,
        key=lambda ref: _parse_scalar_value(component_map[ref].value) or float("inf"),
    )


def _append_timer555_frequency_warning(
    context: _Timer555Context,
    bridges: dict[tuple[str, str], list[dict[str, str]]],
    component_map: dict[str, ComponentIR],
    component_nets: dict[str, set[str]],
    warnings: list[dict[str, object]],
) -> None:
    if context.pot is None or context.discharge_net is None:
        return

    timing_cap_refs = _timer555_frequency_capacitor_refs(
        context,
        bridges,
        component_map,
        component_nets,
    )
    timing_cap_value = next(
        (
            _parse_scalar_value(component_map[ref].value)
            for ref in timing_cap_refs
            if ref in component_map
        ),
        None,
    )
    pot_value = _parse_scalar_value(context.pot.value)
    fixed_resistor_total = sum(
        _parse_scalar_value(component_map[ref].value) or 0.0
        for ref in _net_pair_bridge_refs(
            bridges,
            context.vcc_net or "",
            context.discharge_net,
            kind="resistor",
        )
        if ref in component_map
    )
    if not (timing_cap_value and pot_value and fixed_resistor_total):
        return

    estimated_frequency_hz = 1.44 / ((fixed_resistor_total + pot_value) * timing_cap_value)
    if estimated_frequency_hz < 500.0 or estimated_frequency_hz > 2000.0:
        warnings.append(
            {
                "code": "TIMER555_PWM_FREQUENCY_OUT_OF_RANGE",
                "message": (
                    f"555 PWM timing values estimate {estimated_frequency_hz:.1f} Hz, "
                    "outside the 500 Hz to 2 kHz target range."
                ),
                "details": {
                    "ref": context.timer.ref,
                    "estimated_frequency_hz": round(estimated_frequency_hz, 2),
                    "timing_capacitor_refs": timing_cap_refs,
                    "fixed_resistance_ohms": round(fixed_resistor_total, 2),
                    "pot_resistance_ohms": round(pot_value, 2),
                },
            }
        )


def _timer555_pwm_warnings(ir: CircuitIR) -> list[dict[str, object]]:
    pin_to_net = _collect_pin_to_net(ir)
    component_nets = _collect_component_nets(ir)
    bridges = _collect_two_pin_component_bridges(ir)
    component_map = {component.ref: component for component in ir.components}
    warnings: list[dict[str, object]] = []

    for timer in ir.components:
        if not _looks_timer_555(timer.ref, timer.symbol, timer.value):
            continue

        context = _build_timer555_context(timer, ir, pin_to_net)
        _append_timer555_pin_role_warnings(context, warnings)
        _append_timer555_control_and_timing_warnings(
            context,
            bridges,
            component_map,
            component_nets,
            warnings,
        )
        if _looks_timer555_pwm_topology(context):
            _append_timer555_steering_warnings(context, ir, pin_to_net, warnings)
            _append_timer555_gate_and_load_warnings(context, pin_to_net, bridges, warnings)
        _append_timer555_frequency_warning(
            context,
            bridges,
            component_map,
            component_nets,
            warnings,
        )

    return warnings

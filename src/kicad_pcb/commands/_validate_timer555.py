"""Advisory lint checks for 555-timer PWM circuits."""

from __future__ import annotations

from dataclasses import dataclass

from ..circuit_ir import CircuitIR, ComponentIR
from ..symbol_index import SymbolIndex
from ._validate_helpers import (
    _collect_component_nets,
    _collect_pin_to_net,
    _collect_two_pin_component_bridges,
    _component_kind,
    _is_ground_net,
    _is_positive_supply_net,
    _looks_nmos,
    _looks_timer_555,
    _net_pair_bridge_refs,
    _parse_scalar_value,
    _symbol_tail,
)

# ---------------------------------------------------------------------------
# Timer 555 context dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Timer555Context:
    timer: ComponentIR
    ground_net: str | None
    trigger_net: str | None
    output_net: str | None
    reset_net: str | None
    ctrl_net: str | None
    threshold_net: str | None
    discharge_net: str | None
    vcc_net: str | None
    timing_net: str | None
    pot: ComponentIR | None
    mosfet: ComponentIR | None
    connector: ComponentIR | None


# ---------------------------------------------------------------------------
# Hard-fail codes (severity overrides applied in _validate.py rules table)
# ---------------------------------------------------------------------------

_TIMER555_HARD_FAIL_CODES = frozenset(
    {
        "TIMER555_GROUND_PIN_INVALID",
        "TIMER555_VCC_PIN_INVALID",
        "TIMER555_RESET_NOT_TIED_HIGH",
        "TIMER555_TIMING_NODE_SPLIT",
        "TIMER555_TIMING_CAP_NOT_TO_GROUND",
        "TIMER555_TIMING_CAP_ACROSS_SUPPLY",
        "TIMER555_CTRL_CAP_MISSING_TO_GROUND",
        "TIMER555_CTRL_CAP_WRONG_TARGET",
        "TIMER555_STEERING_NETWORK_INVALID",
        "TIMER555_GATE_RESISTOR_MISSING",
        "TIMER555_GATE_PULLDOWN_MISSING",
        "TIMER555_GATE_PULLDOWN_TOUCHES_TIMING_NODE",
        "TIMER555_LOW_SIDE_LOAD_TOPOLOGY_INVALID",
    }
)

# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------


def _looks_timer555_pwm_topology(context: _Timer555Context) -> bool:
    return any(
        component is not None for component in (context.pot, context.mosfet, context.connector)
    )


def _build_timer555_context(
    timer: ComponentIR,
    ir: CircuitIR,
    pin_to_net: dict[tuple[str, str], str],
) -> _Timer555Context:
    ground_net = pin_to_net.get((timer.ref, "1"))
    trigger_net = pin_to_net.get((timer.ref, "2"))
    threshold_net = pin_to_net.get((timer.ref, "6"))
    timing_net = trigger_net if trigger_net == threshold_net else None
    if timing_net is not None and not _is_ground_net(ground_net):
        timing_net = None

    return _Timer555Context(
        timer=timer,
        ground_net=ground_net,
        trigger_net=trigger_net,
        output_net=pin_to_net.get((timer.ref, "3")),
        reset_net=pin_to_net.get((timer.ref, "4")),
        ctrl_net=pin_to_net.get((timer.ref, "5")),
        threshold_net=threshold_net,
        discharge_net=pin_to_net.get((timer.ref, "7")),
        vcc_net=pin_to_net.get((timer.ref, "8")),
        timing_net=timing_net,
        pot=next(
            (
                component
                for component in ir.components
                if _symbol_tail(component.symbol) == "r_potentiometer"
            ),
            None,
        ),
        mosfet=next(
            (
                component
                for component in ir.components
                if _looks_nmos(component.ref, component.symbol)
            ),
            None,
        ),
        connector=next(
            (
                component
                for component in ir.components
                if _symbol_tail(component.symbol) == "conn_01x02"
            ),
            None,
        ),
    )


# ---------------------------------------------------------------------------
# Pin-role warnings
# ---------------------------------------------------------------------------


def _append_timer555_pin_role_warnings(
    context: _Timer555Context,
    warnings: list[dict[str, object]],
) -> None:
    if not _is_ground_net(context.ground_net):
        warnings.append(
            {
                "code": "TIMER555_GROUND_PIN_INVALID",
                "message": f"555 timer {context.timer.ref} pin 1 must connect to GND.",
                "details": {"ref": context.timer.ref, "pin": "1", "net": context.ground_net},
            }
        )

    if not _is_positive_supply_net(context.vcc_net):
        warnings.append(
            {
                "code": "TIMER555_VCC_PIN_INVALID",
                "message": (
                    f"555 timer {context.timer.ref} pin 8 must connect to a positive supply."
                ),
                "details": {"ref": context.timer.ref, "pin": "8", "net": context.vcc_net},
            }
        )

    if context.reset_net != context.vcc_net:
        warnings.append(
            {
                "code": "TIMER555_RESET_NOT_TIED_HIGH",
                "message": (
                    f"555 timer {context.timer.ref} pin 4 should tie to the same supply as pin 8."
                ),
                "details": {
                    "ref": context.timer.ref,
                    "reset_net": context.reset_net,
                    "vcc_net": context.vcc_net,
                },
            }
        )

    if context.timing_net is None:
        warnings.append(
            {
                "code": "TIMER555_TIMING_NODE_SPLIT",
                "message": (
                    f"555 timer {context.timer.ref} pins 2 and 6 must share a single timing node."
                ),
                "details": {
                    "ref": context.timer.ref,
                    "trigger_net": context.trigger_net,
                    "threshold_net": context.threshold_net,
                },
            }
        )


# ---------------------------------------------------------------------------
# Control / timing warnings
# ---------------------------------------------------------------------------


def _timer555_non_ground_cap_targets(
    ctrl_net: str,
    component_map: dict[str, ComponentIR],
    component_nets: dict[str, set[str]],
) -> list[dict[str, object]]:
    non_ground_targets: list[dict[str, object]] = []
    for component_ref, nets in component_nets.items():
        if ctrl_net not in nets:
            continue
        component = component_map.get(component_ref)
        if component is None or _component_kind(component.ref, component.symbol) != "capacitor":
            continue
        other_nets = sorted(
            net_name for net_name in nets if net_name != ctrl_net and not _is_ground_net(net_name)
        )
        if not other_nets:
            continue
        non_ground_targets.append({"component_ref": component_ref, "other_nets": other_nets})
    return non_ground_targets


def _append_timer555_control_and_timing_warnings(
    context: _Timer555Context,
    bridges: dict[tuple[str, str], list[dict[str, str]]],
    component_map: dict[str, ComponentIR],
    component_nets: dict[str, set[str]],
    warnings: list[dict[str, object]],
) -> None:
    if context.ctrl_net is not None:
        ctrl_ground_caps = _net_pair_bridge_refs(
            bridges,
            context.ctrl_net,
            context.ground_net or "",
            kind="capacitor",
        )
        ctrl_non_ground_caps = _timer555_non_ground_cap_targets(
            context.ctrl_net,
            component_map,
            component_nets,
        )
        if not ctrl_ground_caps:
            warnings.append(
                {
                    "code": "TIMER555_CTRL_CAP_MISSING_TO_GROUND",
                    "message": (
                        f"555 timer {context.timer.ref} control pin 5 should have "
                        "a small capacitor to GND."
                    ),
                    "details": {"ref": context.timer.ref, "ctrl_net": context.ctrl_net},
                }
            )
        if ctrl_non_ground_caps:
            warnings.append(
                {
                    "code": "TIMER555_CTRL_CAP_WRONG_TARGET",
                    "message": (
                        f"555 timer {context.timer.ref} control pin 5 capacitor "
                        "must connect only to GND."
                    ),
                    "details": {
                        "ref": context.timer.ref,
                        "ctrl_net": context.ctrl_net,
                        "non_ground_cap_targets": ctrl_non_ground_caps,
                    },
                }
            )

    if context.timing_net is None:
        return

    timing_caps_to_ground = _net_pair_bridge_refs(
        bridges,
        context.timing_net,
        context.ground_net or "",
        kind="capacitor",
    )
    timing_caps_to_vcc = _net_pair_bridge_refs(
        bridges,
        context.timing_net,
        context.vcc_net or "",
        kind="capacitor",
    )
    if not timing_caps_to_ground:
        warnings.append(
            {
                "code": "TIMER555_TIMING_CAP_NOT_TO_GROUND",
                "message": (
                    f"555 timer {context.timer.ref} timing node should connect "
                    "to GND through a capacitor."
                ),
                "details": {"ref": context.timer.ref, "timing_net": context.timing_net},
            }
        )
    if timing_caps_to_vcc:
        warnings.append(
            {
                "code": "TIMER555_TIMING_CAP_ACROSS_SUPPLY",
                "message": (
                    f"555 timer {context.timer.ref} timing capacitor appears to "
                    "bridge the timing node to VCC."
                ),
                "details": {
                    "ref": context.timer.ref,
                    "timing_net": context.timing_net,
                    "vcc_net": context.vcc_net,
                    "capacitor_refs": timing_caps_to_vcc,
                },
            }
        )


# ---------------------------------------------------------------------------
# Steering / gate / load warnings
# ---------------------------------------------------------------------------


def _append_timer555_steering_warnings(
    context: _Timer555Context,
    ir: CircuitIR,
    pin_to_net: dict[tuple[str, str], str],
    warnings: list[dict[str, object]],
) -> None:
    if context.discharge_net is None:
        return

    pot_pin1_net = pin_to_net.get((context.pot.ref, "1")) if context.pot is not None else None
    pot_pin2_net = pin_to_net.get((context.pot.ref, "2")) if context.pot is not None else None
    pot_pin3_net = pin_to_net.get((context.pot.ref, "3")) if context.pot is not None else None
    diode_targets: set[str] = set()
    for component in ir.components:
        if _symbol_tail(component.symbol) != "d":
            continue
        pin1_net = pin_to_net.get((component.ref, "1"))
        pin2_net = pin_to_net.get((component.ref, "2"))
        if (
            pin1_net == context.discharge_net
            and pin2_net is not None
            and pin2_net in {pot_pin1_net, pot_pin3_net}
        ):
            diode_targets.add(pin2_net)
        if (
            pin2_net == context.discharge_net
            and pin1_net is not None
            and pin1_net in {pot_pin1_net, pot_pin3_net}
        ):
            diode_targets.add(pin1_net)

    if (
        context.pot is None
        or context.timing_net is None
        or pot_pin2_net != context.timing_net
        or diode_targets != {pot_pin1_net, pot_pin3_net}
    ):
        warnings.append(
            {
                "code": "TIMER555_STEERING_NETWORK_INVALID",
                "message": (
                    f"555 timer {context.timer.ref} does not have the expected "
                    "diode-steered potentiometer timing network."
                ),
                "details": {
                    "ref": context.timer.ref,
                    "discharge_net": context.discharge_net,
                    "timing_net": context.timing_net,
                    "pot_ref": context.pot.ref if context.pot is not None else None,
                    "pot_pin1_net": pot_pin1_net,
                    "pot_pin2_net": pot_pin2_net,
                    "pot_pin3_net": pot_pin3_net,
                    "diode_target_nets": sorted(net for net in diode_targets if net is not None),
                },
            }
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


# ---------------------------------------------------------------------------
# Frequency warning
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Public entry points (called by lint rules in _validate.py)
# ---------------------------------------------------------------------------


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


def _timer555_warnings(
    ir: CircuitIR,
    _symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    return _timer555_pwm_warnings(ir)

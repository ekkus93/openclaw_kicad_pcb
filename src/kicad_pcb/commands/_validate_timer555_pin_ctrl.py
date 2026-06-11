"""555-timer pin-role, control/timing, and steering warnings."""

from __future__ import annotations

from ..circuit_ir import CircuitIR, ComponentIR
from ._validate_helpers import (
    _component_kind,
    _is_ground_net,
    _is_positive_supply_net,
    _net_pair_bridge_refs,
    _symbol_tail,
)
from ._validate_timer555_context import _Timer555Context


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

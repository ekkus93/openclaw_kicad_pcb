"""555-timer context dataclass and context-building helpers."""

from __future__ import annotations

from dataclasses import dataclass

from ..circuit_ir import CircuitIR, ComponentIR
from ._validate_helpers import (
    _is_ground_net,
    _looks_nmos,
    _symbol_tail,
)


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


__all__ = [
    "_TIMER555_HARD_FAIL_CODES",
    "_Timer555Context",
    "_build_timer555_context",
    "_looks_timer555_pwm_topology",
]

"""Phase 6 route-nets tests — feedback, short nets, continuation."""

from __future__ import annotations

import math

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands._sch_apply import SCHEMATIC_HEURISTIC_PROFILES
from kicad_pcb.router import (
    WireSegment,
    route_nets,
)


def test_route_nets_classifies_feedback_net_explicitly() -> None:
    """Feedback-style nets should surface the first-class feedback routing category."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
            ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ComponentIR(ref="R4", symbol="Device:R", value="100k"),
        ],
        nets=[
            NetIR(
                name="U1A_INV",
                pins=[
                    PinRefIR(ref="R1", pin="2"),
                    PinRefIR(ref="R4", pin="2"),
                    PinRefIR(ref="U1", pin="2"),
                ],
            ),
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("R1", "2"): (54.61, 125.73, 90.0),
            ("R4", "2"): (85.09, 125.73, 90.0),
            ("U1", "2"): (69.85, 106.68, 270.0),
        },
        heuristic_policy=SCHEMATIC_HEURISTIC_PROFILES["analog_audio"].routing_policy,
    )

    choice = next(choice for choice in routing.route_decisions if choice.net_name == "U1A_INV")

    assert choice.classification == "feedback"


def test_short_signal_chain_net_stays_direct_before_label_fallback() -> None:
    """Short local signal-chain nets should stay wired even when tier drift would label them."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
        ],
        nets=[
            NetIR(
                name="STAGE_L",
                pins=[
                    PinRefIR(ref="R1", pin="1"),
                    PinRefIR(ref="U1", pin="3"),
                ],
            ),
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("R1", "1"): (30.0, 100.0, 0.0),
            ("U1", "3"): (70.0, 100.0, 180.0),
        },
        tiers={"R1": 0, "U1": 2},
    )

    choice = next(choice for choice in routing.route_decisions if choice.net_name == "STAGE_L")

    assert choice.classification == "signal_chain"
    assert choice.strategy == "direct"
    assert routing.labels == []


def test_short_feedback_net_stays_direct_before_label_fallback() -> None:
    """Short feedback nets should prefer a local wire over label fallback."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
        ],
        nets=[
            NetIR(
                name="U1A_INV",
                pins=[
                    PinRefIR(ref="R1", pin="1"),
                    PinRefIR(ref="U1", pin="2"),
                ],
            ),
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("R1", "1"): (30.0, 100.0, 0.0),
            ("U1", "2"): (70.0, 100.0, 180.0),
        },
        tiers={"R1": 0, "U1": 2},
    )

    choice = next(choice for choice in routing.route_decisions if choice.net_name == "U1A_INV")

    assert choice.classification == "feedback"
    assert choice.strategy == "direct"
    assert routing.labels == []


def test_short_connector_attachment_net_stays_direct_before_label_fallback() -> None:
    """Short local connector-attachment nets should stay wired instead of labeling."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x02", value="IN"),
            ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
        ],
        nets=[
            NetIR(
                name="LEFT_IN",
                pins=[
                    PinRefIR(ref="J1", pin="1"),
                    PinRefIR(ref="U1", pin="3"),
                ],
            ),
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("J1", "1"): (30.0, 100.0, 0.0),
            ("U1", "3"): (69.0, 100.0, 180.0),
        },
        tiers={"J1": 0, "U1": 2},
    )

    choice = next(choice for choice in routing.route_decisions if choice.net_name == "LEFT_IN")

    assert choice.classification == "connector_attachment"
    assert choice.strategy == "direct"
    assert routing.labels == []


def test_route_nets_routes_full_preview_vol_l_out_as_downstream_continuation() -> None:
    """Full-preview VOL_L_OUT should read as RV1 continuing downstream into U1."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="C5", symbol="Device:C", value="1u"),
            ComponentIR(ref="R1", symbol="Device:R", value="100k"),
            ComponentIR(ref="RV1", symbol="Device:R_Potentiometer", value="10k"),
            ComponentIR(ref="R4", symbol="Device:R", value="100k"),
            ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
        ],
        nets=[
            NetIR(
                name="IN_L_AC",
                pins=[
                    PinRefIR(ref="C5", pin="2"),
                    PinRefIR(ref="R1", pin="2"),
                    PinRefIR(ref="RV1", pin="1"),
                ],
            ),
            NetIR(
                name="VOL_L_OUT",
                pins=[
                    PinRefIR(ref="RV1", pin="2"),
                    PinRefIR(ref="R4", pin="1"),
                    PinRefIR(ref="U1", pin="3"),
                ],
            ),
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("C5", "2"): (54.61, 106.68, 90.0),
            ("R1", "2"): (54.61, 121.92, 90.0),
            ("RV1", "1"): (85.09, 144.78, 270.0),
            ("RV1", "2"): (88.90, 140.97, 180.0),
            ("R4", "1"): (85.09, 114.30, 270.0),
            ("U1", "3"): (138.43, 120.65, 0.0),
        },
        positions={
            "C5": (54.61, 110.49, 0.0),
            "R1": (54.61, 125.73, 0.0),
            "RV1": (85.09, 140.97, 0.0),
            "R4": (85.09, 110.49, 0.0),
            "U1": (146.05, 118.11, 0.0),
        },
    )
    choice = next(choice for choice in routing.route_decisions if choice.net_name == "VOL_L_OUT")

    assert choice.strategy == "compact_signal_tail"
    assert choice.heuristic_override == "compact_output_tail"
    assert WireSegment(138.43, 120.65, 85.09, 120.65) not in routing.wires
    assert WireSegment(85.09, 119.38, 88.90, 119.38) in routing.wires
    assert any(
        math.isclose(w.x1, 88.90, abs_tol=0.01)
        and math.isclose(w.x2, 88.90, abs_tol=0.01)
        and {round(w.y1, 2), round(w.y2, 2)} == {119.38, 140.97}
        for w in routing.wires
    )
    assert WireSegment(88.90, 140.97, 138.43, 140.97) in routing.wires
    assert any(
        math.isclose(w.x1, 138.43, abs_tol=0.01)
        and math.isclose(w.x2, 138.43, abs_tol=0.01)
        and {round(w.y1, 2), round(w.y2, 2)} == {120.65, 140.97}
        for w in routing.wires
    )
    assert not any(
        math.isclose(w.x1, 93.98, abs_tol=0.01) or math.isclose(w.x2, 93.98, abs_tol=0.01)
        for w in routing.wires
    )


def test_route_nets_vol_l_out_no_l_shaped_detour_when_rv1_exits_rightward() -> None:
    """VOL_L_OUT with RV1 pin2 at angle 180° must not produce a right-then-up detour.

    In the original problem geometry (snapshot _230958), RV1 pin2 exits rightward
    while the op-amp input sits to the right. The old behaviour produced a short
    rightward stub followed immediately by a drop into a horizontal bus-like trunk.
    After the fix, RV1 feeds a short support rise from R4 and then continues as one
    dominant downstream run toward U1.
    """
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="C5", symbol="Device:C", value="1u"),
            ComponentIR(ref="R1", symbol="Device:R", value="100k"),
            ComponentIR(ref="RV1", symbol="Device:R_Potentiometer", value="10k"),
            ComponentIR(ref="R4", symbol="Device:R", value="100k"),
            ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
        ],
        nets=[
            NetIR(
                name="IN_L_AC",
                pins=[
                    PinRefIR(ref="C5", pin="2"),
                    PinRefIR(ref="R1", pin="2"),
                    PinRefIR(ref="RV1", pin="1"),
                ],
            ),
            NetIR(
                name="VOL_L_OUT",
                pins=[
                    PinRefIR(ref="RV1", pin="2"),
                    PinRefIR(ref="R4", pin="1"),
                    PinRefIR(ref="U1", pin="3"),
                ],
            ),
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("C5", "2"): (54.61, 106.68, 90.0),
            ("R1", "2"): (54.61, 121.92, 90.0),
            ("RV1", "1"): (85.09, 129.54, 270.0),
            ("RV1", "2"): (88.90, 133.35, 180.0),  # exits rightward — the problem pin
            ("R4", "1"): (85.09, 119.38, 270.0),
            ("U1", "3"): (138.43, 120.65, 0.0),
        },
        positions={
            "C5": (54.61, 110.49, 0.0),
            "R1": (54.61, 125.73, 0.0),
            "RV1": (85.09, 133.35, 0.0),
            "R4": (85.09, 123.19, 0.0),
            "U1": (146.05, 118.11, 0.0),
        },
    )
    choice = next(choice for choice in routing.route_decisions if choice.net_name == "VOL_L_OUT")

    assert choice.strategy == "compact_signal_tail"
    assert choice.heuristic_override == "compact_output_tail"
    assert WireSegment(85.09, 124.46, 88.90, 124.46) in routing.wires
    assert any(
        math.isclose(w.x1, 88.90, abs_tol=0.01)
        and math.isclose(w.x2, 88.90, abs_tol=0.01)
        and {round(w.y1, 2), round(w.y2, 2)} == {124.46, 133.35}
        for w in routing.wires
    )
    assert WireSegment(88.90, 133.35, 138.43, 133.35) in routing.wires
    assert any(
        math.isclose(w.x1, 138.43, abs_tol=0.01)
        and math.isclose(w.x2, 138.43, abs_tol=0.01)
        and {round(w.y1, 2), round(w.y2, 2)} == {120.65, 133.35}
        for w in routing.wires
    )
    # Old bus-like ladder segments must be absent.
    assert WireSegment(85.09, 124.46, 133.35, 124.46) not in routing.wires
    assert not any(
        math.isclose(w.x1, 93.98, abs_tol=0.01) and math.isclose(w.x2, 93.98, abs_tol=0.01)
        for w in routing.wires
    ), "expected no wire at x=93.98 (old L-detour connector position)"

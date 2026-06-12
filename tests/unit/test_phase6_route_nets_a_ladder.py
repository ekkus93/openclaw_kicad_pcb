"""Phase 6: ladder routing, routing profiles, direct routing, output tails, ground lane routing."""

from __future__ import annotations

import math
from collections import defaultdict

from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands._sch_apply import SCHEMATIC_HEURISTIC_PROFILES
from kicad_pcb.router import (
    DEFAULT_ROUTING_HEURISTIC_POLICY,
    NetRouting,
    RoutingHeuristicPolicy,
    SharedLanePlan,
    WireSegment,
    _plan_local_ladder_routes,
    route_nets,
)


def _count_short_segments(wires: list[WireSegment], threshold_mm: float = 5.1) -> int:
    """Count short wire segments at or below *threshold_mm*."""
    return sum(1 for seg in wires if math.hypot(seg.x2 - seg.x1, seg.y2 - seg.y1) <= threshold_mm)


def _connected_power_symbol_sets(routing: NetRouting) -> list[set[str]]:
    parent: dict[tuple[float, float], tuple[float, float]] = {}

    def find(point: tuple[float, float]) -> tuple[float, float]:
        parent.setdefault(point, point)
        if parent[point] != point:
            parent[point] = find(parent[point])
        return parent[point]

    def union(left: tuple[float, float], right: tuple[float, float]) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    labels_by_root: dict[tuple[float, float], set[str]] = defaultdict(set)
    for wire in routing.wires:
        start = (round(wire.x1, 2), round(wire.y1, 2))
        end = (round(wire.x2, 2), round(wire.y2, 2))
        union(start, end)
    for power_symbol in routing.power_symbols:
        point = (round(power_symbol.x, 2), round(power_symbol.y, 2))
        labels_by_root[find(point)].add(f"PWR:{power_symbol.net_name}")
    return list(labels_by_root.values())


def test_plan_local_ladder_routes_skips_compact_rightward_output_tail() -> None:
    """A compact rightward 3-pin tail should fall back to chain routing, not a lane."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="C7", symbol="Device:C", value="100n"),
            ComponentIR(ref="R6", symbol="Device:R", value="47"),
            ComponentIR(ref="R7", symbol="Device:R", value="100"),
            ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="OUT"),
        ],
        nets=[
            NetIR(
                name="AFTER_R6",
                pins=[PinRefIR(ref="R6", pin="2"), PinRefIR(ref="C7", pin="1")],
            ),
            NetIR(
                name="HP_L_OUT",
                pins=[
                    PinRefIR(ref="C7", pin="2"),
                    PinRefIR(ref="J2", pin="1"),
                    PinRefIR(ref="R7", pin="1"),
                ],
            ),
        ],
    )

    plans = _plan_local_ladder_routes(
        ir,
        pin_endpoints={
            ("R6", "2"): (55.08, 20.0, 0.0),
            ("C7", "1"): (55.08, 35.0, 0.0),
            ("C7", "2"): (55.08, 10.0, 0.0),
            ("J2", "1"): (55.08, 20.0, 0.0),
            ("R7", "1"): (75.08, 20.0, 0.0),
        },
    )

    assert "HP_L_OUT" not in plans


def test_plan_local_ladder_routes_skips_asymmetric_compact_output_tail() -> None:
    """A near-lane asymmetric output tail should still avoid inferred ladder routing."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="C7", symbol="Device:C", value="100n"),
            ComponentIR(ref="R6", symbol="Device:R", value="47"),
            ComponentIR(ref="R7", symbol="Device:R", value="100"),
            ComponentIR(ref="J2", symbol="Connector:AudioJack3", value="OUT"),
        ],
        nets=[
            NetIR(
                name="AFTER_R6",
                pins=[PinRefIR(ref="R6", pin="2"), PinRefIR(ref="C7", pin="1")],
            ),
            NetIR(
                name="HP_L_OUT",
                pins=[
                    PinRefIR(ref="C7", pin="2"),
                    PinRefIR(ref="R7", pin="1"),
                    PinRefIR(ref="J2", pin="T"),
                ],
            ),
        ],
    )

    plans = _plan_local_ladder_routes(
        ir,
        pin_endpoints={
            ("R6", "2"): (213.36, 173.99, 90.0),
            ("C7", "1"): (213.36, 135.89, 270.0),
            ("C7", "2"): (213.36, 128.27, 90.0),
            ("R7", "1"): (238.76, 166.37, 270.0),
            ("J2", "T"): (217.17, 165.10, 0.0),
        },
    )

    assert "HP_L_OUT" not in plans


def test_plan_local_ladder_routes_can_disable_compact_output_tail_policy() -> None:
    """Disabling the analog compact-tail rule should restore the inferred lane plan."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="C7", symbol="Device:C", value="100n"),
            ComponentIR(ref="R6", symbol="Device:R", value="47"),
            ComponentIR(ref="R7", symbol="Device:R", value="100"),
            ComponentIR(ref="J2", symbol="Connector:AudioJack3", value="OUT"),
        ],
        nets=[
            NetIR(
                name="AFTER_R6",
                pins=[PinRefIR(ref="R6", pin="2"), PinRefIR(ref="C7", pin="1")],
            ),
            NetIR(
                name="HP_L_OUT",
                pins=[
                    PinRefIR(ref="C7", pin="2"),
                    PinRefIR(ref="R7", pin="1"),
                    PinRefIR(ref="J2", pin="T"),
                ],
            ),
        ],
    )

    plans = _plan_local_ladder_routes(
        ir,
        pin_endpoints={
            ("R6", "2"): (213.36, 173.99, 90.0),
            ("C7", "1"): (213.36, 135.89, 270.0),
            ("C7", "2"): (213.36, 128.27, 90.0),
            ("R7", "1"): (238.76, 166.37, 270.0),
            ("J2", "T"): (217.17, 165.10, 0.0),
        },
        heuristic_policy=RoutingHeuristicPolicy(enable_compact_output_tails=False),
    )

    assert DEFAULT_ROUTING_HEURISTIC_POLICY.enable_compact_output_tails
    assert "HP_L_OUT" in plans
    assert plans["HP_L_OUT"] == SharedLanePlan("vertical", 213.36, 123.19, 165.1)


def test_small_analog_local_routing_prefers_chain_over_compact_lane() -> None:
    """Analog small-circuit mode should drop a compact lane when a chain is cleaner."""
    endpoints = [(54.61, 114.30), (54.61, 125.73), (85.09, 129.54)]
    lane_plan = SharedLanePlan("vertical", 54.61, 114.30, 129.54)
    analog_audio = SCHEMATIC_HEURISTIC_PROFILES["analog_audio"]

    assert DEFAULT_ROUTING_HEURISTIC_POLICY.enable_small_analog_local_routing is False
    assert analog_audio.routing_policy.enable_small_analog_local_routing is True
    assert analog_audio.routing_policy.should_prefer_small_analog_chain(
        endpoints,
        inferred_plan=lane_plan,
    )
    assert not RoutingHeuristicPolicy(
        enable_small_analog_local_routing=False,
    ).should_prefer_small_analog_chain(
        endpoints,
        inferred_plan=lane_plan,
    )


def test_small_analog_local_routing_draws_buffer_follower_as_local_loop_plus_branch() -> None:
    """Analog mode should draw a buffer follower net as a compact local loop."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="U1B", symbol="Amplifier_Operational:NE5532", value="NE5532"),
            ComponentIR(ref="R6", symbol="Device:R", value="47"),
        ],
        nets=[
            NetIR(
                name="OUT_L_STAGE2_RAW",
                pins=[
                    PinRefIR(ref="U1B", pin="6"),
                    PinRefIR(ref="U1B", pin="7"),
                    PinRefIR(ref="R6", pin="1"),
                ],
            ),
        ],
    )
    pin_endpoints = {
        ("U1B", "6"): (140.0, 100.0, 0.0),
        ("U1B", "7"): (150.0, 100.0, 180.0),
        ("R6", "1"): (170.0, 100.0, 0.0),
    }
    block_layout = BlockLayout()
    block_layout.add_assignment("U1B", BlockRole.BUFFER_STAGE)
    block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)

    analog_routing = route_nets(
        ir=ir,
        pin_endpoints=pin_endpoints,
        block_layout=block_layout,
        heuristic_policy=SCHEMATIC_HEURISTIC_PROFILES["analog_audio"].routing_policy,
    )
    digital_routing = route_nets(
        ir=ir,
        pin_endpoints=pin_endpoints,
        block_layout=block_layout,
        heuristic_policy=SCHEMATIC_HEURISTIC_PROFILES["generic_digital"].routing_policy,
    )

    analog_choice = analog_routing.route_decisions[0]
    digital_choice = digital_routing.route_decisions[0]
    analog_segments = {
        (
            round(seg.x1, 2),
            round(seg.y1, 2),
            round(seg.x2, 2),
            round(seg.y2, 2),
        )
        for seg in analog_routing.wires
    }
    digital_segments = {
        (
            round(seg.x1, 2),
            round(seg.y1, 2),
            round(seg.x2, 2),
            round(seg.y2, 2),
        )
        for seg in digital_routing.wires
    }

    assert analog_choice.strategy == "chain"
    assert analog_choice.heuristic_override == "small_analog_local_routing"
    assert digital_choice.heuristic_override is None
    assert (134.92, 95.25, 155.08, 95.25) in analog_segments, analog_routing.wires
    assert (134.92, 100.0, 134.92, 95.25) in analog_segments, analog_routing.wires
    assert (155.08, 100.0, 155.08, 95.25) in analog_segments, analog_routing.wires
    assert (170.0, 100.0, 164.92, 100.0) in analog_segments, analog_routing.wires
    assert (155.08, 100.0, 164.92, 100.0) in analog_segments, analog_routing.wires
    assert (134.92, 95.25, 155.08, 95.25) not in digital_segments, digital_routing.wires


def test_named_routing_profiles_diverge_on_small_analog_input_chain_fixture() -> None:
    """Analog small-circuit mode should prefer a chain where digital keeps a lane."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="IN"),
            ComponentIR(ref="C5", symbol="Device:C", value="1u"),
            ComponentIR(ref="R1", symbol="Device:R", value="100k"),
            ComponentIR(ref="RV1", symbol="Device:R_Potentiometer", value="10k"),
        ],
        nets=[
            NetIR(
                name="LEFT_IN",
                pins=[
                    PinRefIR(ref="J1", pin="1"),
                    PinRefIR(ref="C5", pin="1"),
                    PinRefIR(ref="R1", pin="1"),
                ],
            ),
            NetIR(
                name="IN_L_AC",
                pins=[
                    PinRefIR(ref="C5", pin="2"),
                    PinRefIR(ref="R1", pin="2"),
                    PinRefIR(ref="RV1", pin="1"),
                ],
            ),
        ],
    )
    pin_endpoints = {
        ("J1", "1"): (39.37, 123.19, 180.0),
        ("C5", "1"): (54.61, 106.68, 270.0),
        ("R1", "1"): (54.61, 118.11, 270.0),
        ("C5", "2"): (54.61, 114.30, 90.0),
        ("R1", "2"): (54.61, 125.73, 90.0),
        ("RV1", "1"): (85.09, 129.54, 270.0),
    }
    analog_audio = SCHEMATIC_HEURISTIC_PROFILES["analog_audio"]
    generic_digital = SCHEMATIC_HEURISTIC_PROFILES["generic_digital"]

    analog_routing = route_nets(
        ir=ir,
        pin_endpoints=pin_endpoints,
        heuristic_policy=analog_audio.routing_policy,
    )
    digital_routing = route_nets(
        ir=ir,
        pin_endpoints=pin_endpoints,
        heuristic_policy=generic_digital.routing_policy,
    )
    analog_choice = next(
        choice for choice in analog_routing.route_decisions if choice.net_name == "IN_L_AC"
    )
    digital_choice = next(
        choice for choice in digital_routing.route_decisions if choice.net_name == "IN_L_AC"
    )
    left_in_choice = next(
        choice for choice in analog_routing.route_decisions if choice.net_name == "LEFT_IN"
    )

    assert analog_audio.routing_policy.enable_small_analog_local_routing is True
    assert generic_digital.routing_policy.enable_small_analog_local_routing is False
    assert left_in_choice.classification == "connector_attachment"
    assert analog_choice.classification == "signal_chain"
    assert digital_choice.classification == "signal_chain"
    assert analog_choice.strategy == "chain"
    assert analog_choice.heuristic_override == "small_analog_local_routing"
    assert digital_choice.strategy == "shared_lane"
    assert digital_choice.heuristic_override is None


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

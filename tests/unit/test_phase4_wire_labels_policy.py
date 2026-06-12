"""Phase 4: property text spacing, label policy, label modes, and power symbol tests."""

from __future__ import annotations

import pytest

from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.graphviz_layout.snap import (
    _apply_property_text_spacing,
)
from kicad_pcb.lint import LintSeverity
from kicad_pcb.router import (
    LABEL_MODE_POLICIES,
    LabelPolicy,
    route_nets,
)
from kicad_pcb.sexpr import parse
from kicad_pcb.sexpr.nodes import ListNode

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WARN = LintSeverity.WARNING
_ERR = LintSeverity.ERROR


def _minimal_ir(
    *,
    refs: list[str] | None = None,
    nets: list[dict] | None = None,
) -> CircuitIR:
    """Build a minimal CircuitIR for testing.

    *refs* defaults to ["R1", "R2"].
    *nets* is a list of dicts with keys ``name`` and ``pins`` (list of
    ``{"ref": ..., "pin": ...}`` dicts).
    """
    if refs is None:
        refs = ["R1", "R2"]
    if nets is None:
        nets = [
            {"name": "NET1", "pins": [{"ref": refs[0], "pin": "1"}, {"ref": refs[1], "pin": "1"}]}
        ]

    components = [ComponentIR(ref=r, symbol="Device:R", value="1k") for r in refs]
    ir_nets = [NetIR(name=n["name"], pins=[PinRefIR(**p) for p in n["pins"]]) for n in nets]
    return CircuitIR(version="1", components=components, nets=ir_nets)


def _sch(body: str = "") -> ListNode:
    """Parse a minimal kicad_sch document with optional *body*."""
    return parse(
        "(kicad_sch (version 20230121) (generator test)\n"
        "  (lib_symbols)\n"
        f"  {body}\n"
        '  (sheet_instances (path "/" (page "1")))\n'
        ")"
    )


def _wire(x1: float, y1: float, x2: float, y2: float) -> str:
    """Return an S-expression wire snippet."""
    return f"(wire (pts (xy {x1} {y1}) (xy {x2} {y2})))"


def _symbol_at(x: float, y: float) -> str:
    """Return a minimal symbol snippet at (x, y)."""
    return (
        f'(symbol (lib_id "Device:R") (at {x} {y} 0) (uuid "00000000-0000-0000-0000-000000000001"))'
    )


def _label(name: str, x: float = 10.0, y: float = 10.0) -> str:
    return f'(label "{name}" (at {x} {y} 0))'


def _codes(issues: list) -> list[str]:
    return [i.code for i in issues]


def _sev(issues: list, code: str) -> LintSeverity | None:
    for i in issues:
        if i.code == code:
            return i.severity
    return None


def _make_ir(
    components: list[tuple[str, str]],
    nets: list[tuple[str, list[tuple[str, str]]]],
    *,
    version: str = "1",
) -> CircuitIR:
    """Minimal CircuitIR factory.

    *components* is ``[(ref, symbol), ...]``.
    *nets* is ``[(net_name, [(ref, pin), ...]), ...]``.
    """
    ir_components = [ComponentIR(ref=ref, symbol=sym) for ref, sym in components]
    ir_nets = [
        NetIR(name=name, pins=[PinRefIR(ref=r, pin=p) for r, p in pins]) for name, pins in nets
    ]
    if not ir_components:
        ir_components = [ComponentIR(ref="_DUMMY", symbol="_")]
    if not ir_nets:
        ir_nets = [NetIR(name="_NC", pins=[PinRefIR(ref=ir_components[0].ref, pin="1")])]
    return CircuitIR(version=version, components=ir_components, nets=ir_nets)


# ---------------------------------------------------------------------------
# 4.1 / 4.2  LayoutEngine factory — NoneLayoutEngine
# ---------------------------------------------------------------------------


class TestPropertyTextSpacing:
    """Unit tests for the late property-text spacing pass."""

    def test_pushes_nearby_x_lanes_apart(self) -> None:
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (50.8, 50.8, 0.0),
            "R2": (63.5, 60.96, 0.0),
        }

        result = _apply_property_text_spacing(positions)

        assert result["R1"] == positions["R1"]
        assert result["R2"][1] == pytest.approx(66.04)

    def test_leaves_distant_x_lanes_unchanged(self) -> None:
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (50.8, 50.8, 0.0),
            "R2": (114.3, 60.96, 0.0),
        }

        result = _apply_property_text_spacing(positions)

        assert result == positions

    def test_skips_power_refs(self) -> None:
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (50.8, 50.8, 0.0),
            "#PWR01": (50.8, 58.42, 0.0),
        }

        result = _apply_property_text_spacing(positions)

        assert result == positions

    def test_keeps_fixed_refs_stationary(self) -> None:
        positions: dict[str, tuple[float, float, float | None]] = {
            "U1": (50.8, 50.8, 0.0),
            "R1": (63.5, 60.96, 0.0),
        }

        result = _apply_property_text_spacing(positions, fixed_refs=frozenset({"R1"}))

        assert result == positions


# ---------------------------------------------------------------------------
# Phase 2.3 — Label duplication limits (LabelPolicy)
# ---------------------------------------------------------------------------


class TestLabelPolicy:
    """Unit tests for :class:`LabelPolicy` and the ``policy`` param of :func:`route_nets`.

    Routing path notes used by fixture design
    -----------------------------------------
    * **Hub** fires when ``3 ≤ len(known) ≤ 6`` **and** ``not unknown``.  A net
      with any unknown pins bypasses hub → falls to label-fallback.
    * **High-degree** fires when ``len(known) > 6`` (regardless of unknown).
    * **Label-fallback**: emits one :class:`NetLabel` per known pin (capped by
      ``policy.max_labels_per_net``) and one per unknown pin (always).
    """

    # ------------------------------------------------------------------
    # Label-fallback (known-pin cap)
    # ------------------------------------------------------------------

    def test_default_policy_caps_known_pin_labels_at_two(self) -> None:
        """Label-fallback: 4 known + 1 unknown → default policy caps known at 2.

        Without the policy gate the loop would emit 4 known labels + 1 unknown = 5.
        With ``DEFAULT_LABEL_POLICY`` (max=2) it should emit 2 known + 1 unknown = 3.
        """
        # 4 known (R1–R4) + 1 unknown (R5 absent from pin_endpoints).
        # Hub is bypassed because ``unknown`` is non-empty.
        # High-degree is bypassed because len(known)=4 ≤ 6.
        ir = _make_ir(
            [(f"R{i}", "Device:R") for i in range(1, 6)],
            [("SIG", [(f"R{i}", "1") for i in range(1, 6)])],
        )
        pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            (f"R{i}", "1"): (float(i * 10), 0.0, 0.0) for i in range(1, 5)
        }  # R5 absent → unknown
        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints)
        # Default policy: 2 known labels + 1 unknown label = 3 total.
        assert len(routing.labels) == 3, (
            f"Expected 3 labels (2 capped known + 1 unknown); got {routing.labels}"
        )

    def test_custom_policy_max_one_known_label(self) -> None:
        """policy(max_labels_per_net=1): 4 known + 1 unknown → 1 known + 1 unknown = 2."""
        ir = _make_ir(
            [(f"R{i}", "Device:R") for i in range(1, 6)],
            [("SIG", [(f"R{i}", "1") for i in range(1, 6)])],
        )
        pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            (f"R{i}", "1"): (float(i * 10), 0.0, 0.0) for i in range(1, 5)
        }  # R5 absent → unknown
        policy = LabelPolicy(max_labels_per_net=1)
        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints, policy=policy)
        assert len(routing.labels) == 2, (
            f"Expected 2 labels (1 capped known + 1 unknown); got {routing.labels}"
        )

    def test_unlimited_policy_emits_all_labels(self) -> None:
        """policy(max_labels_per_net=999): all 4 known + 1 unknown = 5 labels emitted."""
        ir = _make_ir(
            [(f"R{i}", "Device:R") for i in range(1, 6)],
            [("SIG", [(f"R{i}", "1") for i in range(1, 6)])],
        )
        pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            (f"R{i}", "1"): (float(i * 10), 0.0, 0.0) for i in range(1, 5)
        }  # R5 absent → unknown
        policy = LabelPolicy(max_labels_per_net=999)
        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints, policy=policy)
        assert len(routing.labels) == 5, (
            f"Expected 5 labels (4 known + 1 unknown, unlimited); got {routing.labels}"
        )

    def test_two_pin_label_fallback_unaffected_by_default_policy(self) -> None:
        """2 far-apart known pins → 2 labels; default max=2 does not reduce this."""
        ir = _make_ir(
            [("R1", "Device:R"), ("R2", "Device:R")],
            [("NET1", [("R1", "1"), ("R2", "1")])],
        )
        pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            ("R1", "1"): (30.0, 100.0, 0.0),
            ("R2", "1"): (250.0, 100.0, 180.0),  # 220 mm > MAX_DIRECT_DIST_MM (200 mm)
        }
        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints)
        assert len(routing.labels) == 2, (
            f"Default policy should leave 2-pin fallback unchanged; got {routing.labels}"
        )

    # ------------------------------------------------------------------
    # High-degree global-label cap
    # ------------------------------------------------------------------

    def test_high_degree_global_labels_capped_at_default_four(self) -> None:
        """8-pin non-power net (degree>6 → global-label path): default policy caps at 4."""
        # 8 components, all in pin_endpoints (all known).  Non-power name → global labels.
        ir = _make_ir(
            [(f"R{i}", "Device:R") for i in range(1, 9)],
            [("SIG", [(f"R{i}", "1") for i in range(1, 9)])],
        )
        pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            (f"R{i}", "1"): (float(i * 10), 0.0, 0.0) for i in range(1, 9)
        }
        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints)
        # Default policy: max_global_labels_per_net=4.
        assert len(routing.global_labels) == 4, (
            f"Expected 4 global labels (default cap); got {routing.global_labels}"
        )

    def test_high_degree_custom_global_label_policy(self) -> None:
        """policy(max_global_labels_per_net=2): 8-pin net emits only 2 GlobalLabelPlacements."""
        ir = _make_ir(
            [(f"R{i}", "Device:R") for i in range(1, 9)],
            [("SIG", [(f"R{i}", "1") for i in range(1, 9)])],
        )
        pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            (f"R{i}", "1"): (float(i * 10), 0.0, 0.0) for i in range(1, 9)
        }
        policy = LabelPolicy(max_global_labels_per_net=2)
        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints, policy=policy)
        assert len(routing.global_labels) == 2, (
            f"Expected 2 global labels (custom cap=2); got {routing.global_labels}"
        )


class TestStructuralRoutingClassification:
    def test_feedback_role_overrides_neutral_net_name(self) -> None:
        ir = _make_ir(
            [("U1", "Amplifier_Operational:OpAmp_Dual_Generic"), ("R2", "Device:R")],
            [("N001", [("U1", "1"), ("R2", "1")])],
        )
        pin_endpoints = {
            ("U1", "1"): (40.0, 40.0, 180.0),
            ("R2", "1"): (60.0, 40.0, 0.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("R2", BlockRole.FEEDBACK)

        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints, block_layout=block_layout)

        assert routing.route_decisions[0].classification == "feedback"

    def test_interstage_roles_override_neutral_net_name(self) -> None:
        ir = _make_ir(
            [
                ("U1", "Amplifier_Operational:OpAmp_Dual_Generic"),
                ("C6", "Device:C"),
                ("R5", "Device:R"),
            ],
            [("N002", [("U1", "1"), ("C6", "1"), ("R5", "1")])],
        )
        pin_endpoints = {
            ("U1", "1"): (40.0, 40.0, 180.0),
            ("C6", "1"): (60.0, 30.0, 270.0),
            ("R5", "1"): (60.0, 50.0, 90.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("C6", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R5", BlockRole.INTERSTAGE)

        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints, block_layout=block_layout)

        assert routing.route_decisions[0].classification == "signal_chain"

    def test_name_fallback_still_applies_without_block_layout(self) -> None:
        ir = _make_ir(
            [("U1", "Amplifier_Operational:OpAmp_Dual_Generic"), ("R2", "Device:R")],
            [("U1A_INV", [("U1", "1"), ("R2", "1")])],
        )
        pin_endpoints = {
            ("U1", "1"): (40.0, 40.0, 180.0),
            ("R2", "1"): (60.0, 40.0, 0.0),
        }

        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints)

        assert routing.route_decisions[0].classification == "feedback"


class TestStructuralLabelPriority:
    def test_structural_roles_prioritize_visible_local_labels(self) -> None:
        ir = _make_ir(
            [(f"R{i}", "Device:R") for i in range(1, 6)],
            [("N_STAGE", [(f"R{i}", "1") for i in range(1, 6)])],
        )
        pin_endpoints = {
            ("R1", "1"): (10.0, 0.0, 0.0),
            ("R2", "1"): (20.0, 0.0, 0.0),
            ("R3", "1"): (30.0, 0.0, 0.0),
            ("R4", "1"): (40.0, 0.0, 0.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R2", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("R3", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R4", BlockRole.OUTPUT)

        routing = route_nets(
            ir=ir,
            pin_endpoints=pin_endpoints,
            block_layout=block_layout,
        )

        visible_x = {round(label.x, 2) for label in routing.labels if label.x > 0.0}
        assert visible_x == {30.48, 39.37}

    def test_structural_roles_prioritize_visible_global_labels(self) -> None:
        ir = _make_ir(
            [(f"R{i}", "Device:R") for i in range(1, 9)],
            [("N_BUS", [(f"R{i}", "1") for i in range(1, 9)])],
        )
        pin_endpoints = {(f"R{i}", "1"): (float(i * 10), 0.0, 0.0) for i in range(1, 9)}
        block_layout = BlockLayout()
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R2", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("R3", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R4", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R5", BlockRole.OUTPUT)
        block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("R7", BlockRole.DECOUPLING)
        block_layout.add_assignment("R8", BlockRole.POWER_ENTRY)
        policy = LabelPolicy(max_global_labels_per_net=2)

        routing = route_nets(
            ir=ir,
            pin_endpoints=pin_endpoints,
            block_layout=block_layout,
            policy=policy,
        )

        visible_x = {round(label.x, 2) for label in routing.global_labels if label.name == "N_BUS"}
        assert visible_x == {39.37, 49.53}

    def test_label_caps_preserve_existing_order_without_structural_context(self) -> None:
        ir = _make_ir(
            [(f"R{i}", "Device:R") for i in range(1, 6)],
            [("N_STAGE", [(f"R{i}", "1") for i in range(1, 6)])],
        )
        pin_endpoints = {
            ("R1", "1"): (10.0, 0.0, 0.0),
            ("R2", "1"): (20.0, 0.0, 0.0),
            ("R3", "1"): (30.0, 0.0, 0.0),
            ("R4", "1"): (40.0, 0.0, 0.0),
        }

        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints)

        visible_x = {round(label.x, 2) for label in routing.labels if label.x > 0.0}
        assert visible_x == {10.16, 20.32}


class TestLabelModes:
    def test_debug_mode_expands_label_fallback_caps(self) -> None:
        ir = _make_ir(
            [(f"R{i}", "Device:R") for i in range(1, 6)],
            [("SIG", [(f"R{i}", "1") for i in range(1, 6)])],
        )
        pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            (f"R{i}", "1"): (float(i * 10), 0.0, 0.0) for i in range(1, 5)
        }

        routing = route_nets(
            ir=ir,
            pin_endpoints=pin_endpoints,
            policy=LABEL_MODE_POLICIES["debug"],
        )

        assert len(routing.labels) == 5

    def test_important_mode_promotes_one_direct_signal_chain_label(self) -> None:
        ir = _make_ir(
            [
                ("J1", "Connector_Generic:Conn_01x02"),
                ("R1", "Device:R"),
            ],
            [("LEFT_IN", [("J1", "1"), ("R1", "1")])],
        )
        pin_endpoints = {
            ("J1", "1"): (30.0, 100.0, 0.0),
            ("R1", "1"): (70.0, 100.0, 180.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)

        routing = route_nets(
            ir=ir,
            pin_endpoints=pin_endpoints,
            block_layout=block_layout,
            policy=LABEL_MODE_POLICIES["always-show-important-labels"],
        )

        assert routing.route_decisions[0].strategy == "direct"
        assert len(routing.labels) == 1
        label = routing.labels[0]
        assert (label.name, round(label.x, 2), round(label.y, 2), label.angle) == (
            "LEFT_IN",
            24.92,
            100.0,
            180,
        )

    def test_minimal_mode_keeps_direct_signal_chain_net_label_free(self) -> None:
        ir = _make_ir(
            [
                ("J1", "Connector_Generic:Conn_01x02"),
                ("R1", "Device:R"),
            ],
            [("LEFT_IN", [("J1", "1"), ("R1", "1")])],
        )
        pin_endpoints = {
            ("J1", "1"): (30.0, 100.0, 0.0),
            ("R1", "1"): (70.0, 100.0, 180.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)

        routing = route_nets(
            ir=ir,
            pin_endpoints=pin_endpoints,
            block_layout=block_layout,
            policy=LABEL_MODE_POLICIES["minimal"],
        )

        assert routing.route_decisions[0].strategy == "direct"
        assert routing.labels == []

    def test_important_mode_still_promotes_label_on_multi_pin_stage_seam(self) -> None:
        ir = _make_ir(
            [
                ("J1", "Connector_Generic:Conn_01x02"),
                ("C5", "Device:C"),
                ("R1", "Device:R"),
            ],
            [("IN_L_AC", [("J1", "1"), ("C5", "1"), ("R1", "1")])],
        )
        pin_endpoints = {
            ("J1", "1"): (20.0, 100.0, 0.0),
            ("C5", "1"): (60.0, 90.0, 180.0),
            ("R1", "1"): (60.0, 110.0, 180.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("C5", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)

        routing = route_nets(
            ir=ir,
            pin_endpoints=pin_endpoints,
            block_layout=block_layout,
            policy=LABEL_MODE_POLICIES["always-show-important-labels"],
        )

        assert routing.route_decisions[0].strategy != "direct"
        assert [label.name for label in routing.labels] == ["IN_L_AC"]

    def test_important_mode_promotes_only_explicit_stage_seam_nets(self) -> None:
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="C5", symbol="Device:C", value="10u"),
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="RV1", symbol="Device:R_Potentiometer", value="10k"),
                ComponentIR(ref="R4", symbol="Device:R", value="10k"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="R2", symbol="Device:R", value="22k"),
                ComponentIR(ref="R3", symbol="Device:R", value="22k"),
                ComponentIR(ref="C6", symbol="Device:C", value="10u"),
                ComponentIR(ref="R5", symbol="Device:R", value="22k"),
                ComponentIR(ref="R6", symbol="Device:R", value="100"),
                ComponentIR(ref="C7", symbol="Device:C", value="220u"),
                ComponentIR(ref="R7", symbol="Device:R", value="10k"),
                ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="Out"),
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
                NetIR(
                    name="VOL_L_OUT",
                    pins=[
                        PinRefIR(ref="RV1", pin="2"),
                        PinRefIR(ref="U1", pin="3"),
                        PinRefIR(ref="R4", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT_L_STAGE1",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="R2", pin="1"),
                        PinRefIR(ref="C6", pin="1"),
                    ],
                ),
                NetIR(
                    name="BUF_L_IN",
                    pins=[
                        PinRefIR(ref="C6", pin="2"),
                        PinRefIR(ref="R5", pin="1"),
                        PinRefIR(ref="U1", pin="5"),
                    ],
                ),
                NetIR(
                    name="HP_L_OUT",
                    pins=[
                        PinRefIR(ref="C7", pin="2"),
                        PinRefIR(ref="R7", pin="1"),
                        PinRefIR(ref="J2", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT_L_STAGE2_RAW",
                    pins=[
                        PinRefIR(ref="U1", pin="7"),
                        PinRefIR(ref="U1", pin="6"),
                        PinRefIR(ref="R6", pin="1"),
                    ],
                ),
                NetIR(
                    name="AFTER_R6",
                    pins=[PinRefIR(ref="R6", pin="2"), PinRefIR(ref="C7", pin="1")],
                ),
                NetIR(
                    name="U1A_INV",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="R2", pin="2"),
                        PinRefIR(ref="R3", pin="1"),
                    ],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("C5", BlockRole.INPUT)
        block_layout.add_assignment("R1", BlockRole.INPUT)
        block_layout.add_assignment("RV1", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R4", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("R2", BlockRole.FEEDBACK)
        block_layout.add_assignment("R3", BlockRole.FEEDBACK)
        block_layout.add_assignment("C6", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R5", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("C7", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("R7", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("J2", BlockRole.OUTPUT)

        pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            ("J1", "1"): (20.0, 80.0, 0.0),
            ("C5", "1"): (40.0, 72.0, 180.0),
            ("R1", "1"): (40.0, 88.0, 180.0),
            ("C5", "2"): (60.0, 72.0, 0.0),
            ("R1", "2"): (60.0, 88.0, 0.0),
            ("RV1", "1"): (80.0, 80.0, 180.0),
            ("RV1", "2"): (100.0, 80.0, 0.0),
            ("U1", "3"): (120.0, 72.0, 180.0),
            ("R4", "1"): (120.0, 88.0, 180.0),
            ("U1", "1"): (140.0, 80.0, 0.0),
            ("R2", "1"): (160.0, 72.0, 180.0),
            ("C6", "1"): (160.0, 88.0, 180.0),
            ("C6", "2"): (180.0, 72.0, 0.0),
            ("R5", "1"): (180.0, 88.0, 0.0),
            ("U1", "5"): (200.0, 80.0, 180.0),
            ("C7", "2"): (220.0, 72.0, 0.0),
            ("R7", "1"): (220.0, 88.0, 0.0),
            ("J2", "1"): (240.0, 80.0, 180.0),
            ("U1", "7"): (260.0, 72.0, 0.0),
            ("U1", "6"): (260.0, 88.0, 0.0),
            ("R6", "1"): (280.0, 80.0, 180.0),
            ("R6", "2"): (300.0, 72.0, 0.0),
            ("C7", "1"): (300.0, 88.0, 180.0),
            ("U1", "2"): (320.0, 80.0, 0.0),
            ("R2", "2"): (340.0, 72.0, 180.0),
            ("R3", "1"): (340.0, 88.0, 180.0),
        }

        routing = route_nets(
            ir=ir,
            pin_endpoints=pin_endpoints,
            block_layout=block_layout,
            policy=LABEL_MODE_POLICIES["always-show-important-labels"],
        )

        label_names = {label.name for label in routing.labels}

        assert {
            "LEFT_IN",
            "IN_L_AC",
            "VOL_L_OUT",
            "OUT_L_STAGE1",
            "BUF_L_IN",
            "HP_L_OUT",
        } <= label_names
        assert {"OUT_L_STAGE2_RAW", "AFTER_R6", "U1A_INV"}.isdisjoint(label_names)

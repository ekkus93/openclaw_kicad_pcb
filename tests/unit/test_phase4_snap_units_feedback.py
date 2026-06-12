"""Phase 4 snap: feedback detection, IC unit groups, and stereo split tests."""

from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.layout import (
    ComponentAnnotation,
    find_feedback_paths,
)
from kicad_pcb.lint import LintSeverity
from kicad_pcb.tier import (
    assign_tiers,
)

_WARN = LintSeverity.WARNING
_ERR = LintSeverity.ERROR


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


def _feedback_ir() -> CircuitIR:
    """Minimal IR containing a feedback resistor.

    Signal chain:  J1 —[NET_IN]— U1 —[NET_OUT]— J2
    Feedback loop: R_fb connects NET_OUT back to NET_IN.

    After assign_tiers the cycle is broken and R_fb ends up at tier 3
    (one step beyond its highest-tier neighbour, U1 at tier 2), which
    leaves all of R_fb's neighbours at strictly lower tiers → feedback.
    """
    return _make_ir(
        [
            ("J1", "Connector_Generic:Conn_01x01"),
            ("U1", "Amplifier_Operational:TL071"),
            ("R_fb", "Device:R"),
            ("J2", "Connector_Generic:Conn_01x01"),
        ],
        [
            # Forward path: J1 → U1 input
            ("NET_IN", [("J1", "1"), ("U1", "3"), ("R_fb", "1")]),
            # U1 output → J2, same net used by R_fb pin2 (creates cycle)
            ("NET_OUT", [("U1", "6"), ("J2", "1"), ("R_fb", "2")]),
        ],
    )


class TestFindFeedbackPaths:
    """Unit tests for :func:`find_feedback_paths`."""

    def test_find_feedback_resistor(self) -> None:
        """R_fb connecting op-amp output net to inverting-input net is feedback."""
        ir = _feedback_ir()
        tiers = assign_tiers(ir)
        result = find_feedback_paths(ir, tiers)

        # The key assertion from the spec.
        assert "R_fb" in result, "R_fb must be in annotations"
        assert result["R_fb"].feedback is True, (
            f"R_fb should be feedback=True; tiers={tiers}, R_fb tier={tiers.get('R_fb')}"
        )

    def test_series_resistor_not_feedback(self) -> None:
        """A plain series resistor between two ICs is NOT feedback."""
        ir = _make_ir(
            [("J1", "Connector_Generic:Conn"), ("R1", "Device:R"), ("U1", "Amplifier:TL071")],
            [
                ("IN", [("J1", "1"), ("R1", "1")]),
                ("MID", [("R1", "2"), ("U1", "3")]),
            ],
        )
        tiers = assign_tiers(ir)
        result = find_feedback_paths(ir, tiers)
        assert result["R1"].feedback is False, "Series resistor must NOT be feedback"

    def test_connector_never_feedback(self) -> None:
        """Connectors are excluded from feedback detection (non-passive)."""
        ir = _feedback_ir()
        tiers = assign_tiers(ir)
        result = find_feedback_paths(ir, tiers)
        assert result["J1"].feedback is False
        assert result["J2"].feedback is False

    def test_ic_never_feedback(self) -> None:
        """ICs are excluded from feedback detection (non-passive)."""
        ir = _feedback_ir()
        tiers = assign_tiers(ir)
        result = find_feedback_paths(ir, tiers)
        assert result["U1"].feedback is False

    def test_all_components_returned(self) -> None:
        """Every component in the IR has an annotation entry."""
        ir = _feedback_ir()
        tiers = assign_tiers(ir)
        result = find_feedback_paths(ir, tiers)
        ir_refs = {c.ref for c in ir.components}
        assert set(result.keys()) == ir_refs

    def test_empty_tiers_does_not_crash(self) -> None:
        """find_feedback_paths accepts an empty tiers dict without error.

        The topological detection algorithm does not require tiers, so
        an empty dict is a valid (if sparse) input.  R_fb is still
        detected because the shared-component criterion is tier-independent.
        """
        ir = _feedback_ir()
        result = find_feedback_paths(ir, {})
        # No TypeError / crash; R_fb is still feedback (topology-driven).
        assert "R_fb" in result
        assert result["R_fb"].feedback is True

    def test_component_annotation_dataclass(self) -> None:
        """ComponentAnnotation defaults to feedback=False."""
        a = ComponentAnnotation()
        assert a.feedback is False
        b = ComponentAnnotation(feedback=True)
        assert b.feedback is True


class TestSnapFeedbackComponents:
    """Unit tests for :func:`graphviz_layout.snap_feedback_components`."""

    def test_feedback_component_placed_above_amp(self) -> None:
        """Feedback component snaps to anchor_y - GRID_ROW_MM."""
        ir = _feedback_ir()
        tiers = assign_tiers(ir)
        annotations = find_feedback_paths(ir, tiers)

        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.0, 80.0, None),
            "U1": (90.0, 80.0, None),
            "R_fb": (60.0, 100.0, None),  # below U1 — should be snapped above
            "J2": (120.0, 80.0, None),
        }
        result = _gv_mod.snap_feedback_components(positions, annotations, ir)

        # R_fb should now be above whichever anchor was found (U1 or J1/J2).
        rfb_y = result["R_fb"][1]
        # Any signal-net neighbour is a valid anchor; the snap places R_fb
        # at anchor_y - GRID_ROW_MM.
        possible_anchors = {"J1": 80.0, "U1": 80.0, "J2": 80.0}
        expected_ys = {y - _gv_mod.GRID_ROW_MM for y in possible_anchors.values()}
        assert rfb_y in expected_ys, (
            f"R_fb y={rfb_y} not a valid snap target; expected one of {expected_ys}"
        )

    def test_non_feedback_component_unchanged(self) -> None:
        """Components with feedback=False are not moved."""
        ir = _feedback_ir()
        tiers = assign_tiers(ir)
        annotations = find_feedback_paths(ir, tiers)

        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.0, 80.0, None),
            "U1": (90.0, 80.0, None),
            "R_fb": (60.0, 100.0, None),
            "J2": (120.0, 80.0, None),
        }
        result = _gv_mod.snap_feedback_components(positions, annotations, ir)
        for ref in ("J1", "U1", "J2"):
            assert result[ref] == positions[ref], f"{ref} must not move"

    def test_x_coordinate_preserved_for_feedback(self) -> None:
        """snap_feedback_components only changes y; x is preserved."""
        ir = _feedback_ir()
        tiers = assign_tiers(ir)
        annotations = find_feedback_paths(ir, tiers)

        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.0, 80.0, None),
            "U1": (90.0, 80.0, None),
            "R_fb": (62.0, 100.0, None),
            "J2": (120.0, 80.0, None),
        }
        result = _gv_mod.snap_feedback_components(positions, annotations, ir)
        assert result["R_fb"][0] == pytest.approx(62.0), "x should not change"

    def test_no_feedback_components_returns_unchanged(self) -> None:
        """When no component is feedback, positions dict is returned as-is."""
        ir = _make_ir(
            [("J1", "Connector_Generic:Conn"), ("R1", "Device:R"), ("U1", "Amp:TL071")],
            [
                ("IN", [("J1", "1"), ("R1", "1")]),
                ("MID", [("R1", "2"), ("U1", "3")]),
            ],
        )
        tiers = assign_tiers(ir)
        annotations = find_feedback_paths(ir, tiers)
        # Sanity: no feedback in a pure series chain.
        assert all(not a.feedback for a in annotations.values())

        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (0.0, 50.0, None),
            "R1": (30.0, 50.0, None),
            "U1": (60.0, 50.0, None),
        }
        result = _gv_mod.snap_feedback_components(positions, annotations, ir)
        assert result == positions


# ---------------------------------------------------------------------------
# Phase 6 — Multi-unit IC handling
# ---------------------------------------------------------------------------


def _multi_unit_ir() -> CircuitIR:
    """Minimal dual-op-amp circuit with a multi-unit IC.

    Signal path: J1 --[NET_IN]--> U1A --[NET_OUT]--> J2.
    Power unit: U1B connected only to VCC and GND.
    """
    return CircuitIR.model_validate(
        {
            "version": "1",
            "components": [
                {"ref": "J1", "symbol": "Connector:Conn_01x01", "value": ""},
                {"ref": "U1A", "symbol": "Amplifier:NE5532", "value": "NE5532"},
                {"ref": "U1B", "symbol": "Amplifier:NE5532", "value": "NE5532"},
                {"ref": "J2", "symbol": "Connector:Conn_01x01", "value": ""},
            ],
            "nets": [
                {"name": "NET_IN", "pins": [{"ref": "J1", "pin": "1"}, {"ref": "U1A", "pin": "3"}]},
                {
                    "name": "NET_OUT",
                    "pins": [{"ref": "U1A", "pin": "1"}, {"ref": "J2", "pin": "1"}],
                },
                {"name": "VCC", "pins": [{"ref": "U1B", "pin": "8"}]},
                {"name": "GND", "pins": [{"ref": "U1B", "pin": "4"}]},
            ],
        }
    )


def _multi_stage_unit_ir() -> CircuitIR:
    """Three-unit op-amp example with two signal stages and one power unit."""
    return CircuitIR.model_validate(
        {
            "version": "1",
            "components": [
                {"ref": "J1", "symbol": "Connector:Conn_01x01", "value": ""},
                {"ref": "U1A", "symbol": "Amplifier:NE5532", "value": "NE5532"},
                {"ref": "U1B", "symbol": "Amplifier:NE5532", "value": "NE5532"},
                {"ref": "U1P", "symbol": "Amplifier:NE5532", "value": "NE5532"},
                {"ref": "J2", "symbol": "Connector:Conn_01x01", "value": ""},
            ],
            "nets": [
                {"name": "NET_IN", "pins": [{"ref": "J1", "pin": "1"}, {"ref": "U1A", "pin": "3"}]},
                {
                    "name": "NET_STAGE",
                    "pins": [{"ref": "U1A", "pin": "1"}, {"ref": "U1B", "pin": "5"}],
                },
                {
                    "name": "NET_OUT",
                    "pins": [{"ref": "U1B", "pin": "7"}, {"ref": "J2", "pin": "1"}],
                },
                {"name": "VCC", "pins": [{"ref": "U1P", "pin": "8"}]},
                {"name": "GND", "pins": [{"ref": "U1P", "pin": "4"}]},
            ],
        }
    )

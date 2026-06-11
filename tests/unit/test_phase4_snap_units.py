"""Phase 4 snap: feedback detection, IC unit groups, and stereo split tests."""

from __future__ import annotations

import re

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.layout import (
    ComponentAnnotation,
    StereoChannel,
    detect_stereo_channels,
    find_feedback_paths,
)
from kicad_pcb.lint import LintSeverity
from kicad_pcb.tier import (
    IcUnitGroup,
    assign_ic_units_to_tiers,
    assign_tiers,
    build_ic_unit_sibling_constraints,
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


class TestIcUnitGroups:
    """Phase 6 — assign_ic_units_to_tiers and per-unit DOT placement."""

    def test_multi_unit_ref_detected(self) -> None:
        """U1A and U1B are grouped under base ref U1."""
        ir = _multi_unit_ir()
        tiers = assign_tiers(ir)
        groups = assign_ic_units_to_tiers(ir, tiers)
        assert "U1" in groups
        assert groups["U1"].units == ["U1A", "U1B"]
        assert groups["U1"].base_ref == "U1"

    def test_power_unit_detected(self) -> None:
        """U1B (only VCC/GND nets) is identified as the power unit."""
        ir = _multi_unit_ir()
        tiers = assign_tiers(ir)
        groups = assign_ic_units_to_tiers(ir, tiers)
        assert groups["U1"].power_unit == "U1B"

    def test_single_unit_ic_excluded(self) -> None:
        """A plain 'U1' ref (no letter suffix) produces no group entry."""
        ir = _make_ir(
            [("J1", "Connector"), ("U1", "Amp:TL071"), ("J2", "Connector")],
            [("NET_IN", [("J1", "1"), ("U1", "3")]), ("NET_OUT", [("U1", "1"), ("J2", "1")])],
        )
        groups = assign_ic_units_to_tiers(ir, assign_tiers(ir))
        assert groups == {}

    def test_passive_ref_with_letter_suffix_excluded(self) -> None:
        """R1A is a passive prefix; it must not be treated as a multi-unit IC."""
        ir = _make_ir(
            [("J1", "Connector"), ("R1A", "Device:R"), ("J2", "Connector")],
            [("NET", [("J1", "1"), ("R1A", "1"), ("J2", "1")])],
        )
        groups = assign_ic_units_to_tiers(ir, assign_tiers(ir))
        assert groups == {}

    def test_ic_unit_group_dataclass_defaults(self) -> None:
        """IcUnitGroup default values are correct."""
        g = IcUnitGroup(base_ref="U2")
        assert g.units == []
        assert g.power_unit is None

    def test_multi_unit_ic_power_unit_in_power_cluster(self) -> None:  # spec test
        """DOT source places U1B (power unit) inside cluster_power subgraph."""
        ir = _multi_unit_ir()
        tiers = assign_tiers(ir)
        groups = assign_ic_units_to_tiers(ir, tiers)
        power_unit_refs = {g.power_unit for g in groups.values() if g.power_unit is not None}
        dot = _gv_mod._build_dot_source(ir, power_unit_refs=power_unit_refs)
        # cluster_power must exist and contain U1B.
        assert "cluster_power" in dot
        cluster_start = dot.index("cluster_power")
        cluster_end = dot.index("}", cluster_start)
        cluster_body = dot[cluster_start:cluster_end]
        assert "U1B" in cluster_body

    def test_multi_unit_ic_signal_units_in_signal_tiers(self) -> None:  # spec test
        """DOT source places U1A (signal unit) in a rank=same tier subgraph."""
        ir = _multi_unit_ir()
        tiers = assign_tiers(ir)
        groups = assign_ic_units_to_tiers(ir, tiers)
        power_unit_refs = {g.power_unit for g in groups.values() if g.power_unit is not None}
        dot = _gv_mod._build_dot_source(ir, power_unit_refs=power_unit_refs)
        # U1A must appear in a rank=... subgraph (rank=source, rank=same, or rank=sink).
        rank_blocks = re.findall(r"\{[^{}]*rank=(?:same|source|sink)[^{}]*\}", dot, re.DOTALL)
        assert any("U1A" in block for block in rank_blocks), (
            f"U1A not found in any rank subgraph.\nDOT:\n{dot}"
        )

    def test_power_unit_not_in_signal_tiers(self) -> None:
        """U1B must not appear in any rank=same/source/sink subgraph."""
        ir = _multi_unit_ir()
        tiers = assign_tiers(ir)
        groups = assign_ic_units_to_tiers(ir, tiers)
        power_unit_refs = {g.power_unit for g in groups.values() if g.power_unit is not None}
        dot = _gv_mod._build_dot_source(ir, power_unit_refs=power_unit_refs)
        rank_blocks = re.findall(r"\{[^{}]*rank=(?:same|source|sink)[^{}]*\}", dot, re.DOTALL)
        assert not any("U1B" in block for block in rank_blocks), (
            f"U1B must not be in a tier subgraph.\nDOT:\n{dot}"
        )

    def test_power_unit_excluded_from_tiers_even_with_affinity_order(self) -> None:
        """Affinity ordering must not reinsert power units into rank subgraphs."""
        ir = _multi_stage_unit_ir()
        tiers = assign_tiers(ir)
        groups = assign_ic_units_to_tiers(ir, tiers)
        power_unit_refs = {g.power_unit for g in groups.values() if g.power_unit is not None}

        dot = _gv_mod._build_dot_source(
            ir,
            power_unit_refs=power_unit_refs,
            tiers=tiers,
            affinity_order={0: ["J1", "U1P"], 1: ["U1A", "U1B"]},
        )

        rank_blocks = re.findall(r"\{[^{}]*rank=(?:same|source|sink)[^{}]*\}", dot, re.DOTALL)
        assert not any("U1P" in block for block in rank_blocks), (
            f"U1P leaked back into a tier subgraph via affinity ordering.\nDOT:\n{dot}"
        )

    def test_signal_sibling_constraints_skip_power_units(self) -> None:
        """Only signal units participate in sibling-order constraints."""
        ir = _multi_stage_unit_ir()
        groups = assign_ic_units_to_tiers(ir, assign_tiers(ir))

        assert groups["U1"].signal_units == ["U1A", "U1B"]
        assert build_ic_unit_sibling_constraints(groups) == [("U1A", "U1B")]

    def test_signal_sibling_constraint_emitted_in_dot(self) -> None:
        """DOT source adds an invisible U1A->U1B constraint but excludes U1P."""
        ir = _multi_stage_unit_ir()
        tiers = assign_tiers(ir)
        groups = assign_ic_units_to_tiers(ir, tiers)
        power_unit_refs = {g.power_unit for g in groups.values() if g.power_unit is not None}
        sibling_pairs = build_ic_unit_sibling_constraints(groups)

        dot = _gv_mod._build_dot_source(
            ir,
            power_unit_refs=power_unit_refs,
            unit_sibling_pairs=sibling_pairs,
            tiers=tiers,
        )

        assert "U1A -> U1B [style=invis, weight=4, constraint=false];" in dot
        assert "U1P -> U1A" not in dot
        assert "U1A -> U1P" not in dot
        assert "U1P -> U1B" not in dot
        assert "U1B -> U1P" not in dot


# ---------------------------------------------------------------------------
# Phase 7 — Stereo symmetry
# ---------------------------------------------------------------------------


def _stereo_ir() -> CircuitIR:
    """Minimal stereo headphone amp circuit with distinct L/R channel nets.

    Signal paths:
      J1 --[IN_L]--> R1 --[MID_L]--> U1 --[OUT_L]--> J2   (left channel)
      J3 --[IN_R]--> R2 --[MID_R]--> U2 --[OUT_R]--> J4   (right channel)
      J5 shared (power connector, no stereo suffix)        (mono)
    """
    return CircuitIR.model_validate(
        {
            "version": "1",
            "components": [
                {"ref": "J1", "symbol": "Connector:Conn", "value": ""},
                {"ref": "R1", "symbol": "Device:R", "value": "10k"},
                {"ref": "U1", "symbol": "Amplifier:TL071", "value": "TL071"},
                {"ref": "J2", "symbol": "Connector:Conn", "value": ""},
                {"ref": "J3", "symbol": "Connector:Conn", "value": ""},
                {"ref": "R2", "symbol": "Device:R", "value": "10k"},
                {"ref": "U2", "symbol": "Amplifier:TL071", "value": "TL071"},
                {"ref": "J4", "symbol": "Connector:Conn", "value": ""},
                {"ref": "J5", "symbol": "Connector:Conn", "value": ""},
            ],
            "nets": [
                {"name": "IN_L", "pins": [{"ref": "J1", "pin": "1"}, {"ref": "R1", "pin": "1"}]},
                {"name": "MID_L", "pins": [{"ref": "R1", "pin": "2"}, {"ref": "U1", "pin": "3"}]},
                {"name": "OUT_L", "pins": [{"ref": "U1", "pin": "1"}, {"ref": "J2", "pin": "1"}]},
                {"name": "IN_R", "pins": [{"ref": "J3", "pin": "1"}, {"ref": "R2", "pin": "1"}]},
                {"name": "MID_R", "pins": [{"ref": "R2", "pin": "2"}, {"ref": "U2", "pin": "3"}]},
                {"name": "OUT_R", "pins": [{"ref": "U2", "pin": "1"}, {"ref": "J4", "pin": "1"}]},
                # J5 on a non-stereo net (mono)
                {
                    "name": "POWER",
                    "pins": [{"ref": "J5", "pin": "1"}, {"ref": "U1", "pin": "8"}],
                },
            ],
        }
    )


class TestDetectStereoChannels:
    """Phase 7 — detect_stereo_channels."""

    def test_detect_stereo_channels_from_net_suffix(self) -> None:  # spec test
        """Components on _L nets → L; on _R nets → R; mixed/none → mono."""
        ir = _stereo_ir()
        channels = detect_stereo_channels(ir)
        assert channels["J1"] == "L"
        assert channels["R1"] == "L"
        assert channels["U1"] == "L"  # only L nets in signal chain
        assert channels["J2"] == "L"
        assert channels["J3"] == "R"
        assert channels["R2"] == "R"
        assert channels["U2"] == "R"
        assert channels["J4"] == "R"

    def test_mono_component_on_mixed_nets(self) -> None:
        """A component on both _L and _R signal nets is classified as mono."""
        ir = _make_ir(
            [("J1", "Connector"), ("R1", "Device:R"), ("J2", "Connector")],
            [
                ("NET_L", [("J1", "1"), ("R1", "1")]),
                ("NET_R", [("R1", "2"), ("J2", "1")]),
            ],
        )
        channels = detect_stereo_channels(ir)
        assert channels["R1"] == "mono"  # has both L and R nets

    def test_dash_suffix_recognised(self) -> None:
        """Nets ending in -L / -R (dash separator) are detected correctly."""
        ir = _make_ir(
            [("J1", "Connector"), ("R1", "Device:R")],
            [("OUT-L", [("J1", "1"), ("R1", "1")])],
        )
        channels = detect_stereo_channels(ir)
        assert channels["J1"] == "L"
        assert channels["R1"] == "L"

    def test_all_components_present_in_result(self) -> None:
        """Every component in ir is represented in the returned dict."""
        ir = _stereo_ir()
        channels = detect_stereo_channels(ir)
        for comp in ir.components:
            assert comp.ref in channels

    def test_no_stereo_nets_all_mono(self) -> None:
        """With no stereo-suffix nets, every component is mono."""
        ir = _make_ir(
            [("J1", "Connector"), ("R1", "Device:R"), ("J2", "Connector")],
            [("NET", [("J1", "1"), ("R1", "1"), ("J2", "1")])],
        )
        channels = detect_stereo_channels(ir)
        assert all(v == "mono" for v in channels.values())

    def test_stereo_channel_type_alias(self) -> None:
        """StereoChannel is exported from layout and is a Literal type alias."""
        # Runtime check: the values returned are valid StereoChannel literals.
        ir = _stereo_ir()
        channels = detect_stereo_channels(ir)
        valid: set[StereoChannel] = {"L", "R", "mono"}
        assert all(v in valid for v in channels.values())


class TestApplyStereoSplit:
    """Phase 7 — _apply_stereo_split post-layout y remapping."""

    # Shared page constants matching graphviz_layout defaults.
    _ORIGIN_Y: float = _gv_mod.ORIGIN_Y
    _PAGE_MAX_Y: float = _gv_mod.PAGE_MAX_Y
    _PAGE_H: float = _gv_mod.PAGE_MAX_Y - _gv_mod.ORIGIN_Y

    def _mid(self) -> float:
        return self._ORIGIN_Y + self._PAGE_H * 0.5

    def test_left_channel_above_midline(self) -> None:  # spec test
        """L-channel components land in the top half (y < midline)."""
        channels: dict[str, str] = {"J1": "L", "R1": "L"}
        # Give them y values spread across the usable page.
        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.0, self._ORIGIN_Y + 10.0, None),
            "R1": (60.0, self._ORIGIN_Y + 60.0, None),
        }
        result = _gv_mod.apply_stereo_split(
            positions, channels, origin_y=self._ORIGIN_Y, page_max_y=self._PAGE_MAX_Y
        )
        midline = self._mid()
        assert result["J1"][1] < midline, f"J1 y={result['J1'][1]} not above midline {midline}"
        assert result["R1"][1] < midline, f"R1 y={result['R1'][1]} not above midline {midline}"

    def test_right_channel_below_midline(self) -> None:  # spec test
        """R-channel components land in the bottom half (y ≥ midline)."""
        channels: dict[str, str] = {"R2": "R", "U2": "R"}
        positions: dict[str, tuple[float, float, float | None]] = {
            "R2": (30.0, self._ORIGIN_Y + 10.0, None),
            "U2": (60.0, self._ORIGIN_Y + 60.0, None),
        }
        result = _gv_mod.apply_stereo_split(
            positions, channels, origin_y=self._ORIGIN_Y, page_max_y=self._PAGE_MAX_Y
        )
        midline = self._mid()
        assert result["R2"][1] >= midline, f"R2 y={result['R2'][1]} not below midline {midline}"
        assert result["U2"][1] >= midline, f"U2 y={result['U2'][1]} not below midline {midline}"

    def test_mono_component_y_unchanged(self) -> None:
        """Mono components keep their original y position."""
        channels: dict[str, str] = {"J5": "mono", "R_shared": "L"}
        original_y = self._ORIGIN_Y + 30.0
        positions: dict[str, tuple[float, float, float | None]] = {
            "J5": (10.0, original_y, None),
            "R_shared": (20.0, self._ORIGIN_Y + 20.0, None),
        }
        result = _gv_mod.apply_stereo_split(
            positions, channels, origin_y=self._ORIGIN_Y, page_max_y=self._PAGE_MAX_Y
        )
        assert result["J5"][1] == pytest.approx(original_y)

    def test_no_stereo_channels_returns_unchanged(self) -> None:
        """When no L/R channels exist, positions are returned as-is."""
        channels: dict[str, str] = {"J1": "mono", "R1": "mono"}
        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.0, 80.0, None),
            "R1": (60.0, 100.0, None),
        }
        result = _gv_mod.apply_stereo_split(
            positions, channels, origin_y=self._ORIGIN_Y, page_max_y=self._PAGE_MAX_Y
        )
        assert result is positions  # fast-path: same object returned

    def test_x_coordinate_preserved(self) -> None:
        """apply_stereo_split only changes y; x and rotation are preserved."""
        channels: dict[str, str] = {"R1": "L", "R2": "R"}
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (42.5, self._ORIGIN_Y + 40.0, 0.0),
            "R2": (80.0, self._ORIGIN_Y + 40.0, 90.0),
        }
        result = _gv_mod.apply_stereo_split(
            positions, channels, origin_y=self._ORIGIN_Y, page_max_y=self._PAGE_MAX_Y
        )
        assert result["R1"][0] == pytest.approx(42.5)
        assert result["R1"][2] == pytest.approx(0.0)
        assert result["R2"][0] == pytest.approx(80.0)
        assert result["R2"][2] == pytest.approx(90.0)


# ---------------------------------------------------------------------------
# Phase 5 — _apply_post_layout_snaps coordinator
# ---------------------------------------------------------------------------

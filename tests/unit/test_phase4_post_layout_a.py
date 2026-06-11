"""Phase 4: post-layout snap pass orchestration — basic ordering, multi-unit
sibling cohesion, buffer stage, and feedback fallback tests.
"""

from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.layout import (
    GRID_COL_MM,
    ComponentAnnotation,
)
from kicad_pcb.lint import LintSeverity
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


# ---------------------------------------------------------------------------
# 4.1 / 4.2  LayoutEngine factory — NoneLayoutEngine
# ---------------------------------------------------------------------------


class TestApplyPostLayoutSnaps_Basic:
    """Tests for :func:`apply_post_layout_snaps` in graphviz_layout."""

    def _simple_ir(self) -> CircuitIR:
        """Minimal IR: one connector, one resistor, one power symbol."""
        return CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="#PWR01", symbol="power:VCC", value="VCC"),
            ],
            nets=[
                NetIR(
                    name="NET",
                    pins=[
                        PinRefIR(ref="J1", pin="1"),
                        PinRefIR(ref="R1", pin="1"),
                    ],
                ),
                NetIR(
                    name="VCC",
                    pins=[
                        PinRefIR(ref="R1", pin="2"),
                        PinRefIR(ref="#PWR01", pin="1"),
                    ],
                ),
            ],
        )

    def test_snap_order_power_before_feedback(self) -> None:
        """Power snap must run before feedback snap.

        A ``#PWR`` VCC symbol must be clamped to ``ORIGIN_Y`` by the power
        snap pass even when a feedback ref shares the same x-column.  The
        feedback snap only moves non-``#PWR`` refs, so the power-snap result
        is preserved.
        """
        ir = self._simple_ir()
        # Place #PWR01 at a y somewhere in the middle of the page.
        positions: dict[str, tuple[float, float, float | None]] = {
            "#PWR01": (50.0, 120.0, None),
            "R1": (50.0, 100.0, None),
            "J1": (30.48, 80.0, None),
        }
        # Treat R1 as a feedback ref so the feedback pass attempts to move it.
        annotations = {"R1": ComponentAnnotation(feedback=True)}
        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"R1"},
            annotations=annotations,
            channels={"J1": "mono", "R1": "mono", "#PWR01": "mono"},
            decoupling_map={},
        )
        # Power snap: #PWR01 (VCC) → top row = ORIGIN_Y.
        assert result["#PWR01"][1] == pytest.approx(_gv_mod.ORIGIN_Y), (
            f"#PWR01 must be clamped to ORIGIN_Y={_gv_mod.ORIGIN_Y} by power snap, "
            f"got {result['#PWR01'][1]}"
        )

    def test_snap_skips_empty_feedback_refs(self) -> None:
        """Passing feedback_refs=set() must not raise and must return valid positions."""
        ir = self._simple_ir()
        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.48, 80.0, None),
            "R1": (50.0, 100.0, None),
        }
        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={"J1": "mono", "R1": "mono"},
            decoupling_map={},
        )
        # All refs must still be present; no exception raised.
        assert set(result.keys()) == {"J1", "R1"}

    def test_snap_skips_mono_channels(self) -> None:
        """All-mono channels must not trigger a stereo split.

        With no L/R channels present, ``_apply_stereo_split`` is skipped.
        However, ``_snap_connectors_to_ic_y`` (Rule 2) still runs and snaps
        J1's y-coordinate to the median y of its signal-net neighbours (R1).
        """
        ir = self._simple_ir()
        # Place components at exact grid positions.
        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.48, 50.80, None),
            "R1": (50.80, 76.20, None),
        }
        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={"J1": "mono", "R1": "mono"},
            decoupling_map={},
        )
        # No stereo split — R1 (non-connector) y stays unchanged.
        assert result["R1"][1] == pytest.approx(positions["R1"][1])
        # Connector J1 is snapped to R1's y by _snap_connectors_to_ic_y (Rule 2).
        assert result["J1"][1] == pytest.approx(76.20)

    def test_snap_compacts_multi_unit_signal_siblings_and_recenters_power_unit(self) -> None:
        """Final post-snap layout keeps split-unit siblings as one compact cluster."""
        ir = _multi_stage_unit_ir()
        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.48, 101.60, None),
            "U1A": (50.80, 101.60, None),
            "U1B": (127.00, 127.00, None),
            "U1P": (203.20, 63.50, None),
            "J2": (228.60, 127.00, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            power_unit_refs=frozenset({"U1P"}),
            unit_sibling_pairs=(("U1A", "U1B"),),
        )

        assert result["U1A"][0] < result["U1B"][0]
        assert result["U1B"][0] - result["U1A"][0] == pytest.approx(GRID_COL_MM)

        sibling_center_x = (result["U1A"][0] + result["U1B"][0]) / 2.0
        assert result["U1P"][0] == pytest.approx(sibling_center_x)

    def test_snap_multi_unit_sibling_cohesion_skips_incomplete_position_sets(self) -> None:
        """Missing sibling refs should not raise or disturb unrelated refs."""
        ir = _multi_stage_unit_ir()
        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.48, 101.60, None),
            "U1A": (50.80, 101.60, None),
            "U1P": (203.20, 63.50, None),
            "J2": (228.60, 127.00, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            power_unit_refs=frozenset({"U1P"}),
            unit_sibling_pairs=(("U1A", "U1B"),),
        )

        assert set(result) == set(positions)
        assert result["U1A"][0] == pytest.approx(positions["U1A"][0])
        assert result["U1P"][0] == pytest.approx(positions["U1P"][0])

    def test_late_transition_subbands_do_not_break_multi_unit_signal_cohesion(self) -> None:
        """Final transition-band ordering must not pull split signal units apart."""
        ir = _multi_stage_unit_ir()
        block_layout = BlockLayout()
        block_layout.add_assignment("U1A", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("U1B", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("C6", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("J2", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.48, 101.60, None),
            "U1A": (97.79, 91.44, None),
            "U1B": (171.45, 146.05, None),
            "U1P": (113.03, 124.46, None),
            "C6": (134.62, 153.67, None),
            "R6": (208.27, 168.91, None),
            "J2": (245.11, 121.92, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            power_unit_refs=frozenset({"U1P"}),
            unit_sibling_pairs=(("U1A", "U1B"),),
            block_layout=block_layout,
        )

        assert result["U1A"][0] < result["U1B"][0]
        assert result["U1B"][0] - result["U1A"][0] == pytest.approx(GRID_COL_MM)

        sibling_center_x = (result["U1A"][0] + result["U1B"][0]) / 2.0
        assert result["U1P"][0] == pytest.approx(sibling_center_x)

    def test_multi_stage_opamp_chain_stays_on_readable_signal_band(self) -> None:
        """Phase 2.2: split op-amp stages should read as one horizontal analog chain."""
        ir = _multi_stage_unit_ir()
        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("U1A", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("C6", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R5", BlockRole.INTERSTAGE)
        block_layout.add_assignment("U1B", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("J2", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.48, 121.92, None),
            "U1A": (97.79, 91.44, None),
            "C6": (134.62, 153.67, None),
            "R5": (134.62, 161.29, None),
            "U1B": (171.45, 146.05, None),
            "R6": (208.27, 168.91, None),
            "J2": (245.11, 121.92, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        main_band_refs = ("U1A", "C6", "R5", "U1B")
        main_band_ys = [result[ref][1] for ref in main_band_refs]
        assert max(main_band_ys) - min(main_band_ys) <= 2.0 * _gv_mod.GRID_ROW_MM, (
            f"Multi-stage op-amp chain should stay on one readable horizontal band: {result}"
        )
        assert result["U1A"][0] < result["C6"][0] <= result["R5"][0] <= result["U1B"][0], (
            f"Interstage handoff should stay between the two op-amp stages: {result}"
        )

    def test_buffer_stage_keeps_direct_output_support_on_short_main_row(self) -> None:
        """Phase 2.2: a unity-gain buffer should keep its direct output loop obvious."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1A", symbol="Amplifier:NE5532", value="NE5532"),
                ComponentIR(ref="C6", symbol="Device:C", value="22u"),
                ComponentIR(ref="R5", symbol="Device:R", value="100k"),
                ComponentIR(ref="U1B", symbol="Amplifier:NE5532", value="NE5532"),
                ComponentIR(ref="R6", symbol="Device:R", value="47"),
                ComponentIR(ref="C7", symbol="Device:C", value="220u"),
                ComponentIR(ref="J2", symbol="Connector:Conn_01x01", value="OUT"),
            ],
            nets=[
                NetIR(
                    name="OUT_STAGE1",
                    pins=[PinRefIR(ref="U1A", pin="1"), PinRefIR(ref="C6", pin="1")],
                ),
                NetIR(
                    name="BUF_L_IN",
                    pins=[
                        PinRefIR(ref="C6", pin="2"),
                        PinRefIR(ref="R5", pin="1"),
                        PinRefIR(ref="U1B", pin="5"),
                    ],
                ),
                NetIR(
                    name="OUT_L_STAGE2_RAW",
                    pins=[
                        PinRefIR(ref="U1B", pin="6"),
                        PinRefIR(ref="U1B", pin="7"),
                        PinRefIR(ref="R6", pin="1"),
                    ],
                ),
                NetIR(
                    name="AFTER_R6",
                    pins=[PinRefIR(ref="R6", pin="2"), PinRefIR(ref="C7", pin="1")],
                ),
                NetIR(
                    name="HP_L_OUT",
                    pins=[PinRefIR(ref="C7", pin="2"), PinRefIR(ref="J2", pin="1")],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("U1A", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("C6", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R5", BlockRole.INTERSTAGE)
        block_layout.add_assignment("U1B", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("C7", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("J2", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "U1A": (97.79, 91.44, None),
            "C6": (113.03, 129.54, None),
            "R5": (120.65, 137.16, None),
            "U1B": (171.45, 144.78, None),
            "R6": (208.27, 175.26, None),
            "C7": (208.27, 137.16, None),
            "J2": (245.11, 121.92, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        buffer_band_refs = ("C6", "R5", "U1B", "R6")
        buffer_band_ys = [result[ref][1] for ref in buffer_band_refs]

        assert max(buffer_band_ys) - min(buffer_band_ys) <= _gv_mod.GRID_ROW_MM + 1e-4, (
            f"Buffer handoff and direct output support should stay on one short row: {result}"
        )
        assert result["C6"][0] <= result["R5"][0] <= result["U1B"][0] < result["R6"][0], (
            f"Buffer row should read left-to-right handoff into the unity-gain stage: {result}"
        )
        assert result["R6"][0] - result["U1B"][0] <= 2.0 * GRID_COL_MM, (
            f"Direct buffer output support should stay close to U1B: {result}"
        )

    def test_buffer_stage_shapes_input_handoff_as_bridge_plus_shunt_node(self) -> None:
        """Phase 2.2.2: the buffer input should read as handoff-on-row plus shunt-below."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1A", symbol="Amplifier:NE5532", value="NE5532"),
                ComponentIR(ref="C6", symbol="Device:C", value="22u"),
                ComponentIR(ref="R5", symbol="Device:R", value="100k"),
                ComponentIR(ref="U1B", symbol="Amplifier:NE5532", value="NE5532"),
                ComponentIR(ref="R6", symbol="Device:R", value="47"),
            ],
            nets=[
                NetIR(
                    name="OUT_STAGE1",
                    pins=[PinRefIR(ref="U1A", pin="1"), PinRefIR(ref="C6", pin="1")],
                ),
                NetIR(
                    name="BUF_L_IN",
                    pins=[
                        PinRefIR(ref="C6", pin="2"),
                        PinRefIR(ref="R5", pin="1"),
                        PinRefIR(ref="U1B", pin="5"),
                    ],
                ),
                NetIR(name="GND", pins=[PinRefIR(ref="R5", pin="2")]),
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

        block_layout = BlockLayout()
        block_layout.add_assignment("U1A", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("C6", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R5", BlockRole.INTERSTAGE)
        block_layout.add_assignment("U1B", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)

        positions: dict[str, tuple[float, float, float | None]] = {
            "U1A": (97.79, 91.44, None),
            "C6": (105.41, 129.54, None),
            "R5": (117.00, 137.16, None),
            "U1B": (171.45, 144.78, None),
            "R6": (208.27, 175.26, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        u1b_x, u1b_y, _ = result["U1B"]
        c6_x, c6_y, _ = result["C6"]
        r5_x, r5_y, _ = result["R5"]

        assert c6_x == pytest.approx(r5_x), (
            f"Buffer handoff bridge and shunt should share one input-node column: {result}"
        )
        assert u1b_x - c6_x == pytest.approx(GRID_COL_MM / 2.0), (
            f"Buffer input node should sit midway between the handoff and U1B: {result}"
        )
        assert c6_y == pytest.approx(u1b_y), (
            f"Incoming buffer handoff should stay on the U1B stage row: {result}"
        )
        assert r5_y == pytest.approx(u1b_y + _gv_mod.GRID_ROW_MM), (
            f"Local shunt support should hang one row below the U1B input node: {result}"
        )

    def test_buffer_stage_keeps_output_tail_as_compact_right_side_chain(self) -> None:
        """Phase 2.2.3: the U1B output tail should stay compact below the buffer row."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1B", symbol="Amplifier:NE5532", value="NE5532"),
                ComponentIR(ref="R6", symbol="Device:R", value="47"),
                ComponentIR(ref="C7", symbol="Device:C", value="220u"),
                ComponentIR(ref="R7", symbol="Device:R", value="33"),
                ComponentIR(ref="J2", symbol="Connector:Conn_01x01", value="OUT"),
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
                NetIR(
                    name="AFTER_R6",
                    pins=[PinRefIR(ref="R6", pin="2"), PinRefIR(ref="C7", pin="1")],
                ),
                NetIR(
                    name="HP_L_OUT",
                    pins=[
                        PinRefIR(ref="C7", pin="2"),
                        PinRefIR(ref="R7", pin="1"),
                        PinRefIR(ref="J2", pin="1"),
                    ],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("U1B", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("C7", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("R7", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("J2", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "U1B": (171.45, 144.78, None),
            "R6": (208.27, 175.26, None),
            "C7": (246.38, 205.74, None),
            "R7": (262.89, 220.98, None),
            "J2": (297.18, 205.74, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        tail_refs = ("C7", "R7", "J2")
        tail_ys = [result[ref][1] for ref in tail_refs]

        assert max(tail_ys) - min(tail_ys) <= _gv_mod.GRID_ROW_MM, (
            f"Output tail should read as one compact right-side chain: {result}"
        )
        assert min(tail_ys) > result["R6"][1], (
            "Output tail should stay below the fixed buffer row instead of "
            f"collapsing onto it: {result}"
        )
        assert result["R6"][0] < result["C7"][0] <= result["R7"][0] <= result["J2"][0], (
            f"Output tail should stay ordered to the right of the direct buffer support: {result}"
        )
        assert result["J2"][0] - result["R6"][0] <= 3.0 * GRID_COL_MM, (
            f"Output tail should stay local to U1B instead of stretching rightward: {result}"
        )

    def test_buffer_stage_keeps_output_connector_on_tail_row_and_outermost_lane(self) -> None:
        """Phase 2.4.3: the output connector should stay attached to the final tail row."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1B", symbol="Amplifier:NE5532", value="NE5532"),
                ComponentIR(ref="R6", symbol="Device:R", value="47"),
                ComponentIR(ref="C7", symbol="Device:C", value="220u"),
                ComponentIR(ref="R7", symbol="Device:R", value="33"),
                ComponentIR(ref="J2", symbol="Connector:Conn_01x01", value="OUT"),
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
                NetIR(
                    name="AFTER_R6",
                    pins=[PinRefIR(ref="R6", pin="2"), PinRefIR(ref="C7", pin="1")],
                ),
                NetIR(
                    name="HP_L_OUT",
                    pins=[
                        PinRefIR(ref="C7", pin="2"),
                        PinRefIR(ref="R7", pin="1"),
                        PinRefIR(ref="J2", pin="1"),
                    ],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("U1B", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("C7", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("R7", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("J2", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "U1B": (171.45, 144.78, None),
            "R6": (208.27, 175.26, None),
            "C7": (246.38, 205.74, None),
            "R7": (262.89, 220.98, None),
            "J2": (297.18, 205.74, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        assert result["J2"][1] == pytest.approx(result["C7"][1]), (
            f"Output connector should stay on the coupling-cap tail row: {result}"
        )
        assert result["J2"][1] == pytest.approx(result["R7"][1]), (
            "Output connector should stay attached to the final "
            f"resistor/capacitor tail row: {result}"
        )
        assert result["R7"][0] < result["J2"][0], (
            f"Output connector should remain the outermost element on the output-tail row: {result}"
        )

    def test_buffer_stage_clears_feedback_corridor_of_intrusive_tail_parts(self) -> None:
        """Phase 2.2.2: downstream output parts should not sit inside the U1B loop corridor."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1B", symbol="Amplifier:NE5532", value="NE5532"),
                ComponentIR(ref="R6", symbol="Device:R", value="47"),
                ComponentIR(ref="C7", symbol="Device:C", value="220u"),
                ComponentIR(ref="R7", symbol="Device:R", value="33"),
                ComponentIR(ref="J2", symbol="Connector:Conn_01x01", value="OUT"),
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
                NetIR(
                    name="AFTER_R6",
                    pins=[PinRefIR(ref="R6", pin="2"), PinRefIR(ref="C7", pin="1")],
                ),
                NetIR(
                    name="HP_L_OUT",
                    pins=[
                        PinRefIR(ref="C7", pin="2"),
                        PinRefIR(ref="R7", pin="1"),
                        PinRefIR(ref="J2", pin="1"),
                    ],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("U1B", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("C7", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("R7", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("J2", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "U1B": (171.45, 144.78, None),
            "R6": (208.27, 175.26, None),
            "C7": (190.50, 137.16, None),
            "R7": (198.12, 129.54, None),
            "J2": (236.22, 137.16, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        assert result["C7"][1] > result["R6"][1], (
            "An intrusive downstream cap should be pushed below the fixed buffer row instead of "
            f"sitting inside the U1B loop corridor: {result}"
        )
        assert result["R6"][0] < result["C7"][0] <= result["R7"][0] <= result["J2"][0], (
            "Once evacuated from the corridor, the downstream tail should resume the normal "
            f"right-side order: {result}"
        )

    def test_feedback_node_stays_compact_after_late_stage2_locality_passes(self) -> None:
        """Phase 2.2.4: late U1B locality passes must not stretch the U1A feedback node."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1A", symbol="Amplifier:NE5532", value="NE5532"),
                ComponentIR(ref="R2", symbol="Device:R", value="22k"),
                ComponentIR(ref="R3", symbol="Device:R", value="2.2k"),
                ComponentIR(ref="C6", symbol="Device:C", value="22u"),
                ComponentIR(ref="R5", symbol="Device:R", value="100k"),
                ComponentIR(ref="U1B", symbol="Amplifier:NE5532", value="NE5532"),
                ComponentIR(ref="R6", symbol="Device:R", value="47"),
                ComponentIR(ref="C7", symbol="Device:C", value="220u"),
                ComponentIR(ref="R7", symbol="Device:R", value="33"),
                ComponentIR(ref="J2", symbol="Connector:Conn_01x01", value="OUT"),
            ],
            nets=[
                NetIR(
                    name="U1A_INV",
                    pins=[
                        PinRefIR(ref="U1A", pin="2"),
                        PinRefIR(ref="R2", pin="1"),
                        PinRefIR(ref="R3", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT_STAGE1",
                    pins=[
                        PinRefIR(ref="U1A", pin="1"),
                        PinRefIR(ref="R2", pin="2"),
                        PinRefIR(ref="C6", pin="1"),
                    ],
                ),
                NetIR(name="GND", pins=[PinRefIR(ref="R3", pin="2")]),
                NetIR(
                    name="BUF_L_IN",
                    pins=[
                        PinRefIR(ref="C6", pin="2"),
                        PinRefIR(ref="R5", pin="1"),
                        PinRefIR(ref="U1B", pin="5"),
                    ],
                ),
                NetIR(
                    name="OUT_L_STAGE2_RAW",
                    pins=[
                        PinRefIR(ref="U1B", pin="6"),
                        PinRefIR(ref="U1B", pin="7"),
                        PinRefIR(ref="R6", pin="1"),
                    ],
                ),
                NetIR(
                    name="AFTER_R6",
                    pins=[PinRefIR(ref="R6", pin="2"), PinRefIR(ref="C7", pin="1")],
                ),
                NetIR(
                    name="HP_L_OUT",
                    pins=[
                        PinRefIR(ref="C7", pin="2"),
                        PinRefIR(ref="R7", pin="1"),
                        PinRefIR(ref="J2", pin="1"),
                    ],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("U1A", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("R2", BlockRole.FEEDBACK)
        block_layout.add_assignment("R3", BlockRole.FEEDBACK)
        block_layout.add_assignment("C6", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R5", BlockRole.INTERSTAGE)
        block_layout.add_assignment("U1B", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("C7", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("R7", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("J2", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "U1A": (97.79, 91.44, None),
            "R2": (150.0, 60.0, None),
            "R3": (152.0, 170.0, None),
            "C6": (134.62, 153.67, None),
            "R5": (134.62, 161.29, None),
            "U1B": (171.45, 146.05, None),
            "R6": (208.27, 168.91, None),
            "C7": (246.38, 205.74, None),
            "R7": (262.89, 220.98, None),
            "J2": (297.18, 205.74, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"R2", "R3"},
            annotations={
                "R2": ComponentAnnotation(feedback=True),
                "R3": ComponentAnnotation(feedback=True),
            },
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        u1a_x, u1a_y, _ = result["U1A"]
        r2_x, r2_y, _ = result["R2"]
        r3_x, r3_y, _ = result["R3"]

        assert r2_x == pytest.approx(r3_x), (
            f"Feedback bridge and shunt should share one compact node column: {result}"
        )
        assert u1a_x - r2_x <= GRID_COL_MM, (
            f"Feedback node should stay close to U1A instead of stretching leftward: {result}"
        )
        assert r2_y == pytest.approx(u1a_y), (
            f"Feedback bridge should stay on U1A's stage row after late passes: {result}"
        )
        assert r3_y == pytest.approx(u1a_y + _gv_mod.GRID_ROW_MM), (
            f"Gain-to-ground shunt should stay one row below the inverting node: {result}"
        )

    def test_opamp_stage_shapes_non_inverting_input_handoff_as_bridge_plus_shunt_node(
        self,
    ) -> None:
        """Phase 2.2.1: the U1A non-inverting input should read as
        bridge-on-row plus shunt-below.
        """
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="VIN", symbol="Device:R", value="src"),
                ComponentIR(ref="RV1", symbol="Device:R_Potentiometer", value="10k"),
                ComponentIR(ref="R4", symbol="Device:R", value="100k"),
                ComponentIR(ref="U1A", symbol="Amplifier:NE5532", value="NE5532"),
                ComponentIR(ref="R2", symbol="Device:R", value="22k"),
                ComponentIR(ref="R3", symbol="Device:R", value="2.2k"),
            ],
            nets=[
                NetIR(
                    name="IN_L_AC",
                    pins=[PinRefIR(ref="VIN", pin="1"), PinRefIR(ref="RV1", pin="1")],
                ),
                NetIR(
                    name="VOL_L_OUT",
                    pins=[
                        PinRefIR(ref="RV1", pin="2"),
                        PinRefIR(ref="R4", pin="1"),
                        PinRefIR(ref="U1A", pin="3"),
                    ],
                ),
                NetIR(name="GND", pins=[PinRefIR(ref="R4", pin="2"), PinRefIR(ref="R3", pin="2")]),
                NetIR(
                    name="U1A_INV",
                    pins=[
                        PinRefIR(ref="U1A", pin="2"),
                        PinRefIR(ref="R2", pin="1"),
                        PinRefIR(ref="R3", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT_STAGE1",
                    pins=[PinRefIR(ref="U1A", pin="1"), PinRefIR(ref="R2", pin="2")],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("VIN", BlockRole.INPUT)
        block_layout.add_assignment("RV1", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R4", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1A", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("R2", BlockRole.FEEDBACK)
        block_layout.add_assignment("R3", BlockRole.FEEDBACK)

        positions: dict[str, tuple[float, float, float | None]] = {
            "VIN": (30.48, 91.44, None),
            "RV1": (52.0, 120.0, None),
            "R4": (60.0, 70.0, None),
            "U1A": (97.79, 91.44, None),
            "R2": (160.0, 60.0, None),
            "R3": (162.0, 160.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"R2", "R3"},
            annotations={
                "R2": ComponentAnnotation(feedback=True),
                "R3": ComponentAnnotation(feedback=True),
            },
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        u1a_x, u1a_y, _ = result["U1A"]
        rv1_x, rv1_y, _ = result["RV1"]
        r4_x, r4_y, _ = result["R4"]

        assert rv1_x == pytest.approx(r4_x), (
            f"The non-inverting bridge and shunt should share one input-node column: {result}"
        )
        assert u1a_x - rv1_x == pytest.approx(GRID_COL_MM), (
            f"The non-inverting input node should sit one grid lane left of U1A: {result}"
        )
        assert rv1_y == pytest.approx(u1a_y), (
            f"The bridge into U1A should stay on the stage row: {result}"
        )
        assert r4_y == pytest.approx(u1a_y + _gv_mod.GRID_ROW_MM), (
            f"The local shunt should hang one row below the non-inverting input node: {result}"
        )

    def test_opamp_stage_keeps_upstream_input_bundle_in_one_left_column(self) -> None:
        """Phase 2.2.1: upstream bridge parts should feed U1A as a clean left bundle."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="J1", symbol="Connector:AudioJack3", value="IN"),
                ComponentIR(ref="C5", symbol="Device:C", value="1u"),
                ComponentIR(ref="R1", symbol="Device:R", value="100k"),
                ComponentIR(ref="RV1", symbol="Device:R_Potentiometer", value="10k"),
                ComponentIR(ref="R4", symbol="Device:R", value="100k"),
                ComponentIR(ref="U1A", symbol="Amplifier:NE5532", value="NE5532"),
                ComponentIR(ref="R2", symbol="Device:R", value="22k"),
                ComponentIR(ref="R3", symbol="Device:R", value="2.2k"),
            ],
            nets=[
                NetIR(
                    name="LEFT_IN",
                    pins=[
                        PinRefIR(ref="J1", pin="T"),
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
                        PinRefIR(ref="R4", pin="1"),
                        PinRefIR(ref="U1A", pin="3"),
                    ],
                ),
                NetIR(
                    name="GND",
                    pins=[
                        PinRefIR(ref="J1", pin="S"),
                        PinRefIR(ref="RV1", pin="3"),
                        PinRefIR(ref="R4", pin="2"),
                        PinRefIR(ref="R3", pin="2"),
                    ],
                ),
                NetIR(
                    name="U1A_INV",
                    pins=[
                        PinRefIR(ref="U1A", pin="2"),
                        PinRefIR(ref="R2", pin="1"),
                        PinRefIR(ref="R3", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT_STAGE1",
                    pins=[PinRefIR(ref="U1A", pin="1"), PinRefIR(ref="R2", pin="2")],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("C5", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("RV1", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R4", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1A", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("R2", BlockRole.FEEDBACK)
        block_layout.add_assignment("R3", BlockRole.FEEDBACK)

        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (20.0, 150.0, None),
            "C5": (110.0, 60.0, None),
            "R1": (130.0, 150.0, None),
            "RV1": (140.0, 40.0, None),
            "R4": (60.0, 180.0, None),
            "U1A": (97.79, 121.92, None),
            "R2": (160.0, 60.0, None),
            "R3": (162.0, 160.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"R2", "R3"},
            annotations={
                "R2": ComponentAnnotation(feedback=True),
                "R3": ComponentAnnotation(feedback=True),
            },
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        u1a_x, u1a_y, _ = result["U1A"]
        c5_x, c5_y, _ = result["C5"]
        r1_x, r1_y, _ = result["R1"]
        rv1_x, rv1_y, _ = result["RV1"]
        _r4_x, r4_y, _ = result["R4"]
        r2_x, r2_y, _ = result["R2"]
        r3_x, r3_y, _ = result["R3"]

        assert c5_x == pytest.approx(r1_x), (
            f"The upstream bridge bundle should share one left-side column: {result}"
        )
        assert rv1_x - c5_x == pytest.approx(GRID_COL_MM), (
            "The non-inverting input node should sit one lane right of the "
            f"upstream bridge bundle: {result}"
        )
        assert r2_x - rv1_x == pytest.approx(GRID_COL_MM / 2.0), (
            f"The feedback node should stay between the non-inverting node and U1A: {result}"
        )
        assert u1a_x - r2_x == pytest.approx(GRID_COL_MM / 2.0), (
            f"U1A should complete a readable left-to-right gain-stage chain: {result}"
        )
        assert sorted([c5_y, r1_y]) == pytest.approx([u1a_y - _gv_mod.GRID_ROW_MM, u1a_y]), (
            f"The upstream bridge bundle should occupy one compact two-row column: {result}"
        )
        assert rv1_y == pytest.approx(u1a_y), (
            f"The non-inverting bridge should stay on the U1A row: {result}"
        )
        assert r4_y == pytest.approx(u1a_y + _gv_mod.GRID_ROW_MM), (
            f"The grounded shunt should hang one row below the input node: {result}"
        )
        assert r2_y == pytest.approx(u1a_y), (
            f"The feedback bridge should stay on the U1A row: {result}"
        )
        assert r3_y == pytest.approx(u1a_y + _gv_mod.GRID_ROW_MM), (
            f"The gain-to-ground shunt should stay one row below the feedback node: {result}"
        )

    def test_feedback_falls_back_to_any_neighbor_non_strict(self) -> None:
        """Non-strict mode preserves fallback from IC/connector anchor to any neighbor."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R_FB", symbol="Device:R", value="100k"),
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="R2", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(name="N1", pins=[PinRefIR(ref="R_FB", pin="1"), PinRefIR(ref="R1", pin="1")]),
                NetIR(name="N2", pins=[PinRefIR(ref="R_FB", pin="2"), PinRefIR(ref="R2", pin="1")]),
            ],
        )
        positions: dict[str, tuple[float, float, float | None]] = {
            "R_FB": (50.8, 120.0, None),
            "R1": (50.8, 80.0, None),
            "R2": (76.2, 100.0, None),
        }
        annotations = {"R_FB": ComponentAnnotation(feedback=True)}

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"R_FB"},
            annotations=annotations,
            channels={"R_FB": "mono", "R1": "mono", "R2": "mono"},
            decoupling_map={},
        )
        anchor_y_after_grid = round(round(80.0 / 1.27) * 1.27, 2)
        expected_y = round(anchor_y_after_grid - _gv_mod.GRID_ROW_MM, 2)
        assert result["R_FB"][1] == pytest.approx(expected_y)

    def test_feedback_fallback_raises_in_strict_mode(self) -> None:
        """Strict mode raises when a feedback component has no IC/connector anchor."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R_FB", symbol="Device:R", value="100k"),
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="R2", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(name="N1", pins=[PinRefIR(ref="R_FB", pin="1"), PinRefIR(ref="R1", pin="1")]),
                NetIR(name="N2", pins=[PinRefIR(ref="R_FB", pin="2"), PinRefIR(ref="R2", pin="1")]),
            ],
        )
        positions: dict[str, tuple[float, float, float | None]] = {
            "R_FB": (50.8, 120.0, None),
            "R1": (50.8, 80.0, None),
            "R2": (76.2, 100.0, None),
        }
        annotations = {"R_FB": ComponentAnnotation(feedback=True)}

        with pytest.raises(UserError, match="no IC/connector anchor") as exc_info:
            _gv_mod.apply_post_layout_snaps(
                positions,
                ir,
                feedback_refs={"R_FB"},
                annotations=annotations,
                channels={"R_FB": "mono", "R1": "mono", "R2": "mono"},
                decoupling_map={},
                strict=True,
            )
        assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID

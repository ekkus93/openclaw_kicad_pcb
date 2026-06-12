"""Phase 4: post-layout snap pass orchestration — basic ordering, multi-unit
sibling cohesion, buffer stage, and feedback fallback tests.
"""

from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
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

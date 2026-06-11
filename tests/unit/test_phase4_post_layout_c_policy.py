"""Phase 4: post-layout snap — layout policy overrides, stage coherence,
named profiles, and opamp neighborhood tests.
"""

from __future__ import annotations

import math
from unittest.mock import patch

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.graphviz_layout import snap as _gv_snap_mod
from kicad_pcb.graphviz_layout.snap import (
    _deoverlap_positions,
    _OpAmpLocalityContext,
)
from kicad_pcb.layout import (
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


# ---------------------------------------------------------------------------
# 4.1 / 4.2  LayoutEngine factory — NoneLayoutEngine
# ---------------------------------------------------------------------------


class TestApplyPostLayoutSnaps_Policy:
    def test_layout_policy_can_disable_decoupling_snap(self) -> None:
        """The layout policy should be able to leave decoupling positions untouched."""
        positions = {
            "U1": (100.0, 100.0, None),
            "CDEC": (70.0, 150.0, None),
        }

        enabled = _gv_mod.DEFAULT_LAYOUT_HEURISTIC_POLICY.apply_decoupling_snap(
            positions,
            {"CDEC": "U1"},
        )
        disabled = _gv_mod.LayoutHeuristicPolicy(
            enable_decoupling_snap=False
        ).apply_decoupling_snap(
            positions,
            {"CDEC": "U1"},
        )

        assert disabled == positions
        assert math.isclose(enabled["CDEC"][0], 100.0, abs_tol=0.01)
        assert math.isclose(enabled["CDEC"][1], 100.0 - _gv_mod.GRID_ROW_MM, abs_tol=0.01)

    def test_decoupling_snap_places_negative_rail_caps_below_anchor(self) -> None:
        """Negative-rail decouplers should be snapped below the active device."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="Out"),
                ComponentIR(ref="CPLUS", symbol="Device:C", value="100n"),
                ComponentIR(ref="CMINUS", symbol="Device:C", value="100n"),
            ],
            nets=[
                NetIR(
                    name="IN_SIG",
                    pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="U1", pin="3")],
                ),
                NetIR(
                    name="OUT_SIG",
                    pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="J2", pin="1")],
                ),
                NetIR(
                    name="VCC",
                    pins=[PinRefIR(ref="U1", pin="7"), PinRefIR(ref="CPLUS", pin="1")],
                ),
                NetIR(
                    name="VEE",
                    pins=[PinRefIR(ref="U1", pin="4"), PinRefIR(ref="CMINUS", pin="1")],
                ),
                NetIR(
                    name="GND",
                    pins=[PinRefIR(ref="CPLUS", pin="2"), PinRefIR(ref="CMINUS", pin="2")],
                ),
            ],
        )
        positions = {
            "U1": (100.0, 100.0, None),
            "CPLUS": (70.0, 150.0, None),
            "CMINUS": (130.0, 40.0, None),
        }

        result = _gv_mod.DEFAULT_LAYOUT_HEURISTIC_POLICY.apply_decoupling_snap(
            positions,
            {"CPLUS": "U1", "CMINUS": "U1"},
            ir,
        )

        assert math.isclose(result["CPLUS"][0], 100.0, abs_tol=0.01)
        assert math.isclose(result["CMINUS"][0], 100.0, abs_tol=0.01)
        assert math.isclose(result["CPLUS"][1], 100.0 - _gv_mod.GRID_ROW_MM, abs_tol=0.01)
        assert math.isclose(result["CMINUS"][1], 100.0 + _gv_mod.GRID_ROW_MM, abs_tol=0.01)

    def test_layout_policy_can_disable_opamp_locality(self) -> None:
        """The layout policy should be able to skip op-amp locality staging."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
                ComponentIR(ref="RFB", symbol="Device:R", value="47k"),
                ComponentIR(ref="ROUT", symbol="Device:R", value="100"),
                ComponentIR(ref="CDEC", symbol="Device:C", value="100n"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(
                    name="IN_A",
                    pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="RIN", pin="1")],
                ),
                NetIR(
                    name="IN_B",
                    pins=[PinRefIR(ref="RIN", pin="2"), PinRefIR(ref="U1", pin="3")],
                ),
                NetIR(
                    name="FB_A",
                    pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="RFB", pin="1")],
                ),
                NetIR(
                    name="FB_B",
                    pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="RFB", pin="2")],
                ),
                NetIR(
                    name="OUT",
                    pins=[
                        PinRefIR(ref="U1", pin="6"),
                        PinRefIR(ref="ROUT", pin="1"),
                        PinRefIR(ref="JOUT", pin="1"),
                    ],
                ),
                NetIR(
                    name="VCC",
                    pins=[PinRefIR(ref="U1", pin="7"), PinRefIR(ref="CDEC", pin="1")],
                ),
                NetIR(
                    name="GND",
                    pins=[PinRefIR(ref="U1", pin="4"), PinRefIR(ref="CDEC", pin="2")],
                ),
            ],
        )
        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("RFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("ROUT", BlockRole.OUTPUT)
        block_layout.add_assignment("CDEC", BlockRole.DECOUPLING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)
        positions = {
            "U1": (100.0, 100.0, None),
            "RIN": (120.0, 70.0, None),
            "ROUT": (80.0, 135.0, None),
            "RFB": (145.0, 70.0, None),
            "CDEC": (70.0, 150.0, None),
            "JIN": (60.0, 70.0, None),
            "JOUT": (150.0, 135.0, None),
        }

        disabled = _gv_mod.LayoutHeuristicPolicy(enable_opamp_locality=False).apply_opamp_locality(
            positions,
            ir,
            annotations={"RFB": ComponentAnnotation(feedback=True)},
            context=_OpAmpLocalityContext(
                decoupling_map={"CDEC": "U1"},
                block_layout=block_layout,
            ),
        )
        enabled = _gv_mod.DEFAULT_LAYOUT_HEURISTIC_POLICY.apply_opamp_locality(
            positions,
            ir,
            annotations={"RFB": ComponentAnnotation(feedback=True)},
            context=_OpAmpLocalityContext(decoupling_map={"CDEC": "U1"}, block_layout=block_layout),
        )

        assert disabled == positions
        assert enabled != positions
        assert enabled["RIN"][0] < positions["RIN"][0]
        assert enabled["ROUT"][0] > positions["ROUT"][0]

    def test_layout_policy_can_disable_input_stage_cohesion(self) -> None:
        """The layout policy should be able to skip input-stage cohesion."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="CIN", symbol="Device:C", value="100n"),
                ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="ROUT", symbol="Device:R", value="100"),
            ],
            nets=[
                NetIR(
                    name="IN_A",
                    pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="CIN", pin="1")],
                ),
                NetIR(
                    name="IN_B",
                    pins=[PinRefIR(ref="CIN", pin="2"), PinRefIR(ref="RIN", pin="1")],
                ),
                NetIR(
                    name="IN_C",
                    pins=[PinRefIR(ref="RIN", pin="2"), PinRefIR(ref="U1", pin="3")],
                ),
                NetIR(
                    name="OUT",
                    pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="ROUT", pin="1")],
                ),
            ],
        )
        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("CIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("ROUT", BlockRole.OUTPUT)
        positions = {
            "JIN": (170.0, 70.0, None),
            "CIN": (175.0, 110.0, None),
            "RIN": (180.0, 125.0, None),
            "U1": (190.0, 100.0, None),
            "ROUT": (120.0, 140.0, None),
        }

        disabled = _gv_mod.LayoutHeuristicPolicy(
            enable_input_stage_cohesion=False
        ).apply_input_stage_cohesion(
            positions,
            ir,
            block_layout=block_layout,
        )
        enabled = _gv_mod.DEFAULT_LAYOUT_HEURISTIC_POLICY.apply_input_stage_cohesion(
            positions,
            ir,
            block_layout=block_layout,
        )

        assert disabled == positions
        assert enabled != positions
        assert enabled["JIN"][0] < positions["JIN"][0]
        assert enabled["CIN"][0] < positions["CIN"][0]

    def test_layout_policy_can_disable_output_stage_cohesion(self) -> None:
        """The layout policy should be able to skip output-stage cohesion."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="RISO", symbol="Device:R", value="47"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(
                    name="OUT_A",
                    pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="RISO", pin="1")],
                ),
                NetIR(
                    name="OUT_B",
                    pins=[PinRefIR(ref="RISO", pin="2"), PinRefIR(ref="JOUT", pin="1")],
                ),
            ],
        )
        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RISO", BlockRole.OUTPUT)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)
        positions = {
            "U1": (190.0, 100.0, None),
            "RISO": (205.0, 100.0, None),
            "JOUT": (215.0, 100.0, None),
        }

        disabled = _gv_mod.LayoutHeuristicPolicy(
            enable_output_stage_cohesion=False
        ).apply_output_stage_cohesion(
            positions,
            ir,
            block_layout=block_layout,
        )
        enabled = _gv_mod.DEFAULT_LAYOUT_HEURISTIC_POLICY.apply_output_stage_cohesion(
            positions,
            ir,
            block_layout=block_layout,
        )

        assert disabled == positions
        assert enabled["JOUT"][0] > positions["JOUT"][0]

    def test_apply_post_layout_snaps_respects_disabled_layout_policy(self) -> None:
        """The post-snap coordinator should honor the layout policy seam."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="CDEC", symbol="Device:C", value="100n"),
            ],
            nets=[
                NetIR(
                    name="VCC",
                    pins=[PinRefIR(ref="U1", pin="7"), PinRefIR(ref="CDEC", pin="1")],
                ),
                NetIR(
                    name="GND",
                    pins=[PinRefIR(ref="U1", pin="4"), PinRefIR(ref="CDEC", pin="2")],
                ),
            ],
        )
        positions: dict[str, tuple[float, float, float | None]] = {
            "U1": (101.6, 101.6, None),
            "CDEC": (68.58, 149.86, None),
        }

        disabled_policy = _gv_mod.LayoutHeuristicPolicy(
            enable_decoupling_snap=False,
            enable_opamp_locality=False,
            enable_input_stage_cohesion=False,
            enable_output_stage_cohesion=False,
        )
        enabled = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={"CDEC": "U1"},
        )
        disabled = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={"CDEC": "U1"},
            heuristic_policy=disabled_policy,
        )

        assert math.isclose(enabled["CDEC"][0], enabled["U1"][0], abs_tol=0.01)
        assert math.isclose(
            enabled["CDEC"][1],
            enabled["U1"][1] - _gv_mod.GRID_ROW_MM,
            abs_tol=0.01,
        )
        assert math.isclose(disabled["CDEC"][0], positions["CDEC"][0], abs_tol=0.01)
        assert not math.isclose(disabled["CDEC"][0], disabled["U1"][0], abs_tol=0.01)
        assert not math.isclose(
            disabled["CDEC"][1],
            disabled["U1"][1] - _gv_mod.GRID_ROW_MM,
            abs_tol=0.01,
        )

    def test_apply_post_layout_snaps_deoverlaps_collisions_from_late_passes(self) -> None:
        """The coordinator should resolve collisions reintroduced after the late passes."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="R2", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(
                    name="SIG",
                    pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="1")],
                ),
            ],
        )
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (50.80, 76.20, None),
            "R2": (63.50, 88.90, None),
        }

        def _late_collision(
            snapshot: dict[str, tuple[float, float, float | None]],
            *_args: object,
            **_kwargs: object,
        ) -> dict[str, tuple[float, float, float | None]]:
            result = dict(snapshot)
            result["R1"] = (50.80, 76.20, None)
            result["R2"] = (50.80, 76.20, None)
            return result

        with patch.object(_gv_snap_mod, "_snap_input_connector_signal_attachment", _late_collision):
            result = _gv_mod.apply_post_layout_snaps(
                positions,
                ir,
                feedback_refs=set(),
                annotations={},
                channels={ref: "mono" for ref in positions},
                decoupling_map={},
            )

        assert result["R1"] != result["R2"]
        assert math.isclose(result["R1"][0], result["R2"][0], abs_tol=0.01)
        assert result["R2"][1] - result["R1"][1] >= 11.42

    def test_deoverlap_positions_separates_exact_overlap_even_for_skip_pair(self) -> None:
        """Skip pairs should not preserve a literal same-cell collision."""
        result = _deoverlap_positions(
            {
                "U1": (91.44, 129.54, None),
                "U2": (91.44, 129.54, None),
            },
            skip_pairs=frozenset({("U1", "U2")}),
        )

        assert result["U1"] != result["U2"]
        assert math.isclose(result["U1"][0], result["U2"][0], abs_tol=0.01)
        assert result["U2"][1] - result["U1"][1] >= 11.42

"""Phase 4: post-layout snap — layout policy overrides, stage coherence,
named profiles, and opamp neighborhood tests.
"""

from __future__ import annotations

import math
from unittest.mock import patch

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.block_detection import BlockLayout, BlockRole, classify_circuit
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands._sch_apply import SCHEMATIC_HEURISTIC_PROFILES
from kicad_pcb.graphviz_layout import snap as _gv_snap_mod
from kicad_pcb.graphviz_layout.snap import (
    _deoverlap_positions,
    _OpAmpLocalityContext,
)
from kicad_pcb.layout import (
    GRID_COL_MM,
    ComponentAnnotation,
    compute_orientations,
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

    def test_named_layout_profiles_diverge_on_decoupling_fixture(self) -> None:
        """Named profiles should produce different post-layout positions on the same fixture."""
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
        analog_audio = SCHEMATIC_HEURISTIC_PROFILES["analog_audio"]
        generic_digital = SCHEMATIC_HEURISTIC_PROFILES["generic_digital"]

        analog_layout = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={"CDEC": "U1"},
            heuristic_policy=analog_audio.layout_policy,
        )
        digital_layout = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={"CDEC": "U1"},
            heuristic_policy=generic_digital.layout_policy,
        )

        assert analog_audio.layout_policy.enable_decoupling_snap is True
        assert generic_digital.layout_policy.enable_decoupling_snap is False
        assert analog_layout != digital_layout
        assert math.isclose(analog_layout["CDEC"][0], analog_layout["U1"][0], abs_tol=0.01)
        assert math.isclose(
            analog_layout["CDEC"][1],
            analog_layout["U1"][1] - _gv_mod.GRID_ROW_MM,
            abs_tol=0.01,
        )
        assert math.isclose(digital_layout["CDEC"][0], positions["CDEC"][0], abs_tol=0.01)
        assert not math.isclose(digital_layout["CDEC"][0], positions["U1"][0], abs_tol=0.01)
        assert not math.isclose(
            digital_layout["CDEC"][1],
            positions["U1"][1] - _gv_mod.GRID_ROW_MM,
            abs_tol=0.01,
        )

    def test_stage_coherence_input_block_compact_and_left_bounded(self) -> None:
        """Phase 7.3: input stage should stay compact and left-bounded."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="CIN", symbol="Device:C", value="100n"),
                ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="COUT", symbol="Device:C", value="100n"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(
                    name="IN_A", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="CIN", pin="1")]
                ),
                NetIR(
                    name="IN_B", pins=[PinRefIR(ref="CIN", pin="2"), PinRefIR(ref="RIN", pin="1")]
                ),
                NetIR(
                    name="IN_C", pins=[PinRefIR(ref="RIN", pin="2"), PinRefIR(ref="U1", pin="3")]
                ),
                NetIR(
                    name="OUT_A", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="COUT", pin="1")]
                ),
                NetIR(
                    name="OUT_B",
                    pins=[PinRefIR(ref="COUT", pin="2"), PinRefIR(ref="JOUT", pin="1")],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("CIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("COUT", BlockRole.OUTPUT)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "JIN": (170.0, 80.0, None),
            "CIN": (178.0, 92.0, None),
            "RIN": (183.0, 108.0, None),
            "U1": (190.0, 100.0, None),
            "COUT": (210.0, 90.0, None),
            "JOUT": (218.0, 100.0, None),
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

        ux, _uy, _ = result["U1"]
        input_refs = ["JIN", "CIN", "RIN"]
        input_x = [result[r][0] for r in input_refs]
        input_y = [result[r][1] for r in input_refs]

        assert max(input_x) < ux, f"Input stage should remain left of op-amp: {result}"
        assert max(input_x) - min(input_x) <= 2.5 * GRID_COL_MM, (
            "Input stage should be compact in x"
        )
        assert max(input_y) - min(input_y) <= 2.5 * _gv_mod.GRID_ROW_MM, (
            "Input stage should be compact in y"
        )

    def test_stage_coherence_output_block_compact_and_right_bounded(self) -> None:
        """Phase 7.3: output stage should stay compact and right-bounded."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="RFB", symbol="Device:R", value="47k"),
                ComponentIR(ref="COUT", symbol="Device:C", value="100n"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(
                    name="IN_A", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="RIN", pin="1")]
                ),
                NetIR(
                    name="IN_B", pins=[PinRefIR(ref="RIN", pin="2"), PinRefIR(ref="U1", pin="3")]
                ),
                NetIR(name="FB", pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="RFB", pin="1")]),
                NetIR(
                    name="OUT_A", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="COUT", pin="1")]
                ),
                NetIR(
                    name="OUT_B",
                    pins=[PinRefIR(ref="COUT", pin="2"), PinRefIR(ref="JOUT", pin="1")],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("COUT", BlockRole.OUTPUT)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "JIN": (165.0, 100.0, None),
            "RIN": (178.0, 100.0, None),
            "U1": (190.0, 100.0, None),
            "RFB": (197.0, 92.0, None),
            "COUT": (208.0, 88.0, None),
            "JOUT": (216.0, 102.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"RFB"},
            annotations={"RFB": ComponentAnnotation(feedback=True)},
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        ux, _uy, _ = result["U1"]
        output_refs = ["COUT", "JOUT"]
        output_x = [result[r][0] for r in output_refs]
        output_y = [result[r][1] for r in output_refs]

        assert min(output_x) > ux, f"Output stage should remain right of op-amp: {result}"
        assert max(output_x) - min(output_x) <= 1.5 * GRID_COL_MM, (
            "Output stage should be compact in x"
        )
        assert max(output_y) - min(output_y) <= 2.5 * _gv_mod.GRID_ROW_MM, (
            "Output stage should be compact in y"
        )

    def test_stage_coherence_input_output_boundaries_do_not_overlap(self) -> None:
        """Phase 7.3: input and output stage boundaries should stay separated."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="CIN", symbol="Device:C", value="100n"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="RFB", symbol="Device:R", value="47k"),
                ComponentIR(ref="COUT", symbol="Device:C", value="100n"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(name="IN", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="CIN", pin="1")]),
                NetIR(name="IN2", pins=[PinRefIR(ref="CIN", pin="2"), PinRefIR(ref="U1", pin="3")]),
                NetIR(name="FB", pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="RFB", pin="1")]),
                NetIR(
                    name="OUT", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="COUT", pin="1")]
                ),
                NetIR(
                    name="OUT2", pins=[PinRefIR(ref="COUT", pin="2"), PinRefIR(ref="JOUT", pin="1")]
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("CIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("COUT", BlockRole.OUTPUT)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "JIN": (170.0, 95.0, None),
            "CIN": (178.0, 100.0, None),
            "U1": (190.0, 100.0, None),
            "RFB": (198.0, 95.0, None),
            "COUT": (208.0, 100.0, None),
            "JOUT": (216.0, 105.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"RFB"},
            annotations={"RFB": ComponentAnnotation(feedback=True)},
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        input_refs = ["JIN", "CIN"]
        output_refs = ["COUT", "JOUT"]
        input_max_x = max(result[r][0] for r in input_refs)
        output_min_x = min(result[r][0] for r in output_refs)

        assert input_max_x + GRID_COL_MM <= output_min_x, (
            f"Input/output stages should remain separated by at least one grid column: {result}"
        )

    def test_opamp_orientation_inputs_left_output_right(self) -> None:
        """Phase 4.3: op-amp orientation should have inputs left, output right."""

        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(name="IN", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="U1", pin="3")]),
                NetIR(
                    name="OUT", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="JOUT", pin="1")]
                ),
            ],
        )

        positions = {"JIN": (30.0, 80.0), "U1": (90.0, 80.0), "JOUT": (150.0, 80.0)}
        orientations = compute_orientations(ir, positions)

        assert orientations["U1"] == 0, (
            "Op-amp should be at 0° orientation (inputs left, output right)"
        )

    def test_feedback_passive_vertical_near_opamp(self) -> None:
        """Phase 4.3: feedback passives in same column as op-amp prefer vertical (90°)."""

        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="RFB", symbol="Device:R", value="47k"),
                ComponentIR(ref="R2", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(
                    name="FB",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="RFB", pin="1"),
                        PinRefIR(ref="R2", pin="1"),
                    ],
                ),
                NetIR(name="FB2", pins=[PinRefIR(ref="U1", pin="3"), PinRefIR(ref="RFB", pin="2")]),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("R2", BlockRole.PRECONDITIONING)

        # RFB positioned in same column as U1 (x within GRID_COL_MM / 2)
        # R2 positioned away from U1 column
        positions = {
            "U1": (90.0, 80.0),
            "RFB": (90.0, 60.0),
            "R2": (120.0, 80.0),
        }

        orientations = compute_orientations(ir, positions, block_layout=block_layout)

        assert orientations["RFB"] == 90, (
            "Feedback passive RFB in same column as op-amp should be vertical (90°)"
        )

    def test_input_output_passives_prefer_horizontal(self) -> None:
        """Phase 4.3: input/output stage passives prefer horizontal (0°) for left-to-right flow."""

        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
                ComponentIR(ref="ROUT", symbol="Device:R", value="100"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(
                    name="IN_A", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="RIN", pin="1")]
                ),
                NetIR(
                    name="OUT_A",
                    pins=[PinRefIR(ref="ROUT", pin="2"), PinRefIR(ref="JOUT", pin="1")],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("ROUT", BlockRole.OUTPUT)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        # Positions arranged left-to-right (horizontal flow dominates)
        positions = {
            "JIN": (30.0, 80.0),
            "RIN": (60.0, 80.0),
            "ROUT": (120.0, 80.0),
            "JOUT": (150.0, 80.0),
        }

        orientations = compute_orientations(ir, positions, block_layout=block_layout)

        assert orientations["RIN"] == 0, (
            "Input-stage passive RIN should be horizontal (0°) for left-to-right flow"
        )
        assert orientations["ROUT"] == 0, (
            "Output-stage passive ROUT should be horizontal (0°) for left-to-right flow"
        )

    def test_opamp_neighborhood_feedback_near_opamp_not_connectors(self) -> None:
        """Phase 4.4: feedback components should be closer to op-amp than to connectors."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
                ComponentIR(ref="RFB", symbol="Device:R", value="47k"),
                ComponentIR(ref="CFB", symbol="Device:C", value="22p"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(
                    name="IN_A", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="RIN", pin="1")]
                ),
                NetIR(
                    name="IN_B", pins=[PinRefIR(ref="RIN", pin="2"), PinRefIR(ref="U1", pin="3")]
                ),
                NetIR(
                    name="FB_A",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="RFB", pin="1"),
                        PinRefIR(ref="CFB", pin="1"),
                    ],
                ),
                NetIR(
                    name="FB_B",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="RFB", pin="2"),
                        PinRefIR(ref="CFB", pin="2"),
                    ],
                ),
                NetIR(
                    name="OUT", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="JOUT", pin="1")]
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("RFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("CFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "JIN": (30.0, 100.0, None),
            "RIN": (60.0, 100.0, None),
            "U1": (110.0, 100.0, None),
            "RFB": (140.0, 90.0, None),
            "CFB": (140.0, 110.0, None),
            "JOUT": (180.0, 100.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"RFB", "CFB"},
            annotations={
                "RFB": ComponentAnnotation(feedback=True),
                "CFB": ComponentAnnotation(feedback=True),
            },
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        ux, uy, _ = result["U1"]
        jin_x, jin_y, _ = result["JIN"]
        jout_x, jout_y, _ = result["JOUT"]

        for fb_ref in ["RFB", "CFB"]:
            fb_x, fb_y, _ = result[fb_ref]
            dist_to_opamp = math.sqrt((fb_x - ux) ** 2 + (fb_y - uy) ** 2)
            dist_to_jin = math.sqrt((fb_x - jin_x) ** 2 + (fb_y - jin_y) ** 2)
            dist_to_jout = math.sqrt((fb_x - jout_x) ** 2 + (fb_y - jout_y) ** 2)

            assert dist_to_opamp < dist_to_jin, (
                f"{fb_ref} should be closer to U1 than to JIN: "
                f"dist(U1)={dist_to_opamp:.2f}, dist(JIN)={dist_to_jin:.2f}"
            )
            assert dist_to_opamp < dist_to_jout, (
                f"{fb_ref} should be closer to U1 than to JOUT: "
                f"dist(U1)={dist_to_opamp:.2f}, dist(JOUT)={dist_to_jout:.2f}"
            )

    def test_opamp_neighborhood_output_parts_on_output_side(self) -> None:
        """Phase 4.4: output-side parts should be placed on the output side of op-amp."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="ROUT", symbol="Device:R", value="100"),
                ComponentIR(ref="COUT", symbol="Device:C", value="10u"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(name="IN", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="U1", pin="3")]),
                NetIR(
                    name="OUT_A", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="ROUT", pin="1")]
                ),
                NetIR(
                    name="OUT_B",
                    pins=[PinRefIR(ref="ROUT", pin="2"), PinRefIR(ref="COUT", pin="1")],
                ),
                NetIR(
                    name="OUT_C",
                    pins=[PinRefIR(ref="COUT", pin="2"), PinRefIR(ref="JOUT", pin="1")],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("ROUT", BlockRole.OUTPUT)
        block_layout.add_assignment("COUT", BlockRole.OUTPUT)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "JIN": (30.0, 100.0, None),
            "U1": (90.0, 100.0, None),
            "ROUT": (60.0, 95.0, None),
            "COUT": (60.0, 105.0, None),
            "JOUT": (150.0, 100.0, None),
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

        ux, _uy, _ = result["U1"]
        rout_x, _rout_y, _ = result["ROUT"]
        cout_x, _cout_y, _ = result["COUT"]
        jout_x, _jout_y, _ = result["JOUT"]

        assert rout_x > ux, (
            f"Output resistor ROUT should be right of U1: ROUT.x={rout_x:.2f}, U1.x={ux:.2f}"
        )
        assert cout_x > ux, (
            f"Output cap COUT should be right of U1: COUT.x={cout_x:.2f}, U1.x={ux:.2f}"
        )
        assert jout_x > ux, (
            f"Output connector JOUT should be right of U1: JOUT.x={jout_x:.2f}, U1.x={ux:.2f}"
        )

    def test_opamp_neighborhood_signal_support_caps_stay_out_of_decoupling_lane(self) -> None:
        """Phase 5.2: signal-side caps touching ground should stay in signal lanes."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="COUT", symbol="Device:C", value="10u"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
                ComponentIR(ref="RISO", symbol="Device:R", value="10"),
                ComponentIR(ref="CDEC", symbol="Device:C", value="100n"),
                ComponentIR(ref="J3", symbol="Connector_Generic:Conn_01x02", value="Power"),
            ],
            nets=[
                NetIR(name="IN", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="U1", pin="3")]),
                NetIR(
                    name="OUT",
                    pins=[
                        PinRefIR(ref="U1", pin="6"),
                        PinRefIR(ref="COUT", pin="1"),
                        PinRefIR(ref="JOUT", pin="1"),
                    ],
                ),
                NetIR(
                    name="VCC",
                    pins=[
                        PinRefIR(ref="J3", pin="1"),
                        PinRefIR(ref="RISO", pin="1"),
                    ],
                ),
                NetIR(
                    name="VCC_LOCAL",
                    pins=[
                        PinRefIR(ref="RISO", pin="2"),
                        PinRefIR(ref="U1", pin="7"),
                        PinRefIR(ref="CDEC", pin="1"),
                    ],
                ),
                NetIR(
                    name="GND",
                    pins=[
                        PinRefIR(ref="J3", pin="2"),
                        PinRefIR(ref="U1", pin="4"),
                        PinRefIR(ref="COUT", pin="2"),
                        PinRefIR(ref="CDEC", pin="2"),
                    ],
                ),
            ],
        )

        block_layout = classify_circuit(ir)

        assert block_layout.get_role("COUT") == BlockRole.OUTPUT
        assert block_layout.get_role("RISO") == BlockRole.POWER_ENTRY
        assert block_layout.get_role("CDEC") == BlockRole.DECOUPLING

        positions: dict[str, tuple[float, float, float | None]] = {
            "JIN": (30.0, 100.0, None),
            "U1": (90.0, 100.0, None),
            "COUT": (65.0, 80.0, None),
            "JOUT": (150.0, 100.0, None),
            "RISO": (120.0, 84.0, None),
            "CDEC": (125.0, 112.0, None),
            "J3": (150.0, 70.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={"CDEC": "U1"},
            block_layout=block_layout,
        )

        ux, uy, _ = result["U1"]
        cout_x, cout_y, _ = result["COUT"]
        cdec_x, cdec_y, _ = result["CDEC"]

        assert cout_x > ux, f"Output support cap should stay on output side: {result}"
        assert cout_y >= uy - 2.0 * _gv_mod.GRID_ROW_MM, (
            "Output support cap should not be pulled into the decoupling lane above the op-amp"
        )
        assert math.isclose(cdec_x, ux, abs_tol=0.01), (
            "True decoupling cap should remain aligned to the op-amp column"
        )
        assert cdec_y < uy, "True decoupling cap should remain above the op-amp"

    def test_opamp_neighborhood_decouplers_near_power_not_input(self) -> None:
        """Phase 4.4: supply decouplers should be nearer the power pins than input network."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
                ComponentIR(ref="CIN", symbol="Device:C", value="100n"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="CDEC", symbol="Device:C", value="100n"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(
                    name="IN_A", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="CIN", pin="1")]
                ),
                NetIR(
                    name="IN_B", pins=[PinRefIR(ref="CIN", pin="2"), PinRefIR(ref="RIN", pin="1")]
                ),
                NetIR(
                    name="IN_C", pins=[PinRefIR(ref="RIN", pin="2"), PinRefIR(ref="U1", pin="3")]
                ),
                NetIR(
                    name="OUT", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="JOUT", pin="1")]
                ),
                NetIR(
                    name="VCC", pins=[PinRefIR(ref="U1", pin="7"), PinRefIR(ref="CDEC", pin="1")]
                ),
                NetIR(
                    name="GND", pins=[PinRefIR(ref="U1", pin="4"), PinRefIR(ref="CDEC", pin="2")]
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("CIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("CDEC", BlockRole.DECOUPLING)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "JIN": (30.0, 100.0, None),
            "CIN": (50.0, 95.0, None),
            "RIN": (50.0, 105.0, None),
            "U1": (110.0, 100.0, None),
            "CDEC": (80.0, 140.0, None),
            "JOUT": (150.0, 100.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={"CDEC": "U1"},
            block_layout=block_layout,
        )

        ux, uy, _ = result["U1"]
        cdec_x, cdec_y, _ = result["CDEC"]
        cin_x, cin_y, _ = result["CIN"]
        rin_x, rin_y, _ = result["RIN"]

        dist_cdec_to_opamp = math.sqrt((cdec_x - ux) ** 2 + (cdec_y - uy) ** 2)
        dist_cdec_to_cin = math.sqrt((cdec_x - cin_x) ** 2 + (cdec_y - cin_y) ** 2)
        dist_cdec_to_rin = math.sqrt((cdec_x - rin_x) ** 2 + (cdec_y - rin_y) ** 2)

        assert dist_cdec_to_opamp < dist_cdec_to_cin, (
            f"Decoupling cap CDEC should be closer to U1 than to input network CIN: "
            f"dist(U1)={dist_cdec_to_opamp:.2f}, dist(CIN)={dist_cdec_to_cin:.2f}"
        )
        assert dist_cdec_to_opamp < dist_cdec_to_rin, (
            f"Decoupling cap CDEC should be closer to U1 than to input network RIN: "
            f"dist(U1)={dist_cdec_to_opamp:.2f}, dist(RIN)={dist_cdec_to_rin:.2f}"
        )

        assert math.isclose(cdec_x, ux, abs_tol=0.01), (
            "Decoupling cap should align to op-amp column for tight power coupling"
        )


# ---------------------------------------------------------------------------
# Phase 8 — Wire routing improvements (Rule §4)
# ---------------------------------------------------------------------------

"""Phase 4: post-layout snap — named profiles, stage coherence, and opamp neighborhood tests."""

from __future__ import annotations

import math

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.block_detection import BlockLayout, BlockRole, classify_circuit
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands._sch_apply import SCHEMATIC_HEURISTIC_PROFILES
from kicad_pcb.layout import (
    GRID_COL_MM,
    ComponentAnnotation,
    compute_orientations,
)

pytestmark = pytest.mark.unit


class TestApplyPostLayoutSnaps_PolicyProfiles:
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

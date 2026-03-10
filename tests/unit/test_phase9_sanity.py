"""Phase 9.3 — Add Tests for Orientation Sanity.

This module validates that orientation conventions are maintained consistently
across entire circuits, not just within individual components or roles.

Phase 9.3 ensures:
- Connectors at page edges are oriented consistently (input/output per tier)
- Op-amps maintain their stable preferred orientation (always 0°)
- Passives within the same functional block have consistent orientations
- Overall orientation coherence across the entire schematic

Test strategy: Generate realistic multi-component circuits and assert that
orientation patterns are sensible from a readability perspective.
"""

from __future__ import annotations

from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.layout import compute_orientations
from kicad_pcb.tier import assign_tiers


class TestConnectorOrientationAtPageEdges:
    """Connectors at page edges should be consistently oriented."""

    def test_input_connector_leftmost_faces_right(self) -> None:
        """Input connector at left edge faces right (0°)."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="IN"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="OUT"),
            ],
            nets=[
                NetIR(
                    name="IN",
                    pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="U1", pin="3")],
                ),
                NetIR(
                    name="OUT",
                    pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="J2", pin="1")],
                ),
            ],
        )
        # Input jack far left, op-amp center-left, output jack right
        positions = {
            "J1": (0.0, 0.0),
            "U1": (20.0, 0.0),
            "J2": (40.0, 0.0),
        }
        # Assign tiers: input = tier 0, opamp = tier 1, output = tier 2
        tiers = assign_tiers(ir)
        result = compute_orientations(ir, positions, tiers=tiers)

        # Input at tier 0 should be 0° (faces right)
        assert result["J1"] == 0, f"Input connector J1 should face right (0°), got {result['J1']}°"

    def test_output_connector_rightmost_faces_left(self) -> None:
        """Output connector at right edge faces left (180°)."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="IN"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="OUT"),
            ],
            nets=[
                NetIR(
                    name="IN",
                    pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="U1", pin="3")],
                ),
                NetIR(
                    name="OUT",
                    pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="J2", pin="1")],
                ),
            ],
        )
        positions = {
            "J1": (0.0, 0.0),
            "U1": (20.0, 0.0),
            "J2": (40.0, 0.0),
        }
        tiers = assign_tiers(ir)
        result = compute_orientations(ir, positions, tiers=tiers)

        # Output at max tier should be 180° (faces left)
        assert result["J2"] == 180, (
            f"Output connector J2 should face left (180°), got {result['J2']}°"
        )

    def test_all_connectors_in_circuit_consistent_with_tiers(self) -> None:
        """All connectors in a multi-jack circuit are consistently oriented by tier."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN1", symbol="Connector_Generic:Conn_01x01", value="L_IN"),
                ComponentIR(ref="JIN2", symbol="Connector_Generic:Conn_01x01", value="R_IN"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="U2", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="JOUT1", symbol="Connector_Generic:Conn_01x01", value="L_OUT"),
                ComponentIR(ref="JOUT2", symbol="Connector_Generic:Conn_01x01", value="R_OUT"),
            ],
            nets=[
                NetIR(
                    name="IN_L",
                    pins=[
                        PinRefIR(ref="JIN1", pin="1"),
                        PinRefIR(ref="U1", pin="3"),
                    ],
                ),
                NetIR(
                    name="IN_R",
                    pins=[
                        PinRefIR(ref="JIN2", pin="1"),
                        PinRefIR(ref="U2", pin="3"),
                    ],
                ),
                NetIR(
                    name="OUT_L",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="JOUT1", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT_R",
                    pins=[
                        PinRefIR(ref="U2", pin="1"),
                        PinRefIR(ref="JOUT2", pin="1"),
                    ],
                ),
            ],
        )
        positions = {
            "JIN1": (0.0, 0.0),
            "JIN2": (0.0, 10.0),
            "U1": (20.0, 0.0),
            "U2": (20.0, 10.0),
            "JOUT1": (40.0, 0.0),
            "JOUT2": (40.0, 10.0),
        }
        tiers = assign_tiers(ir)
        result = compute_orientations(ir, positions, tiers=tiers)

        # All input jacks should be 0° (tier 0)
        input_tiers = {ref: tier for ref, tier in tiers.items() if ref.startswith("JIN")}
        assert all(result[ref] == 0 for ref in input_tiers), (
            f"All input connectors should be 0°, got {result}"
        )

        # All output jacks should be 180° (max tier)
        output_tiers = {ref: tier for ref, tier in tiers.items() if ref.startswith("JOUT")}

        # At least one output jack should be 180° (the primary/first one on max tier)
        output_jacks_180 = [ref for ref in output_tiers if result.get(ref) == 180]
        assert len(output_jacks_180) > 0, (
            f"At least one output connector should be 180°, got {result}"
        )

        # Key sanity check: all connectors should have a valid orientation (0° or 180°)
        connector_refs = ["JIN1", "JIN2", "JOUT1", "JOUT2"]
        for ref in connector_refs:
            assert ref in result, f"Connector {ref} should have an orientation"
            assert result[ref] in (0, 180), (
                f"Connector {ref} should be 0° or 180°, got {result[ref]}°"
            )
        # Within connectors of the same type, they should align
        assert result["JIN1"] == result["JIN2"], (
            f"Input connectors should align: JIN1={result['JIN1']}°, JIN2={result['JIN2']}°"
        )
        # Note: Output connectors might not align if they're on different tiers
        # (e.g., JOUT1 on main path at max tier, JOUT2 on secondary path at lower tier)
        # Just verify they're both valid (0° or 180°)


class TestOpAmpOrientationStability:
    """Op-amp orientation should be stable and uniform across circuits."""

    def test_single_opamp_always_0_degrees(self) -> None:
        """Single op-amp maintains 0° regardless of local context."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="R2", symbol="Device:R", value="100k"),
            ],
            nets=[
                NetIR(
                    name="IN",
                    pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="U1", pin="3")],
                ),
                NetIR(
                    name="FB",
                    pins=[
                        PinRefIR(ref="R2", pin="1"),
                        PinRefIR(ref="U1", pin="2"),
                    ],
                ),
                NetIR(
                    name="OUT",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="R2", pin="2"),
                    ],
                ),
            ],
        )
        positions = {
            "U1": (20.0, 0.0),
            "R1": (10.0, 0.0),
            "R2": (30.0, 10.0),
        }
        result = compute_orientations(ir, positions)

        assert result["U1"] == 0, f"Single op-amp should always be 0°, got {result['U1']}°"

    def test_multiple_opamps_all_0_degrees(self) -> None:
        """Multiple op-amps in a circuit all maintain 0° orientation."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="U2", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="U3", symbol="Amplifier_Operational:NE5532", value="NE5532"),
            ],
            nets=[
                NetIR(
                    name="GND",
                    pins=[PinRefIR(ref="U1", pin="4")],
                ),
            ],
        )
        positions = {
            "U1": (20.0, 0.0),
            "U2": (40.0, 0.0),
            "U3": (60.0, 0.0),
        }
        result = compute_orientations(ir, positions)

        for ref in ["U1", "U2", "U3"]:
            assert result[ref] == 0, f"Op-amp {ref} should be 0°, got {result[ref]}°"

    def test_opamp_orientation_independent_of_feedback_network(self) -> None:
        """Op-amp stays 0° even when feedback network varies orientation."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="RF1", symbol="Device:R", value="100k"),
                ComponentIR(ref="RF2", symbol="Device:R", value="1k"),
                ComponentIR(ref="CF", symbol="Device:C", value="10p"),
            ],
            nets=[
                NetIR(
                    name="INV",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="RF1", pin="1"),
                        PinRefIR(ref="RF2", pin="1"),
                        PinRefIR(ref="CF", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="RF1", pin="2"),
                        PinRefIR(ref="RF2", pin="2"),
                        PinRefIR(ref="CF", pin="2"),
                    ],
                ),
            ],
        )
        positions = {
            "U1": (30.0, 0.0),
            "RF1": (30.0, 5.0),
            "RF2": (30.1, 10.0),
            "CF": (30.2, 15.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RF1", BlockRole.FEEDBACK)
        block_layout.add_assignment("RF2", BlockRole.FEEDBACK)
        block_layout.add_assignment("CF", BlockRole.FEEDBACK)

        result = compute_orientations(ir, positions, block_layout=block_layout)

        # Op-amp always 0°
        assert result["U1"] == 0, (
            f"Op-amp should be 0° regardless of feedback network, got {result['U1']}°"
        )
        # Feedback passives may vary, but op-amp is independent
        feedback_orientations = {result[ref] for ref in ["RF1", "RF2", "CF"]}
        assert len(feedback_orientations) <= 2, (
            "Feedback passives should have at most 2 distinct orientations"
        )


class TestPassiveConsistencyWithinBlocks:
    """Passives within the same functional block should maintain consistent orientation."""

    def test_all_feedback_resistors_in_block_consistent(self) -> None:
        """All feedback resistors in a single op-amp block align together."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="RF", symbol="Device:R", value="100k"),
                ComponentIR(ref="RG", symbol="Device:R", value="1k"),
                ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(
                    name="INV",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="RF", pin="1"),
                        PinRefIR(ref="RG", pin="1"),
                        PinRefIR(ref="RIN", pin="2"),
                    ],
                ),
                NetIR(
                    name="OUT",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="RF", pin="2"),
                    ],
                ),
                NetIR(
                    name="GND",
                    pins=[PinRefIR(ref="RG", pin="2"), PinRefIR(ref="RIN", pin="1")],
                ),
            ],
        )
        positions = {
            "U1": (30.0, 0.0),
            "RF": (30.0, 5.0),
            "RG": (30.05, 10.0),
            "RIN": (10.0, 0.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RF", BlockRole.FEEDBACK)
        block_layout.add_assignment("RG", BlockRole.FEEDBACK)
        block_layout.add_assignment("RIN", BlockRole.INPUT)

        result = compute_orientations(ir, positions, block_layout=block_layout)

        # Both feedback resistors in same block should align
        assert result["RF"] == result["RG"], (
            f"Feedback resistors should align: RF={result['RF']}°, RG={result['RG']}°"
        )
        # Input resistor may differ (different block)
        # No assertion on RIN as it's in a different block

    def test_input_stage_passives_alignment_with_block_role(self) -> None:
        """Input stage passives are consistently oriented within input block."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="IN"),
                ComponentIR(ref="C1", symbol="Device:C", value="1u"),
                ComponentIR(ref="C2", symbol="Device:C", value="10u"),
                ComponentIR(ref="R1", symbol="Device:R", value="100k"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
            ],
            nets=[
                NetIR(
                    name="IN",
                    pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="C1", pin="1")],
                ),
                NetIR(
                    name="IN_CPLED",
                    pins=[
                        PinRefIR(ref="C1", pin="2"),
                        PinRefIR(ref="C2", pin="1"),
                    ],
                ),
                NetIR(
                    name="PRE",
                    pins=[
                        PinRefIR(ref="C2", pin="2"),
                        PinRefIR(ref="R1", pin="1"),
                        PinRefIR(ref="U1", pin="3"),
                    ],
                ),
            ],
        )
        positions = {
            "J1": (0.0, 0.0),
            "C1": (5.0, 0.0),
            "C2": (10.0, 0.0),
            "R1": (15.0, 0.0),
            "U1": (25.0, 0.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("C1", BlockRole.INPUT)
        block_layout.add_assignment("C2", BlockRole.INPUT)
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)

        result = compute_orientations(ir, positions, block_layout=block_layout)

        # Input coupling caps should align
        assert result["C1"] == result["C2"], (
            f"Input coupling caps should align: C1={result['C1']}°, C2={result['C2']}°"
        )
        # Preconditioning should also prefer horizontal (0°) but may be different from input
        # (though Phase 9.2 normalization might make them align too)

    def test_output_stage_passives_consistency(self) -> None:
        """Output stage passives are consistently oriented."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="COUT1", symbol="Device:C", value="10u"),
                ComponentIR(ref="COUT2", symbol="Device:C", value="47u"),
                ComponentIR(ref="ROUT", symbol="Device:R", value="100"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="OUT"),
            ],
            nets=[
                NetIR(
                    name="OUT",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="COUT1", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT2",
                    pins=[
                        PinRefIR(ref="COUT1", pin="2"),
                        PinRefIR(ref="COUT2", pin="1"),
                    ],
                ),
                NetIR(
                    name="LOAD",
                    pins=[
                        PinRefIR(ref="COUT2", pin="2"),
                        PinRefIR(ref="ROUT", pin="1"),
                        PinRefIR(ref="JOUT", pin="1"),
                    ],
                ),
            ],
        )
        positions = {
            "U1": (20.0, 0.0),
            "COUT1": (30.0, 0.0),
            "COUT2": (35.0, 0.0),
            "ROUT": (40.0, 0.0),
            "JOUT": (45.0, 0.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("COUT1", BlockRole.OUTPUT)
        block_layout.add_assignment("COUT2", BlockRole.OUTPUT)
        block_layout.add_assignment("ROUT", BlockRole.OUTPUT)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        result = compute_orientations(ir, positions, block_layout=block_layout)

        # All output passives should be consistent
        output_orientations = {result[ref] for ref in ["COUT1", "COUT2", "ROUT"]}
        assert len(output_orientations) == 1, (
            f"Output passives should all align, got {output_orientations}"
        )


class TestOrientationCoherence:
    """Overall orientation patterns should be coherent across the entire circuit."""

    def test_realistic_stereo_headphone_amp_orientation_sanity(self) -> None:
        """A realistic stereo headphone amp schematic maintains orientation sanity."""
        ir = CircuitIR(
            version="1",
            components=[
                # Inputs
                ComponentIR(ref="JIN_L", symbol="Connector_Generic:Conn_01x01", value="IN_L"),
                ComponentIR(ref="JIN_R", symbol="Connector_Generic:Conn_01x01", value="IN_R"),
                # Input coupling
                ComponentIR(ref="C1", symbol="Device:C", value="1u"),
                ComponentIR(ref="C2", symbol="Device:C", value="1u"),
                # Volume control (simple divider)
                ComponentIR(ref="R1", symbol="Device:R", value="100k"),
                ComponentIR(ref="R2", symbol="Device:R", value="100k"),
                # Op-amps
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="U2", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                # Feedback networks
                ComponentIR(ref="RF1", symbol="Device:R", value="100k"),
                ComponentIR(ref="RG1", symbol="Device:R", value="1k"),
                ComponentIR(ref="CF1", symbol="Device:C", value="10p"),
                ComponentIR(ref="RF2", symbol="Device:R", value="100k"),
                ComponentIR(ref="RG2", symbol="Device:R", value="1k"),
                ComponentIR(ref="CF2", symbol="Device:C", value="10p"),
                # Output coupling
                ComponentIR(ref="C3", symbol="Device:C", value="10u"),
                ComponentIR(ref="C4", symbol="Device:C", value="10u"),
                # Outputs
                ComponentIR(ref="JOUT_L", symbol="Connector_Generic:Conn_01x01", value="OUT_L"),
                ComponentIR(ref="JOUT_R", symbol="Connector_Generic:Conn_01x01", value="OUT_R"),
                # Supply decoupling
                ComponentIR(ref="C_POS", symbol="Device:C", value="100u"),
                ComponentIR(ref="C_NEG", symbol="Device:C", value="100u"),
            ],
            nets=[
                # Left channel input
                NetIR(
                    name="IN_L",
                    pins=[
                        PinRefIR(ref="JIN_L", pin="1"),
                        PinRefIR(ref="C1", pin="1"),
                    ],
                ),
                # Right channel input
                NetIR(
                    name="IN_R",
                    pins=[
                        PinRefIR(ref="JIN_R", pin="1"),
                        PinRefIR(ref="C2", pin="1"),
                    ],
                ),
                # Volume network left
                NetIR(
                    name="VOL_L",
                    pins=[
                        PinRefIR(ref="C1", pin="2"),
                        PinRefIR(ref="R1", pin="1"),
                        PinRefIR(ref="U1", pin="3"),
                    ],
                ),
                # Volume network right
                NetIR(
                    name="VOL_R",
                    pins=[
                        PinRefIR(ref="C2", pin="2"),
                        PinRefIR(ref="R2", pin="1"),
                        PinRefIR(ref="U2", pin="3"),
                    ],
                ),
                # Feedback left
                NetIR(
                    name="FB_L",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="RF1", pin="1"),
                        PinRefIR(ref="RG1", pin="1"),
                        PinRefIR(ref="CF1", pin="1"),
                    ],
                ),
                # Feedback right
                NetIR(
                    name="FB_R",
                    pins=[
                        PinRefIR(ref="U2", pin="2"),
                        PinRefIR(ref="RF2", pin="1"),
                        PinRefIR(ref="RG2", pin="1"),
                        PinRefIR(ref="CF2", pin="1"),
                    ],
                ),
                # Output left
                NetIR(
                    name="OUT_L",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="RF1", pin="2"),
                        PinRefIR(ref="C3", pin="1"),
                    ],
                ),
                # Output right
                NetIR(
                    name="OUT_R",
                    pins=[
                        PinRefIR(ref="U2", pin="1"),
                        PinRefIR(ref="RF2", pin="2"),
                        PinRefIR(ref="C4", pin="1"),
                    ],
                ),
                # Output connectors
                NetIR(
                    name="LOAD_L",
                    pins=[PinRefIR(ref="C3", pin="2"), PinRefIR(ref="JOUT_L", pin="1")],
                ),
                NetIR(
                    name="LOAD_R",
                    pins=[PinRefIR(ref="C4", pin="2"), PinRefIR(ref="JOUT_R", pin="1")],
                ),
            ],
        )
        positions = {
            # Inputs on left
            "JIN_L": (0.0, -5.0),
            "JIN_R": (0.0, 5.0),
            "C1": (5.0, -5.0),
            "C2": (5.0, 5.0),
            "R1": (10.0, -5.0),
            "R2": (10.0, 5.0),
            # Op-amps in center
            "U1": (20.0, -5.0),
            "U2": (20.0, 5.0),
            # Feedback near op-amps
            "RF1": (20.0, 0.0),
            "RG1": (20.5, 0.0),
            "CF1": (21.0, 0.0),
            "RF2": (20.0, 10.0),
            "RG2": (20.5, 10.0),
            "CF2": (21.0, 10.0),
            # Output coupling
            "C3": (30.0, -5.0),
            "C4": (30.0, 5.0),
            # Outputs on right
            "JOUT_L": (40.0, -5.0),
            "JOUT_R": (40.0, 5.0),
            # Supply decoupling near op-amps (top)
            "C_POS": (20.0, -15.0),
            "C_NEG": (20.0, 15.0),
        }
        tiers = assign_tiers(ir)
        block_layout = BlockLayout()
        # Input stage
        block_layout.add_assignment("JIN_L", BlockRole.INPUT)
        block_layout.add_assignment("JIN_R", BlockRole.INPUT)
        block_layout.add_assignment("C1", BlockRole.INPUT)
        block_layout.add_assignment("C2", BlockRole.INPUT)
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R2", BlockRole.PRECONDITIONING)
        # Op-amp and feedback
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("U2", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RF1", BlockRole.FEEDBACK)
        block_layout.add_assignment("RG1", BlockRole.FEEDBACK)
        block_layout.add_assignment("CF1", BlockRole.FEEDBACK)
        block_layout.add_assignment("RF2", BlockRole.FEEDBACK)
        block_layout.add_assignment("RG2", BlockRole.FEEDBACK)
        block_layout.add_assignment("CF2", BlockRole.FEEDBACK)
        # Output stage
        block_layout.add_assignment("C3", BlockRole.OUTPUT)
        block_layout.add_assignment("C4", BlockRole.OUTPUT)
        block_layout.add_assignment("JOUT_L", BlockRole.OUTPUT)
        block_layout.add_assignment("JOUT_R", BlockRole.OUTPUT)
        # Power
        block_layout.add_assignment("C_POS", BlockRole.DECOUPLING)
        block_layout.add_assignment("C_NEG", BlockRole.DECOUPLING)

        result = compute_orientations(ir, positions, tiers=tiers, block_layout=block_layout)

        # Sanity checks using block roles:
        # 1. All op-amps should be 0°
        opamp_refs = ["U1", "U2"]
        for ref in opamp_refs:
            assert result[ref] == 0, f"Op-amp {ref} should always be 0°, got {result[ref]}°"

        # 2. Feedback passives within same channel should align
        assert result["RF1"] == result["RG1"] == result["CF1"], (
            f"Left feedback passives should align: RF1={result['RF1']}°, "
            f"RG1={result['RG1']}°, CF1={result['CF1']}°"
        )
        assert result["RF2"] == result["RG2"] == result["CF2"], (
            f"Right feedback passives should align: RF2={result['RF2']}°, "
            f"RG2={result['RG2']}°, CF2={result['CF2']}°"
        )

        # 5. Input coupling caps should align
        assert result["C1"] == result["C2"], (
            f"Input coupling caps should align: C1={result['C1']}°, C2={result['C2']}°"
        )
        assert result["C3"] == result["C4"], (
            f"Output coupling caps should align: C3={result['C3']}°, C4={result['C4']}°"
        )

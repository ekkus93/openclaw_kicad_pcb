"""Phase 9.3: passive consistency and orientation coherence tests."""

from __future__ import annotations

from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.layout import compute_orientations
from kicad_pcb.tier import assign_tiers


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

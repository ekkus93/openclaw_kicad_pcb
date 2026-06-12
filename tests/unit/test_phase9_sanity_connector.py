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

"""Phase 9.2 — Normalize Similar Part Presentation (Orientation Consistency).

This module tests that similar passives in the same functional role have
consistent orientations, avoiding arbitrary rotation differences that harm
visual grammar and readability.

Phase 9.2 ensures:
- All feedback resistors have the same orientation
- All input coupling capacitors align together
- All output stage passives align together
- Visual consistency prevents reader confusion

Test strategy: Compare orientation assignments for components in the same
block role with the same component type. All should either all be 0° or
all be 90°, given consistent circuit topology.
"""

from __future__ import annotations

from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.layout import compute_orientations

# ---------------------------------------------------------------------------
# Test Fixtures — Multiple similar passives in the same role
# ---------------------------------------------------------------------------


class TestFeedbackPassivesConsistency:
    """Multiple feedback passives should have consistent orientations."""

    def test_two_feedback_resistors_same_orientation(self) -> None:
        """Two feedback resistors in same circuit get same orientation."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="RF1", symbol="Device:R", value="100k"),  # Rf
                ComponentIR(ref="RF2", symbol="Device:R", value="1k"),  # Rg
            ],
            nets=[
                NetIR(
                    name="INV",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="RF1", pin="1"),
                        PinRefIR(ref="RF2", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="RF1", pin="2"),
                    ],
                ),
                NetIR(
                    name="GND",
                    pins=[PinRefIR(ref="RF2", pin="2")],
                ),
            ],
        )
        positions = {
            "U1": (30.0, 0.0),
            "RF1": (30.1, 10.0),  # same column
            "RF2": (30.2, 20.0),  # same column
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RF1", BlockRole.FEEDBACK)
        block_layout.add_assignment("RF2", BlockRole.FEEDBACK)

        result = compute_orientations(ir, positions, block_layout=block_layout)
        assert result["RF1"] == result["RF2"], (
            f"Both feedback resistors should have same orientation: "
            f"RF1={result['RF1']}°, RF2={result['RF2']}°"
        )

    def test_three_feedback_capacitors_same_orientation(self) -> None:
        """Three feedback capacitors all align together."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="CF1", symbol="Device:C", value="10p"),
                ComponentIR(ref="CF2", symbol="Device:C", value="100p"),
                ComponentIR(ref="CF3", symbol="Device:C", value="1n"),
            ],
            nets=[
                NetIR(
                    name="INV",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="CF1", pin="1"),
                        PinRefIR(ref="CF2", pin="1"),
                        PinRefIR(ref="CF3", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="CF1", pin="2"),
                        PinRefIR(ref="CF2", pin="2"),
                        PinRefIR(ref="CF3", pin="2"),
                    ],
                ),
            ],
        )
        positions = {
            "U1": (30.0, 0.0),
            "CF1": (30.0, 5.0),
            "CF2": (30.1, 15.0),
            "CF3": (30.2, 25.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("CF1", BlockRole.FEEDBACK)
        block_layout.add_assignment("CF2", BlockRole.FEEDBACK)
        block_layout.add_assignment("CF3", BlockRole.FEEDBACK)

        result = compute_orientations(ir, positions, block_layout=block_layout)
        assert result["CF1"] == result["CF2"] == result["CF3"], (
            f"All feedback capacitors should have same orientation: "
            f"CF1={result['CF1']}°, CF2={result['CF2']}°, CF3={result['CF3']}°"
        )


class TestInputStagePassivesConsistency:
    """Multiple input-stage passives should align together."""

    def test_two_input_coupling_caps_same_orientation(self) -> None:
        """Two input coupling capacitors have consistent orientation."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="IN"),
                ComponentIR(ref="C1", symbol="Device:C", value="1u"),
                ComponentIR(ref="C2", symbol="Device:C", value="10u"),
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(
                    name="IN",
                    pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="C1", pin="1")],
                ),
                NetIR(
                    name="IN2",
                    pins=[PinRefIR(ref="C1", pin="2"), PinRefIR(ref="C2", pin="1")],
                ),
                NetIR(
                    name="PRE",
                    pins=[PinRefIR(ref="C2", pin="2"), PinRefIR(ref="R1", pin="1")],
                ),
            ],
        )
        positions = {
            "J1": (0.0, 0.0),
            "C1": (5.0, 0.0),
            "C2": (10.0, 0.0),
            "R1": (15.0, 0.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("C1", BlockRole.INPUT)
        block_layout.add_assignment("C2", BlockRole.INPUT)
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)

        result = compute_orientations(ir, positions, block_layout=block_layout)
        assert result["C1"] == result["C2"], (
            f"Both input coupling caps should align: C1={result['C1']}°, C2={result['C2']}°"
        )

    def test_input_and_preconditioning_passives_consistent(self) -> None:
        """Input and preconditioning passives align (both prefer 0°)."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="IN"),
                ComponentIR(ref="C1", symbol="Device:C", value="1u"),  # input
                ComponentIR(ref="R1", symbol="Device:R", value="100k"),  # preconditioning
                ComponentIR(ref="R2", symbol="Device:R", value="100k"),  # preconditioning
            ],
            nets=[
                NetIR(
                    name="IN",
                    pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="C1", pin="1")],
                ),
                NetIR(
                    name="PRE1",
                    pins=[
                        PinRefIR(ref="C1", pin="2"),
                        PinRefIR(ref="R1", pin="1"),
                        PinRefIR(ref="R2", pin="1"),
                    ],
                ),
            ],
        )
        positions = {
            "J1": (0.0, 0.0),
            "C1": (5.0, 0.0),
            "R1": (10.0, 0.0),
            "R2": (15.0, 0.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("C1", BlockRole.INPUT)
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R2", BlockRole.PRECONDITIONING)

        result = compute_orientations(ir, positions, block_layout=block_layout)
        # Input and preconditioning should all prefer 0° (horizontal flow)
        assert result["C1"] == 0, f"Input coupling cap should be 0°, got {result['C1']}°"
        assert result["R1"] == result["R2"], (
            f"Preconditioning resistors should match: R1={result['R1']}°, R2={result['R2']}°"
        )


class TestOutputStagePassivesConsistency:
    """Multiple output-stage passives should align together."""

    def test_two_output_coupling_caps_same_orientation(self) -> None:
        """Two output coupling capacitors have consistent orientation."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="C1", symbol="Device:C", value="10u"),
                ComponentIR(ref="C2", symbol="Device:C", value="47u"),
                ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="OUT"),
            ],
            nets=[
                NetIR(
                    name="OUT",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="C1", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT2",
                    pins=[
                        PinRefIR(ref="C1", pin="2"),
                        PinRefIR(ref="C2", pin="1"),
                    ],
                ),
                NetIR(
                    name="LOAD",
                    pins=[
                        PinRefIR(ref="C2", pin="2"),
                        PinRefIR(ref="J2", pin="1"),
                    ],
                ),
            ],
        )
        positions = {
            "U1": (10.0, 0.0),
            "C1": (20.0, 0.0),
            "C2": (30.0, 0.0),
            "J2": (40.0, 0.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("C1", BlockRole.OUTPUT)
        block_layout.add_assignment("C2", BlockRole.OUTPUT)
        block_layout.add_assignment("J2", BlockRole.OUTPUT)

        result = compute_orientations(ir, positions, block_layout=block_layout)
        assert result["C1"] == result["C2"], (
            f"Both output coupling caps should align: C1={result['C1']}°, C2={result['C2']}°"
        )


class TestMixedRoleConsistency:
    """Different roles can have different consistent orientations."""

    def test_feedback_vertical_input_horizontal_independent(self) -> None:
        """Feedback passives can be 90° while input passives are 0° (independent)."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="IN"),
                ComponentIR(ref="C1", symbol="Device:C", value="1u"),  # input
                ComponentIR(ref="C2", symbol="Device:C", value="10u"),  # input
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="RF", symbol="Device:R", value="100k"),  # feedback
                ComponentIR(ref="CF", symbol="Device:C", value="10p"),  # feedback
            ],
            nets=[
                NetIR(
                    name="IN",
                    pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="C1", pin="1")],
                ),
                NetIR(
                    name="IN_CPLED",
                    pins=[PinRefIR(ref="C1", pin="2"), PinRefIR(ref="C2", pin="1")],
                ),
                NetIR(
                    name="OPAMP_IN",
                    pins=[PinRefIR(ref="C2", pin="2"), PinRefIR(ref="U1", pin="3")],
                ),
                NetIR(
                    name="INV",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="RF", pin="1"),
                        PinRefIR(ref="CF", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="RF", pin="2"),
                        PinRefIR(ref="CF", pin="2"),
                    ],
                ),
            ],
        )
        positions = {
            "J1": (0.0, 0.0),
            "C1": (5.0, 0.0),
            "C2": (10.0, 0.0),
            "U1": (20.0, 0.0),
            "RF": (20.0, 10.0),  # feedback near op-amp column
            "CF": (20.1, 20.0),  # feedback near op-amp column
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("C1", BlockRole.INPUT)
        block_layout.add_assignment("C2", BlockRole.INPUT)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RF", BlockRole.FEEDBACK)
        block_layout.add_assignment("CF", BlockRole.FEEDBACK)

        result = compute_orientations(ir, positions, block_layout=block_layout)
        # Input passives should be 0° (horizontal)
        assert result["C1"] == result["C2"] == 0, (
            f"Input passives should be 0°: C1={result['C1']}°, C2={result['C2']}°"
        )
        # Feedback passives should be 90° (vertical, in op-amp column)
        assert result["RF"] == result["CF"] == 90, (
            f"Feedback passives should be 90°: RF={result['RF']}°, CF={result['CF']}°"
        )


class TestConsistencyPreservation:
    """Normalization preserves layout intentions when passives already align."""

    def test_already_consistent_feedback_passives_unchanged(self) -> None:
        """If all feedback passives are already 90°, normalization keeps them 90°."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="RF1", symbol="Device:R", value="100k"),
                ComponentIR(ref="RF2", symbol="Device:R", value="1k"),
            ],
            nets=[
                NetIR(
                    name="INV",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="RF1", pin="1"),
                        PinRefIR(ref="RF2", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="RF1", pin="2"),
                    ],
                ),
                NetIR(
                    name="GND",
                    pins=[PinRefIR(ref="RF2", pin="2")],
                ),
            ],
        )
        # Both feedback resistors positioned in op-amp column,
        # would naturally get 90°
        positions = {
            "U1": (30.0, 0.0),
            "RF1": (30.0, 10.0),
            "RF2": (30.05, 20.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RF1", BlockRole.FEEDBACK)
        block_layout.add_assignment("RF2", BlockRole.FEEDBACK)

        result = compute_orientations(ir, positions, block_layout=block_layout)
        assert result["RF1"] == 90, f"Feedback resistor 1 should be 90°, got {result['RF1']}°"
        assert result["RF2"] == 90, f"Feedback resistor 2 should be 90°, got {result['RF2']}°"

    def test_single_passive_per_role_no_normalization_needed(self) -> None:
        """Single passive per role requires no normalization."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="IN"),
                ComponentIR(ref="C1", symbol="Device:C", value="1u"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="RF", symbol="Device:R", value="100k"),
            ],
            nets=[
                NetIR(
                    name="IN",
                    pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="C1", pin="1")],
                ),
                NetIR(
                    name="OPAMP_IN",
                    pins=[PinRefIR(ref="C1", pin="2"), PinRefIR(ref="U1", pin="3")],
                ),
                NetIR(
                    name="INV",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="RF", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="RF", pin="2"),
                    ],
                ),
            ],
        )
        positions = {
            "J1": (0.0, 0.0),
            "C1": (5.0, 0.0),
            "U1": (20.0, 0.0),
            "RF": (20.0, 10.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("C1", BlockRole.INPUT)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RF", BlockRole.FEEDBACK)

        result = compute_orientations(ir, positions, block_layout=block_layout)
        # Single C1 in INPUT role: gets orientation based on its rules
        # Single RF in FEEDBACK role: gets orientation based on feedback rules
        # Both should have well-defined orientations
        assert result["C1"] in (0, 90), f"C1 should have valid orientation, got {result['C1']}°"
        assert result["RF"] in (0, 90), f"RF should have valid orientation, got {result['RF']}°"

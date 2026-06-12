"""Phase 9.2 — Normalize Similar Part Presentation: mixed role and preservation tests."""

from __future__ import annotations

import pytest

from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.layout import compute_orientations

pytestmark = pytest.mark.unit


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

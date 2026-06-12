"""Phase 9.1: connector, op-amp, stage passive, and consistency orientation tests."""

from __future__ import annotations

from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.layout import compute_orientations


class TestConnectorOrientations:
    """Connectors should face inward from page edges.

    Input connectors (tier 0) point right (0°); output connectors (max tier)
    point left (180°).  This ensures connectors face toward the circuit,
    improving readability.
    """

    def test_input_connector_0_degrees(self) -> None:
        """Input connector at tier 0 → 0°."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="IN"),
            ],
            nets=[
                NetIR(name="IN", pins=[PinRefIR(ref="J1", pin="1")]),
            ],
        )
        tiers = {"J1": 0}
        result = compute_orientations(ir, {}, tiers=tiers)
        assert result["J1"] == 0, "Input connector at tier 0 should be 0° (facing right)"

    def test_output_connector_180_degrees(self) -> None:
        """Output connector at max tier → 180°."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="OUT"),
            ],
            nets=[
                NetIR(name="OUT", pins=[PinRefIR(ref="J2", pin="1")]),
            ],
        )
        tiers = {"J2": 3}  # max tier
        result = compute_orientations(ir, {}, tiers=tiers)
        assert result["J2"] == 180, (
            "Output connector at max tier should be 180° (facing left toward circuit)"
        )

    def test_connector_orientation_by_role(self) -> None:
        """Connector orientation can be overridden by explicit role."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="IN"),
            ],
            nets=[
                NetIR(name="IN", pins=[PinRefIR(ref="J1", pin="1")]),
            ],
        )
        tiers = {"J1": 0}
        roles = {"J1": "output"}
        result = compute_orientations(ir, {}, tiers=tiers, roles=roles)
        assert result["J1"] == 180, "Connector with role='output' should be 180° even if at tier 0"


class TestOpAmpOrientations:
    """Op-amps should maintain stable preferred orientation (0°).

    Consistent op-amp orientation across circuits supports rapid pattern
    recognition (inputs feed in from left, output goes right).
    """

    def test_opamp_always_0_degrees(self) -> None:
        """Op-amp should always rotate to 0°, not adjusted by tiers or block layout."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
            ],
            nets=[
                NetIR(name="IN+", pins=[PinRefIR(ref="U1", pin="3")]),
                NetIR(name="IN-", pins=[PinRefIR(ref="U1", pin="2")]),
                NetIR(name="OUT", pins=[PinRefIR(ref="U1", pin="1")]),
            ],
        )
        # Even if U1 is placed at max tier or in various positions, it should always be 0°
        tiers = {"U1": 99}  # very high tier
        positions = {"U1": (100.0, 100.0)}
        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        result = compute_orientations(ir, positions, tiers=tiers, block_layout=block_layout)
        assert result["U1"] == 0, (
            "Op-amp should always be 0° for consistent input-left/output-right orientation"
        )


class TestInputStagePassiveOrientations:
    """Input-stage passives should prefer horizontal alignment.

    Input coupling capacitors and input resistors support left-to-right
    signal flow. Horizontal (0°) orientation is preferred unless position
    heuristic strongly disagrees.
    """

    def test_input_coupling_capacitor_horizontal(self) -> None:
        """Input coupling cap (INPUT block role) → prefer 0°."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="IN"),
                ComponentIR(ref="C1", symbol="Device:C", value="1u"),  # coupling
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(
                    name="IN",
                    pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="C1", pin="1")],
                ),
                NetIR(
                    name="CPLED",
                    pins=[PinRefIR(ref="C1", pin="2"), PinRefIR(ref="R1", pin="1")],
                ),
            ],
        )
        positions = {
            "J1": (0.0, 0.0),
            "C1": (5.0, 0.0),
            "R1": (10.0, 0.0),  # horizontal alignment
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("C1", BlockRole.INPUT)
        result = compute_orientations(ir, positions, block_layout=block_layout)
        assert result["C1"] == 0, (
            "Input-stage coupling capacitor should prefer 0° to support left-to-right flow"
        )

    def test_preconditioning_resistor_horizontal(self) -> None:
        """Preconditioning resistor (volume/bias) → prefer 0°."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="R2", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(
                    name="IN",
                    pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="1")],
                ),
                NetIR(
                    name="OUT",
                    pins=[PinRefIR(ref="R1", pin="2"), PinRefIR(ref="R2", pin="2")],
                ),
            ],
        )
        positions = {
            "R1": (5.0, 0.0),
            "R2": (15.0, 0.0),  # horizontal neighbors
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)
        result = compute_orientations(ir, positions, block_layout=block_layout)
        assert result["R1"] == 0, (
            "Preconditioning passives should prefer 0° for signal flow clarity"
        )


class TestOutputStagePassiveOrientations:
    """Output-stage passives should prefer horizontal alignment.

    Output coupling capacitors and output resistors support left-to-right
    signal flow. Horizontal (0°) orientation is preferred.
    """

    def test_output_coupling_capacitor_horizontal(self) -> None:
        """Output coupling cap (OUTPUT block role) → prefer 0°."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="C1", symbol="Device:C", value="10u"),  # output coupling
                ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="OUT"),
            ],
            nets=[
                NetIR(
                    name="OUT",
                    pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="C1", pin="1")],
                ),
                NetIR(
                    name="OUT_COUPLED",
                    pins=[PinRefIR(ref="C1", pin="2"), PinRefIR(ref="J2", pin="1")],
                ),
            ],
        )
        positions = {
            "U1": (10.0, 0.0),
            "C1": (20.0, 0.0),
            "J2": (30.0, 0.0),  # left-to-right
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("C1", BlockRole.OUTPUT)
        result = compute_orientations(ir, positions, block_layout=block_layout)
        assert result["C1"] == 0, (
            "Output-stage passives should prefer 0° to support left-to-right signal flow"
        )


class TestConsistencyWithinRoles:
    """Similar passives in the same role should have consistent orientations.

    Avoid arbitrary rotation of equivalent passive roles, which would create
    inconsistent visual grammar and harm readability.
    """

    def test_two_series_resistors_same_orientation(self) -> None:
        """Two series resistors in same circuit should have same orientation."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="R2", symbol="Device:R", value="10k"),
                ComponentIR(ref="R3", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(
                    name="SIG1",
                    pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="1")],
                ),
                NetIR(
                    name="SIG2",
                    pins=[PinRefIR(ref="R2", pin="2"), PinRefIR(ref="R3", pin="1")],
                ),
            ],
        )
        positions = {
            "R1": (0.0, 0.0),
            "R2": (10.0, 0.0),
            "R3": (20.0, 0.0),  # left-to-right series chain
        }
        result = compute_orientations(ir, positions)
        # Both R1 and R3 are series passives with horizontal neighbours
        assert result["R1"] == result["R3"], (
            "Series resistors with same topology should have same orientation"
        )

    def test_multiple_bypass_caps_same_orientation(self) -> None:
        """Multiple bypass capacitors should all be 90°."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="C1", symbol="Device:C", value="100n"),
                ComponentIR(ref="C2", symbol="Device:C", value="100n"),
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="R2", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(
                    name="SIG1",
                    pins=[
                        PinRefIR(ref="R1", pin="2"),
                        PinRefIR(ref="C1", pin="1"),
                        PinRefIR(ref="R2", pin="1"),
                    ],
                ),
                NetIR(
                    name="GND",
                    pins=[PinRefIR(ref="C1", pin="2"), PinRefIR(ref="C2", pin="2")],
                ),
                NetIR(
                    name="SIG2",
                    pins=[PinRefIR(ref="C2", pin="1"), PinRefIR(ref="R2", pin="2")],
                ),
            ],
        )
        positions = {
            "C1": (10.0, 10.0),
            "C2": (20.0, 10.0),
            "R1": (0.0, 0.0),
            "R2": (30.0, 0.0),
        }
        result = compute_orientations(ir, positions)
        # Both C1 and C2 are bypass caps (signal + GND pins)
        assert result["C1"] == 90, "C1 should be 90° (bypass cap)"
        assert result["C2"] == 90, "C2 should be 90° (bypass cap)"
        assert result["C1"] == result["C2"], "Multiple bypass caps should all have same orientation"

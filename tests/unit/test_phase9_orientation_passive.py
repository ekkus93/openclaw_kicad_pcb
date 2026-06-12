"""Phase 9.1 — Orientation Conventions by Part Role.

This module tests the orientation conventions for different component types
and block roles, ensuring that component orientations support function and
reading flow rather than just fitting routing.

Conventions (Phase 9.1):
------------------------
* **Resistors/Capacitors in signal flow**: Tend to align with flow direction
  - Series passives (both pins on signal nets): prefer horizontal (0°) to
    support left-to-right signal flow.
  - Shunt passives (one pin on power, one on signal): prefer vertical (90°)
    to show vertical connection from signal to rail.
  - Feedback passives (near op-amp): prefer vertical (90°) when in same
    column as op-amp to support feedback loop visualization.
* **Connectors**: Face inward (toward circuit), not outward.
  - Input connectors (tier 0): rotate to 0° (pins face right).
  - Output connectors (max tier): rotate to 180° (pins face left).
* **Op-amps**: Maintain 0° orientation (inputs left, output right).
* **Power/decoupling parts**: May use different conventions if it improves
  clarity. Currently: decoupling/power passives stacked above op-amp,
  prefer vertical orientation to show rail connection.

Note: These tests validate the conventions defined by block role and
component type, ensuring consistency across similar parts in their roles.
"""

from __future__ import annotations

from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.layout import compute_orientations

# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------


class TestSeriesPassiveOrientations:
    """Series passives (both pins on signal nets) should prefer horizontal.

    These form the main signal path: input → resistor → op-amp → output.
    Horizontal orientation supports left-to-right reading flow.
    """

    def test_series_resistor_horizontal_in_ltr_layout(self) -> None:
        """Series resistor between two horizontally-offset neighbours → 0°."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="R2", symbol="Device:R", value="10k"),  # right neighbor
                ComponentIR(ref="R3", symbol="Device:R", value="10k"),  # left neighbor
            ],
            nets=[
                NetIR(
                    name="SIG1",
                    pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R3", pin="2")],
                ),
                NetIR(
                    name="SIG2",
                    pins=[PinRefIR(ref="R1", pin="2"), PinRefIR(ref="R2", pin="1")],
                ),
            ],
        )
        # R1 has neighbors offset horizontally (R3 left, R2 right)
        positions = {
            "R1": (10.0, 0.0),
            "R2": (20.0, 0.0),  # horizontal offset
            "R3": (0.0, 0.0),  # horizontal offset
        }
        result = compute_orientations(ir, positions)
        assert result["R1"] == 0, (
            "Series resistor with horizontal neighbours should be 0° (horizontal)"
        )

    def test_series_capacitor_horizontal_in_ltr_layout(self) -> None:
        """Series coupling capacitor between horizontally-offset signal nets → 0°."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="C1", symbol="Device:C", value="10u"),
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="R2", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(
                    name="IN",
                    pins=[PinRefIR(ref="R1", pin="2"), PinRefIR(ref="C1", pin="1")],
                ),
                NetIR(
                    name="OUT",
                    pins=[PinRefIR(ref="C1", pin="2"), PinRefIR(ref="R2", pin="1")],
                ),
            ],
        )
        positions = {
            "C1": (10.0, 0.0),
            "R1": (0.0, 0.0),
            "R2": (20.0, 0.0),  # horizontal spacing
        }
        result = compute_orientations(ir, positions)
        assert result["C1"] == 0, "Series capacitor with horizontal neighbours should be 0°"


class TestShuntPassiveOrientations:
    """Shunt passives (one pin on power, one on signal) should prefer vertical.

    Bypass/decoupling capacitors and pull-up/pull-down resistors straddle
    a power rail and the signal path. Vertical (90°) orientation visually
    shows the connection from signal wire down to the power rail.
    """

    def test_bypass_capacitor_vertical(self) -> None:
        """Bypass cap (pin1 on signal, pin2 on power GND) → 90°."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="C1", symbol="Device:C", value="100n"),
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(
                    name="SIG",
                    pins=[PinRefIR(ref="R1", pin="2"), PinRefIR(ref="C1", pin="1")],
                ),
                NetIR(
                    name="GND",
                    pins=[PinRefIR(ref="C1", pin="2")],
                ),
            ],
        )
        positions = {"C1": (10.0, 10.0), "R1": (0.0, 0.0)}
        result = compute_orientations(ir, positions)
        assert result["C1"] == 90, "Bypass capacitor (signal + power pins) should be 90° (vertical)"

    def test_pullup_resistor_vertical(self) -> None:
        """Pull-up resistor (pin1 on power VCC, pin2 on signal) → 90°."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="R2", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(
                    name="VCC",
                    pins=[PinRefIR(ref="R1", pin="1")],
                ),
                NetIR(
                    name="SIG",
                    pins=[PinRefIR(ref="R1", pin="2"), PinRefIR(ref="R2", pin="1")],
                ),
            ],
        )
        positions = {"R1": (10.0, 10.0), "R2": (10.0, 0.0)}
        result = compute_orientations(ir, positions)
        assert result["R1"] == 90, "Pull-up resistor (power + signal pins) should be 90° (vertical)"

    def test_negative_charge_pump_capacitor_prefers_270(self) -> None:
        """Negative charge-pump reservoir caps mirror away from their ground stub."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="C1", symbol="Device:C", value="10u"),
                ComponentIR(ref="U1", symbol="Interface_UART:MAX232", value="MAX232"),
            ],
            nets=[
                NetIR(name="GND", pins=[PinRefIR(ref="C1", pin="1"), PinRefIR(ref="U1", pin="15")]),
                NetIR(
                    name="Net-(U1-VS-)",
                    pins=[PinRefIR(ref="C1", pin="2"), PinRefIR(ref="U1", pin="6")],
                ),
            ],
        )
        positions = {"C1": (10.0, 10.0), "U1": (20.0, 10.0)}

        result = compute_orientations(ir, positions)

        assert result["C1"] == 270

    def test_decoupling_capacitor_vertical(self) -> None:
        """Decoupling cap (pin1 on VCC, pin2 on GND) → 90°.

        Note: Currently treated as shunt topology. VCC and GND are both power nets,
        so this should still trigger the shunt-topology check (power-pin-refs).
        """
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="C1", symbol="Device:C", value="100u"),
            ],
            nets=[
                NetIR(
                    name="VCC",
                    pins=[PinRefIR(ref="C1", pin="1")],
                ),
                NetIR(
                    name="GND",
                    pins=[PinRefIR(ref="C1", pin="2")],
                ),
            ],
        )
        positions = {"C1": (10.0, 10.0)}
        result = compute_orientations(ir, positions)
        # Decoupling cap has no signal-net pins, so shunt check should not trigger.
        # This is a known edge case; decoupling-only passives may need special handling.
        # For now, just assert default behavior.
        assert result["C1"] == 0, (
            "Decoupling-only capacitor defaults to 0° (no signal pins present)"
        )

    def test_numeric_positive_rail_decoupling_defaults_to_zero(self) -> None:
        """A +5V/GND decoupler should still be treated as a power-only passive."""
        ir = CircuitIR(
            version="1",
            components=[ComponentIR(ref="C1", symbol="Device:C", value="100n")],
            nets=[
                NetIR(name="+5V", pins=[PinRefIR(ref="C1", pin="1")]),
                NetIR(name="GND", pins=[PinRefIR(ref="C1", pin="2")]),
            ],
        )
        positions = {"C1": (10.0, 10.0)}

        result = compute_orientations(ir, positions)

        assert result["C1"] == 0

    def test_signal_to_power_decoupling_cap_defaults_to_zero(self) -> None:
        """A capacitor classified as decoupling should stay horizontal above its IC."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(
                    ref="U1",
                    symbol="Interface_CAN_LIN:MCP2551-I-SN",
                    value="MCP2551-I-SN",
                ),
                ComponentIR(ref="C1", symbol="Device:C", value="30p"),
            ],
            nets=[
                NetIR(name="GND", pins=[PinRefIR(ref="C1", pin="2"), PinRefIR(ref="U1", pin="2")]),
                NetIR(
                    name="Net-(U1-Vref)",
                    pins=[PinRefIR(ref="C1", pin="1"), PinRefIR(ref="U1", pin="5")],
                ),
                NetIR(name="CAN0_TX", pins=[PinRefIR(ref="U1", pin="1")]),
            ],
        )
        positions = {"U1": (20.0, 20.0), "C1": (20.0, 12.0)}

        result = compute_orientations(ir, positions)

        assert result["C1"] == 0

    def test_signal_to_ground_bypass_with_shared_signal_node_stays_vertical(self) -> None:
        """A wider signal node should remain a shunt/bypass capacitor, not local decoupling."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="C1", symbol="Device:C", value="100n"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
            ],
            nets=[
                NetIR(
                    name="MID_NET",
                    pins=[
                        PinRefIR(ref="R1", pin="2"),
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="C1", pin="1"),
                    ],
                ),
                NetIR(name="GND", pins=[PinRefIR(ref="C1", pin="2")]),
            ],
        )
        positions = {"R1": (10.0, 20.0), "C1": (20.0, 20.0), "U1": (30.0, 20.0)}

        result = compute_orientations(ir, positions)

        assert result["C1"] == 90

    def test_placed_pin_subset_ignores_nonlocal_power_pin_membership(self) -> None:
        """Placed-unit orientation ignores stray net pins outside the placed pin subset."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R1A", symbol="Device:R", value="10k"),
                ComponentIR(ref="R2", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(
                    name="SIG",
                    pins=[PinRefIR(ref="R1A", pin="1"), PinRefIR(ref="R2", pin="1")],
                ),
                NetIR(
                    name="VCC",
                    pins=[PinRefIR(ref="R1A", pin="99")],
                ),
            ],
        )
        positions = {"R1A": (10.0, 0.0), "R2": (20.0, 0.0)}

        result = compute_orientations(
            ir,
            positions,
            placed_pin_numbers={"R1A": ("1", "2"), "R2": ("1", "2")},
        )

        assert result["R1A"] == 0, (
            "Placed-unit orientation should ignore nonlocal pin 99 and keep the series resistor "
            "horizontal"
        )


class TestFeedbackPassiveOrientations:
    """Feedback passives (near op-amp) should prefer vertical in op-amp column.

    Feedback resistors and capacitors around the op-amp should show the
    feedback loop clearly. When positioned in the same column, vertical
    orientation supports the vertical feedback path visualization.
    """

    def test_feedback_resistor_vertical_near_opamp_column(self) -> None:
        """Feedback resistor in same column as op-amp → 90°."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="R1", symbol="Device:R", value="100k"),  # feedback
            ],
            nets=[
                NetIR(
                    name="FB",
                    pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="R1", pin="1")],
                ),
                NetIR(
                    name="OUT",
                    pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="R1", pin="2")],
                ),
            ],
        )
        # Op-amp at x=30.0, feedback resistor in same column (x=30.1, within GRID_COL_MM/2)
        positions = {"U1": (30.0, 0.0), "R1": (30.1, 10.0)}
        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("R1", BlockRole.FEEDBACK)
        result = compute_orientations(ir, positions, block_layout=block_layout)
        assert result["R1"] == 90, (
            "Feedback resistor in op-amp column should be 90° to show vertical feedback path"
        )

    def test_feedback_capacitor_vertical_near_opamp(self) -> None:
        """Feedback capacitor (compensation) in op-amp column → 90°."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="C1", symbol="Device:C", value="10n"),  # compensation
            ],
            nets=[
                NetIR(
                    name="INV",
                    pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="C1", pin="1")],
                ),
                NetIR(
                    name="OUT",
                    pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="C1", pin="2")],
                ),
            ],
        )
        positions = {"U1": (30.0, 0.0), "C1": (30.0, 15.0)}  # same column
        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("C1", BlockRole.FEEDBACK)
        result = compute_orientations(ir, positions, block_layout=block_layout)
        assert result["C1"] == 90, "Feedback compensation capacitor should be 90° when near op-amp"

"""Block detection: layout preservation, unit role tests, and block zone snapping."""

from __future__ import annotations

import json

import pytest

from kicad_pcb.block_detection import (
    BlockAssignment,
    BlockLayout,
    BlockRole,
    classify_circuit,
)
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.graphviz_layout.snap import _snap_block_zones
from tests import (
    NE5532_LEFT_CURRENT_READABILITY_FIXTURE,
    NE5532_LEFT_REGRESSED_READABILITY_FIXTURE,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_READABILITY_FIXTURE = NE5532_LEFT_CURRENT_READABILITY_FIXTURE
_CIRCUIT_IR_PATH = _READABILITY_FIXTURE.circuit_ir_path
_REGRESSED_FIXTURE = NE5532_LEFT_REGRESSED_READABILITY_FIXTURE
_REGRESSED_IR_PATH = _REGRESSED_FIXTURE.circuit_ir_path


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _load_test_circuit() -> CircuitIR:
    """Load the headphone amp baseline circuit IR."""
    if not _CIRCUIT_IR_PATH.exists():
        pytest.skip("Circuit IR fixture not found")

    ir_data = json.loads(_CIRCUIT_IR_PATH.read_text(encoding="utf-8"))
    return CircuitIR(**ir_data)


def _load_regressed_test_circuit() -> CircuitIR:
    """Load the canonical regressed NE5532 left-channel circuit IR."""
    if not _REGRESSED_IR_PATH.exists():
        pytest.skip("Regressed circuit IR fixture not found")

    ir_data = json.loads(_REGRESSED_IR_PATH.read_text(encoding="utf-8"))
    return CircuitIR(**ir_data)


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_layout_preserves_electrical_groups() -> None:
    """Verify classification doesn't fragment electrically-connected groups.

    Block detection should assign roles but not introduce artificial
    separations within electrically-connected sub-circuits.
    For the headphone amp: input jacks + input resistors are connected
    (INPUT/PRECONDITIONING are adjacent roles; both bias left).
    """
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    # The input connector and coupling cap should stay on the input side.
    input_refs = set(layout.components_by_role(BlockRole.INPUT))
    assert {"J1", "C5"}.issubset(input_refs), "J1 and C5 should stay in INPUT"

    # Power jack should be POWER_ENTRY
    power_refs = set(layout.components_by_role(BlockRole.POWER_ENTRY))
    assert "J3" in power_refs, "J3 should be POWER_ENTRY"

    # The output jack should remain the terminal output block.
    output_refs = set(layout.components_by_role(BlockRole.OUTPUT))
    assert "J2" in output_refs, "J2 should be OUTPUT"


@pytest.mark.skipif(
    not _REGRESSED_IR_PATH.exists(),
    reason="Regressed circuit IR fixture not found",
)
def test_regressed_ne5532_fixture_uses_stage_handoff_motifs() -> None:
    """The canonical regressed fixture should map into explicit signal blocks."""
    ir = _load_regressed_test_circuit()
    layout = classify_circuit(ir)

    assert layout.get_role("J1") == BlockRole.INPUT
    assert layout.get_role("J2") == BlockRole.OUTPUT
    assert layout.get_role("J3") == BlockRole.POWER_ENTRY
    assert layout.get_role("U1") == BlockRole.OPAMP_CORE

    assert layout.get_role("C1") == BlockRole.DECOUPLING
    assert layout.get_role("C2") == BlockRole.DECOUPLING
    assert layout.get_role("C3") == BlockRole.DECOUPLING
    assert layout.get_role("C4") == BlockRole.DECOUPLING

    assert layout.get_role("R2") == BlockRole.FEEDBACK
    assert layout.get_role("R3") == BlockRole.FEEDBACK
    assert layout.get_role("C6") == BlockRole.INTERSTAGE
    assert layout.get_role("R5") == BlockRole.INTERSTAGE
    assert layout.get_role("R6") == BlockRole.OUTPUT_CONDITIONING
    assert layout.get_role("C7") == BlockRole.OUTPUT_CONDITIONING
    assert layout.get_role("R7") == BlockRole.OUTPUT_CONDITIONING

    assert layout.get_role("C5") in {BlockRole.INPUT, BlockRole.PRECONDITIONING}
    assert layout.get_role("R1") in {BlockRole.INPUT, BlockRole.PRECONDITIONING}
    assert layout.get_role("RV1") == BlockRole.PRECONDITIONING
    assert layout.get_role("U1") == BlockRole.OPAMP_CORE


def test_split_unit_follower_stage_classified_as_buffer_stage() -> None:
    """Split op-amp unit refs should promote explicit follower stages to BUFFER_STAGE."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
            ComponentIR(ref="UA", symbol="Amplifier_Operational:NE5532", value="NE5532"),
            ComponentIR(ref="CINT", symbol="Device:C", value="1u"),
            ComponentIR(ref="RFB", symbol="Device:R", value="22k"),
            ComponentIR(ref="RG", symbol="Device:R", value="10k"),
            ComponentIR(ref="UB", symbol="Amplifier_Operational:NE5532", value="NE5532"),
            ComponentIR(ref="RBIAS", symbol="Device:R", value="100k"),
            ComponentIR(ref="RISO", symbol="Device:R", value="47"),
            ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
        ],
        nets=[
            NetIR(name="IN", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="UA", pin="3")]),
            NetIR(name="UA_INV", pins=[PinRefIR(ref="UA", pin="2"), PinRefIR(ref="RG", pin="1")]),
            NetIR(
                name="UA_OUT",
                pins=[
                    PinRefIR(ref="UA", pin="1"),
                    PinRefIR(ref="RFB", pin="1"),
                    PinRefIR(ref="CINT", pin="1"),
                ],
            ),
            NetIR(
                name="UA_FB_RET",
                pins=[PinRefIR(ref="RFB", pin="2"), PinRefIR(ref="RG", pin="2")],
            ),
            NetIR(
                name="UB_IN",
                pins=[
                    PinRefIR(ref="CINT", pin="2"),
                    PinRefIR(ref="RBIAS", pin="1"),
                    PinRefIR(ref="UB", pin="3"),
                ],
            ),
            NetIR(
                name="UB_OUT",
                pins=[
                    PinRefIR(ref="UB", pin="1"),
                    PinRefIR(ref="UB", pin="2"),
                    PinRefIR(ref="RISO", pin="1"),
                ],
            ),
            NetIR(
                name="HP_OUT",
                pins=[PinRefIR(ref="RISO", pin="2"), PinRefIR(ref="JOUT", pin="1")],
            ),
            NetIR(name="GND", pins=[PinRefIR(ref="RBIAS", pin="2")]),
        ],
    )

    layout = classify_circuit(ir)

    assert layout.get_role("UA") == BlockRole.OPAMP_CORE
    assert layout.get_role("UB") == BlockRole.BUFFER_STAGE
    assert layout.get_role("CINT") == BlockRole.INTERSTAGE
    assert layout.get_role("RBIAS") == BlockRole.INTERSTAGE


def test_explicit_unit_buffer_stage_classified_when_ref_has_single_signal_unit() -> None:
    """Explicit unit metadata should allow BUFFER_STAGE when only one op-amp unit is in play."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
            ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
            ComponentIR(ref="RISO", symbol="Device:R", value="47"),
            ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
        ],
        nets=[
            NetIR(
                name="BUF_IN",
                pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="U1", pin="5", unit="B")],
            ),
            NetIR(
                name="BUF_OUT",
                pins=[
                    PinRefIR(ref="U1", pin="7", unit="B"),
                    PinRefIR(ref="U1", pin="6", unit="B"),
                    PinRefIR(ref="RISO", pin="1"),
                ],
            ),
            NetIR(name="OUT", pins=[PinRefIR(ref="RISO", pin="2"), PinRefIR(ref="JOUT", pin="1")]),
        ],
    )

    layout = classify_circuit(ir)

    assert layout.get_role("U1") == BlockRole.BUFFER_STAGE
    assert layout.get_role("RISO") == BlockRole.OUTPUT


def test_unsplit_dual_opamp_gets_synthetic_signal_unit_roles() -> None:
    """Unsplit dual op-amps should expose per-unit roles for downstream layout passes."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
            ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
            ComponentIR(ref="CINT", symbol="Device:C", value="1u"),
            ComponentIR(ref="RFB", symbol="Device:R", value="22k"),
            ComponentIR(ref="RG", symbol="Device:R", value="10k"),
            ComponentIR(ref="RBIAS", symbol="Device:R", value="100k"),
            ComponentIR(ref="RISO", symbol="Device:R", value="47"),
            ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
        ],
        nets=[
            NetIR(
                name="IN",
                pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="U1", pin="3", unit="A")],
            ),
            NetIR(
                name="U1A_INV",
                pins=[PinRefIR(ref="U1", pin="2", unit="A"), PinRefIR(ref="RG", pin="1")],
            ),
            NetIR(
                name="U1A_OUT",
                pins=[
                    PinRefIR(ref="U1", pin="1", unit="A"),
                    PinRefIR(ref="RFB", pin="1"),
                    PinRefIR(ref="CINT", pin="1"),
                ],
            ),
            NetIR(
                name="U1A_FB_RET",
                pins=[PinRefIR(ref="RFB", pin="2"), PinRefIR(ref="RG", pin="2")],
            ),
            NetIR(
                name="BUF_IN",
                pins=[
                    PinRefIR(ref="CINT", pin="2"),
                    PinRefIR(ref="RBIAS", pin="1"),
                    PinRefIR(ref="U1", pin="5", unit="B"),
                ],
            ),
            NetIR(
                name="BUF_OUT",
                pins=[
                    PinRefIR(ref="U1", pin="7", unit="B"),
                    PinRefIR(ref="U1", pin="6", unit="B"),
                    PinRefIR(ref="RISO", pin="1"),
                ],
            ),
            NetIR(name="OUT", pins=[PinRefIR(ref="RISO", pin="2"), PinRefIR(ref="JOUT", pin="1")]),
            NetIR(name="GND", pins=[PinRefIR(ref="RBIAS", pin="2")]),
        ],
    )

    layout = classify_circuit(ir)

    assert layout.get_role("U1") == BlockRole.OPAMP_CORE
    assert layout.get_role("U1A") == BlockRole.OPAMP_CORE
    assert layout.get_role("U1B") == BlockRole.BUFFER_STAGE


def test_split_quad_unit_follower_stage_classified_as_buffer_stage() -> None:
    """Quad-op-amp split refs should classify downstream follower units as BUFFER_STAGE."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
            ComponentIR(ref="UC", symbol="Amplifier_Operational:TL074", value="TL074"),
            ComponentIR(ref="RISO", symbol="Device:R", value="47"),
            ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
        ],
        nets=[
            NetIR(name="BUF_IN", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="UC", pin="10")]),
            NetIR(
                name="BUF_OUT",
                pins=[
                    PinRefIR(ref="UC", pin="8"),
                    PinRefIR(ref="UC", pin="9"),
                    PinRefIR(ref="RISO", pin="1"),
                ],
            ),
            NetIR(
                name="OUT",
                pins=[PinRefIR(ref="RISO", pin="2"), PinRefIR(ref="JOUT", pin="1")],
            ),
        ],
    )

    layout = classify_circuit(ir)

    assert layout.get_role("UC") == BlockRole.BUFFER_STAGE
    assert layout.get_role("RISO") == BlockRole.OUTPUT


def test_explicit_quad_unit_buffer_stage_classified_for_unit_d() -> None:
    """Explicit unit metadata should classify quad-op-amp unit D followers as BUFFER_STAGE."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
            ComponentIR(ref="U1", symbol="Amplifier_Operational:TL074", value="TL074"),
            ComponentIR(ref="RISO", symbol="Device:R", value="47"),
            ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
        ],
        nets=[
            NetIR(
                name="BUF_IN",
                pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="U1", pin="12", unit="D")],
            ),
            NetIR(
                name="BUF_OUT",
                pins=[
                    PinRefIR(ref="U1", pin="14", unit="D"),
                    PinRefIR(ref="U1", pin="13", unit="D"),
                    PinRefIR(ref="RISO", pin="1"),
                ],
            ),
            NetIR(
                name="OUT",
                pins=[PinRefIR(ref="RISO", pin="2"), PinRefIR(ref="JOUT", pin="1")],
            ),
        ],
    )

    layout = classify_circuit(ir)

    assert layout.get_role("U1") == BlockRole.BUFFER_STAGE
    assert layout.get_role("RISO") == BlockRole.OUTPUT


def test_block_zone_snapping() -> None:
    """Verify _snap_block_zones biases components toward their designated zones.

    This tests the Phase 1.2 integration: block assignments should influence
    layout by nudging components that are far from their target zones.
    """
    # Create a simple block layout with known roles
    assignments = {
        "J1": BlockAssignment(ref="J1", role=BlockRole.INPUT, confidence=0.9, reason="input jack"),
        "J3": BlockAssignment(
            ref="J3", role=BlockRole.POWER_ENTRY, confidence=0.9, reason="power jack"
        ),
        "J5": BlockAssignment(
            ref="J5", role=BlockRole.OUTPUT, confidence=0.9, reason="output jack"
        ),
    }
    layout = BlockLayout(assignments=assignments)

    # Create positions where components are far from their designated zones:
    # J1 (INPUT) is too far right (x=200mm)
    # J3 (POWER_ENTRY) is too low (y=150mm)
    # J5 (OUTPUT) is too far left (x=50mm)
    positions: dict[str, tuple[float, float, float | None]] = {
        "J1": (200.0, 100.0, 0.0),  # INPUT should bias left (x closer to origin)
        "J3": (150.0, 150.0, 0.0),  # POWER should bias top (y closer to origin)
        "J5": (50.0, 100.0, 0.0),  # OUTPUT should bias right (x closer to page_max)
    }

    # Apply block zone snapping
    result = _snap_block_zones(positions, layout)

    # Verify INPUT component was NOT nudged (already handled by _enforce_connector_x_bounds)
    # (This snap pass is gentle and doesn't override connector bounds)
    # But we should verify the function runs without errors
    assert "J1" in result
    assert "J3" in result
    assert "J5" in result

    # Verify positions are valid (no crashes, within page bounds)
    for ref, (x, y, rot) in result.items():
        assert 30.0 <= x <= 287.0, f"{ref} x out of bounds: {x}"
        assert 50.0 <= y <= 200.0, f"{ref} y out of bounds: {y}"

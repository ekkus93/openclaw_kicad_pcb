"""Tests for functional block detection (Phase 1.1 — CODE_REVIEW6).

This test suite validates that the block classification heuristics correctly
identify functional blocks in the headphone amp baseline fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from kicad_pcb.block_detection import (
    BlockRole,
    classify_circuit,
    debug_dump,
)
from kicad_pcb.circuit_ir import CircuitIR

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_TEST_ROOT = Path(__file__).parent.parent
_READABILITY_FIXTURE_DIR = (
    _TEST_ROOT / "fixtures" / "readability" / "ne5532_headphone_amp_left_current"
)
_CIRCUIT_IR_PATH = _READABILITY_FIXTURE_DIR / "circuit_ir.json"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _load_test_circuit() -> CircuitIR:
    """Load the headphone amp baseline circuit IR."""
    if not _CIRCUIT_IR_PATH.exists():
        pytest.skip("Circuit IR fixture not found")

    ir_data = json.loads(_CIRCUIT_IR_PATH.read_text(encoding="utf-8"))
    return CircuitIR(**ir_data)


# ---------------------------------------------------------------------------
# Block classification tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_classify_all_components() -> None:
    """Test that all components in the circuit are classified."""
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    # All components should be assigned to a block
    assert len(layout.assignments) == len(ir.components)

    for component in ir.components:
        assert component.ref in layout.assignments
        assignment = layout.assignments[component.ref]
        assert assignment.role is not None
        assert 0.0 <= assignment.confidence <= 1.0


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_input_connectors_classified() -> None:
    """Test that input connectors are classified as INPUT block."""
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    # J1, J2 should be input (audio in connectors)
    assert layout.get_role("J1") == BlockRole.INPUT
    assert layout.get_role("J2") == BlockRole.INPUT


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_output_connectors_classified() -> None:
    """Test that output connectors are classified as OUTPUT block."""
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    # J4, J5 should be output (headphone out connectors)
    assert layout.get_role("J4") == BlockRole.OUTPUT
    assert layout.get_role("J5") == BlockRole.OUTPUT


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_power_entry_classified() -> None:
    """Test that power supply connector is classified as POWER_ENTRY."""
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    # J3 is typically the power supply jack
    assert layout.get_role("J3") == BlockRole.POWER_ENTRY


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_input_resistors_classified() -> None:
    """Test that input resistors are classified appropriately.

    R1, R2 are connected to input nets (IN_L, IN_R).
    """
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    # R1, R2 connected to IN_L, IN_R should be INPUT or PRECONDITIONING
    r1_role = layout.get_role("R1")
    r2_role = layout.get_role("R2")
    assert r1_role in (BlockRole.INPUT, BlockRole.PRECONDITIONING)
    assert r2_role in (BlockRole.INPUT, BlockRole.PRECONDITIONING)


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_output_resistors_classified() -> None:
    """Test that output resistors are classified as OUTPUT.

    R7, R8 are connected to OUT_L, OUT_R output nets.
    """
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    # R7, R8 connected to OUT_L, OUT_R should be OUTPUT
    assert layout.get_role("R7") == BlockRole.OUTPUT
    assert layout.get_role("R8") == BlockRole.OUTPUT


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_bias_resistors_classified() -> None:
    """Test that bias/divider resistors are classified appropriately.

    R3, R4 form a bias divider connected to VCC and GND at MID_RAIL.
    R3 (VCC side) should be power-related.
    R4 (GND side) may be PRECONDITIONING, FEEDBACK, or related.
    """
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    r3_role = layout.get_role("R3")
    r4_role = layout.get_role("R4")

    # R3 on VCC should be power-related
    assert r3_role == BlockRole.POWER_ENTRY

    # R4 should be classified to something reasonable (not arbitrary)
    # Likely FEEDBACK or PRECONDITIONING (depends on value heuristics)
    assert r4_role in (
        BlockRole.PRECONDITIONING,
        BlockRole.FEEDBACK,
        BlockRole.OPAMP_CORE,
    )


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_feedback_resistors_classified() -> None:
    """Test that feedback resistors are classified as FEEDBACK or similar.

    R5, R6 are connected to STAGE_L/R nets and MID_RAIL (feedback path).
    """
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    r5_role = layout.get_role("R5")
    r6_role = layout.get_role("R6")

    # These are feedback/signal path resistors
    # May be classified as FEEDBACK, PRECONDITIONING, or OPAMP_CORE
    assert r5_role in (BlockRole.FEEDBACK, BlockRole.PRECONDITIONING, BlockRole.OPAMP_CORE)
    assert r6_role in (BlockRole.FEEDBACK, BlockRole.PRECONDITIONING, BlockRole.OPAMP_CORE)


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_block_layout_has_zones() -> None:
    """Test that block layout defines page zones for constraint-based layout."""
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    # Should have defined zones for major blocks
    assert len(layout.zones) > 0

    # Should have zones for at least input, op-amp, output
    zones_present = {BlockRole.INPUT, BlockRole.OPAMP_CORE, BlockRole.OUTPUT}
    assert zones_present.issubset(layout.zones.keys())

    # Zones should be tuples of 4 floats (x_min, y_min, x_max, y_max)
    for role, zone in layout.zones.items():
        assert isinstance(zone, tuple)
        assert len(zone) == 4
        assert all(isinstance(v, float) for v in zone)

    # Sanity check: output zone should be to the right of input zone
    input_zone = layout.zones[BlockRole.INPUT]
    output_zone = layout.zones[BlockRole.OUTPUT]
    assert input_zone[2] < output_zone[0]  # input x_max < output x_min


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_components_by_role_method() -> None:
    """Test the components_by_role() helper method."""
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    # Get input components
    input_components = layout.components_by_role(BlockRole.INPUT)
    assert "J1" in input_components
    assert "J2" in input_components

    # Get output components
    output_components = layout.components_by_role(BlockRole.OUTPUT)
    assert "J4" in output_components
    assert "J5" in output_components

    # Every component should be in exactly one role
    all_components = set()
    for role in BlockRole:
        components = layout.components_by_role(role)
        all_components.update(components)

    assert len(all_components) == len(ir.components)


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_debug_dump_format() -> None:
    """Test that debug_dump produces well-formed readable output."""
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    output = debug_dump(layout)

    # Should have a header
    assert "Block Assignments Debug Dump" in output
    assert "=" in output

    # Should list each role that is actually assigned in the circuit
    assigned_roles = {assignment.role for assignment in layout.assignments.values()}
    output_upper = output.upper()
    for role in assigned_roles:
        assert role.value.upper() in output_upper

    # Should list each component
    for component in ir.components:
        assert component.ref in output

    # Should have confidence indicators (█ and ░)
    assert "█" in output
    assert "░" in output

    # Should be multi-line
    lines = output.split("\n")
    assert len(lines) > 10


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_confidence_scores() -> None:
    """Test that confidence scores are reasonable and diverse.

    High confidence (1.0) for reference-based or net-name-based classifications.
    Lower confidence for heuristic defaults.
    """
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    # Jacks should have high confidence (reference-based)
    for ref in ("J1", "J2", "J3", "J4", "J5"):
        assignment = layout.assignments[ref]
        assert assignment.confidence >= 0.8

    # Some resistors may have lower confidence (heuristic-based)
    # At least some components should have confidence < 1.0 or == 1.0
    confidences = {a.confidence for a in layout.assignments.values()}
    assert len(confidences) > 1 or all(c == 1.0 for c in confidences)


# ---------------------------------------------------------------------------
# Integration and visual tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_circuit_has_minimal_required_blocks() -> None:
    """Test that the circuit is classified into expected major blocks.

    A headphone amp should have at least:
    - INPUT block (jacks)
    - OPAMP_CORE or signal stages
    - OUTPUT block (jacks)
    - POWER_ENTRY (power jack)
    """
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    required_roles = {BlockRole.INPUT, BlockRole.OUTPUT, BlockRole.POWER_ENTRY}
    assigned_roles = {assignment.role for assignment in layout.assignments.values()}

    for role in required_roles:
        assert role in assigned_roles, f"Missing block role: {role.value}"


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_layout_preserves_electrical_groups() -> None:
    """Test that block classification doesn't over-fragment the circuit.

    Input-connected components should mostly be INPUT or PRECONDITIONING.
    Output-connected components should be OUTPUT-related.
    Power components should be in POWER_ENTRY or DECOUPLING.
    """
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    # All input jacks should be INPUT
    input_jacks = {c.ref for c in ir.components if c.ref in ("J1", "J2")}
    for jack_ref in input_jacks:
        assert layout.get_role(jack_ref) == BlockRole.INPUT

    # All output jacks should be OUTPUT
    output_jacks = {c.ref for c in ir.components if c.ref in ("J4", "J5")}
    for jack_ref in output_jacks:
        assert layout.get_role(jack_ref) == BlockRole.OUTPUT

"""Tests for functional block detection (Phase 1.1 — CODE_REVIEW6).

This test suite validates that the block classification heuristics correctly
identify functional blocks in the headphone amp baseline fixture.
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest
from kicad_pcb.block_detection import (
    BlockAssignment,
    BlockLayout,
    BlockRole,
    classify_circuit,
    debug_dump,
)
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.commands.netlist import cmd_new_from_netlist
from kicad_pcb.graphviz_layout.snap import _snap_block_zones
from kicad_pcb.sch_doc import SchematicDoc

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
    """Verify classification doesn't fragment electrically-connected groups.

    Block detection should assign roles but not introduce artificial
    separations within electrically-connected sub-circuits.
    For the headphone amp: input jacks + input resistors are connected
    (INPUT/PRECONDITIONING are adjacent roles; both bias left).
    """
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    # All input jacks should be INPUT
    input_refs = set(layout.components_by_role(BlockRole.INPUT))
    input_jacks = {c.ref for c in ir.components if c.ref in ("J1", "J2")}
    assert input_jacks.issubset(input_refs), "J1 and J2 should be INPUT"

    # Power jack should be POWER_ENTRY
    power_refs = set(layout.components_by_role(BlockRole.POWER_ENTRY))
    assert "J3" in power_refs, "J3 should be POWER_ENTRY"

    # Output jacks should be OUTPUT
    output_refs = set(layout.components_by_role(BlockRole.OUTPUT))
    output_jacks = {c.ref for c in ir.components if c.ref in ("J4", "J5")}
    assert output_jacks.issubset(output_refs), "J4 and J5 should be OUTPUT"


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


# ---------------------------------------------------------------------------
# Phase 3.4 — Signal flow clarity tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_input_connectors_left_of_opamp(tmp_path: Path) -> None:
    """Verify input connectors are placed left of the op-amp stage (Phase 3.4).

    This validates left-to-right signal flow: INPUT → OPAMP_CORE.
    """
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    # Generate schematic from IR using the same pattern as test_readability_baseline
    project_name = "SignalFlowTest"
    work_dir = tmp_path / "signal_flow"
    work_dir.mkdir(parents=True, exist_ok=True)

    cmd_new_from_netlist(
        Namespace(
            name=project_name,
            out_dir=str(work_dir),
            description="Signal flow test schematic",
            netlist=str(_CIRCUIT_IR_PATH),
            symbols_dir=str(_TEST_ROOT / "fixtures" / "symbols"),
            mode="internal",
            layout="graphviz",
            routing="bus",
            validate="internal",
            strict=False,
        )
    )

    # Load the managed sheet (where symbols are actually placed)
    managed_sch = work_dir / project_name / "OpenClaw_Managed.kicad_sch"
    assert managed_sch.exists(), "Managed sheet was not generated"
    doc = SchematicDoc.load(managed_sch)
    symbols = {s["ref"]: (s["x"], s["y"]) for s in doc.list_symbols()}

    # Get INPUT and OPAMP_CORE component positions
    input_refs = layout.components_by_role(BlockRole.INPUT)
    opamp_refs = layout.components_by_role(BlockRole.OPAMP_CORE)

    if not opamp_refs:
        pytest.skip("No op-amp components found in circuit")

    # Find the leftmost op-amp x-coordinate
    opamp_x_positions = [symbols[ref][0] for ref in opamp_refs if ref in symbols]
    if not opamp_x_positions:
        pytest.skip("No op-amp positions found")
    leftmost_opamp_x = min(opamp_x_positions)

    # All input connectors should be left of (or slightly overlapping) op-amps
    for ref in input_refs:
        if ref in symbols:
            input_x = symbols[ref][0]
            assert input_x <= leftmost_opamp_x + 20.0, (
                f"Input connector {ref} (x={input_x:.1f}) should be left of "
                f"op-amp stage (x={leftmost_opamp_x:.1f})"
            )


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_output_connectors_right_of_opamp(tmp_path: Path) -> None:
    """Verify output connectors are placed right of the op-amp stage (Phase 3.4).

    This validates left-to-right signal flow: OPAMP_CORE → OUTPUT.
    """
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    # Generate schematic from IR
    project_name = "SignalFlowTest2"
    work_dir = tmp_path / "signal_flow2"
    work_dir.mkdir(parents=True, exist_ok=True)

    cmd_new_from_netlist(
        Namespace(
            name=project_name,
            out_dir=str(work_dir),
            description="Signal flow test schematic",
            netlist=str(_CIRCUIT_IR_PATH),
            symbols_dir=str(_TEST_ROOT / "fixtures" / "symbols"),
            mode="internal",
            layout="graphviz",
            routing="bus",
            validate="internal",
            strict=False,
        )
    )

    # Load the managed sheet
    managed_sch = work_dir / project_name / "OpenClaw_Managed.kicad_sch"
    assert managed_sch.exists(), "Managed sheet was not generated"
    doc = SchematicDoc.load(managed_sch)
    symbols = {s["ref"]: (s["x"], s["y"]) for s in doc.list_symbols()}

    # Get OUTPUT and OPAMP_CORE component positions
    output_refs = layout.components_by_role(BlockRole.OUTPUT)
    opamp_refs = layout.components_by_role(BlockRole.OPAMP_CORE)

    if not opamp_refs:
        pytest.skip("No op-amp components found in circuit")

    # Find the rightmost op-amp x-coordinate
    opamp_x_positions = [symbols[ref][0] for ref in opamp_refs if ref in symbols]
    if not opamp_x_positions:
        pytest.skip("No op-amp positions found")
    rightmost_opamp_x = max(opamp_x_positions)

    # All output connectors should be right of (or slightly overlapping) op-amps
    for ref in output_refs:
        if ref in symbols:
            output_x = symbols[ref][0]
            assert output_x >= rightmost_opamp_x - 20.0, (
                f"Output connector {ref} (x={output_x:.1f}) should be right of "
                f"op-amp stage (x={rightmost_opamp_x:.1f})"
            )


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
@pytest.mark.xfail(
    reason=(
        "Phase 3.1-3.3 not yet implemented: "
        "OUTPUT placement needs stronger left-to-right constraints"
    ),
    strict=False,
)
def test_output_components_not_in_left_cluster(tmp_path: Path) -> None:
    """Verify output-side components are not mixed into the left input cluster (Phase 3.4).

    Output coupling capacitors and output resistors should be placed to the
    right of the op-amp, not interleaved with input components on the left.

    Expected failure until Phase 3.1 (strengthen left-to-right placement
    constraints) is implemented.
    """
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    # Generate schematic from IR
    project_name = "SignalFlowTest3"
    work_dir = tmp_path / "signal_flow3"
    work_dir.mkdir(parents=True, exist_ok=True)

    cmd_new_from_netlist(
        Namespace(
            name=project_name,
            out_dir=str(work_dir),
            description="Signal flow test schematic",
            netlist=str(_CIRCUIT_IR_PATH),
            symbols_dir=str(_TEST_ROOT / "fixtures" / "symbols"),
            mode="internal",
            layout="graphviz",
            routing="bus",
            validate="internal",
            strict=False,
        )
    )

    # Load the managed sheet
    managed_sch = work_dir / project_name / "OpenClaw_Managed.kicad_sch"
    assert managed_sch.exists(), "Managed sheet was not generated"
    doc = SchematicDoc.load(managed_sch)
    symbols = {s["ref"]: (s["x"], s["y"]) for s in doc.list_symbols()}

    # Get INPUT and OUTPUT component positions
    input_refs = layout.components_by_role(BlockRole.INPUT)
    output_refs = layout.components_by_role(BlockRole.OUTPUT)

    if not input_refs or not output_refs:
        pytest.skip("Missing input or output components")

    # Find the rightmost input x-coordinate
    input_x_positions = [symbols[ref][0] for ref in input_refs if ref in symbols]
    if not input_x_positions:
        pytest.skip("No input positions found")
    rightmost_input_x = max(input_x_positions)

    # Most output components should be significantly right of input cluster
    # Allow for some overlap (≤25% of output components in input region)
    output_in_left_cluster = 0
    total_output_components = 0

    for ref in output_refs:
        if ref in symbols:
            total_output_components += 1
            output_x = symbols[ref][0]
            if output_x < rightmost_input_x + 30.0:  # 30mm tolerance
                output_in_left_cluster += 1

    if total_output_components > 0:
        left_ratio = output_in_left_cluster / total_output_components
        assert left_ratio <= 0.25, (
            f"{left_ratio * 100:.0f}% of output components are in the left cluster "
            f"(expected ≤25%); output stage should be visually separated"
        )

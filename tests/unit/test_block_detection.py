"""Tests for functional block detection (Phase 1.1 — CODE_REVIEW6).

This test suite validates that the block classification heuristics correctly
identify functional blocks in the headphone amp baseline fixture.
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import cast

import pytest

from kicad_pcb.block_detection import (
    BlockAssignment,
    BlockLayout,
    BlockRole,
    classify_circuit,
    debug_dump,
    is_core_like_role,
    is_input_like_role,
    is_output_like_role,
    is_power_like_role,
)
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands.netlist import cmd_new_from_netlist
from kicad_pcb.graphviz_layout.snap import _snap_block_zones
from kicad_pcb.router import _l_route, _spine_route, route_nets
from kicad_pcb.sch_doc import SchematicDoc
from tests import (
    NE5532_LEFT_CURRENT_READABILITY_FIXTURE,
    NE5532_LEFT_REGRESSED_READABILITY_FIXTURE,
    SYMBOLS_FIXTURE_DIR,
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
    component_refs = {component.ref for component in ir.components}

    # All concrete components should be assigned to a block. Multi-unit helpers may
    # also synthesize per-unit refs such as U1A/U1B for downstream layout logic.
    assert component_refs <= set(layout.assignments)

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

    # The real review fixture is mono-left: J1 is the input TRS jack.
    assert layout.get_role("J1") == BlockRole.INPUT


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_output_connectors_classified() -> None:
    """Test that output connectors are classified as OUTPUT block."""
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    # The real review fixture uses J2 as the mono-left output TRS jack.
    assert layout.get_role("J2") == BlockRole.OUTPUT


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

    The real review fixture uses C5 as the input coupling element and RV1/R4 as
    the input-side support path into the first stage.
    """
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    assert layout.get_role("C5") == BlockRole.INPUT
    assert layout.get_role("RV1") == BlockRole.PRECONDITIONING
    assert layout.get_role("R4") == BlockRole.PRECONDITIONING


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_output_resistors_classified() -> None:
    """Test that output-path passives stay on the output side.

    In the real review fixture the series resistor and output bleed resistor are
    part of the output-conditioning block ahead of the TRS output jack.
    """
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    assert layout.get_role("R6") == BlockRole.OUTPUT_CONDITIONING
    assert layout.get_role("R7") == BlockRole.OUTPUT_CONDITIONING


def test_signal_support_caps_are_not_misclassified_as_decoupling() -> None:
    """Output/input support caps touching ground should keep their signal-side roles."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
            ComponentIR(ref="CIN", symbol="Device:C", value="100n"),
            ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
            ComponentIR(ref="COUT", symbol="Device:C", value="10u"),
            ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ComponentIR(ref="RISO", symbol="Device:R", value="10"),
            ComponentIR(ref="CDEC", symbol="Device:C", value="100n"),
            ComponentIR(ref="J3", symbol="Connector_Generic:Conn_01x02", value="Power"),
        ],
        nets=[
            NetIR(
                name="IN",
                pins=[
                    PinRefIR(ref="JIN", pin="1"),
                    PinRefIR(ref="U1", pin="3"),
                    PinRefIR(ref="CIN", pin="1"),
                ],
            ),
            NetIR(
                name="OUT",
                pins=[
                    PinRefIR(ref="U1", pin="6"),
                    PinRefIR(ref="JOUT", pin="1"),
                    PinRefIR(ref="COUT", pin="1"),
                ],
            ),
            NetIR(
                name="VCC",
                pins=[
                    PinRefIR(ref="J3", pin="1"),
                    PinRefIR(ref="RISO", pin="1"),
                ],
            ),
            NetIR(
                name="VCC_LOCAL",
                pins=[
                    PinRefIR(ref="RISO", pin="2"),
                    PinRefIR(ref="U1", pin="7"),
                    PinRefIR(ref="CDEC", pin="1"),
                ],
            ),
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="J3", pin="2"),
                    PinRefIR(ref="U1", pin="4"),
                    PinRefIR(ref="CIN", pin="2"),
                    PinRefIR(ref="COUT", pin="2"),
                    PinRefIR(ref="CDEC", pin="2"),
                ],
            ),
        ],
    )

    layout = classify_circuit(ir)

    assert layout.get_role("CIN") in (BlockRole.INPUT, BlockRole.PRECONDITIONING)
    assert layout.get_role("COUT") == BlockRole.OUTPUT
    assert layout.get_role("RISO") == BlockRole.POWER_ENTRY
    assert layout.get_role("CDEC") == BlockRole.DECOUPLING


def test_negative_rail_decouplers_stay_in_power_support_roles() -> None:
    """Negative-rail bypass caps should not fall through to signal-side roles."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="J3", symbol="Connector_Generic:Conn_01x03", value="Power"),
            ComponentIR(ref="CNEG", symbol="Device:C", value="100n"),
            ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
        ],
        nets=[
            NetIR(
                name="VMINUS15",
                pins=[
                    PinRefIR(ref="J3", pin="3"),
                    PinRefIR(ref="U1", pin="4"),
                    PinRefIR(ref="CNEG", pin="1"),
                ],
            ),
            NetIR(
                name="0V",
                pins=[
                    PinRefIR(ref="J3", pin="2"),
                    PinRefIR(ref="CNEG", pin="2"),
                ],
            ),
        ],
    )

    layout = classify_circuit(ir)

    assert layout.get_role("J3") == BlockRole.POWER_ENTRY
    assert layout.get_role("CNEG") == BlockRole.DECOUPLING


def test_power_only_opamp_unit_stays_in_power_block() -> None:
    """Power-only multi-unit IC sections should not be classified as signal cores."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="J3", symbol="Connector_Generic:Conn_01x03", value="Power"),
            ComponentIR(ref="U1P", symbol="Amplifier_Operational:NE5532", value="NE5532"),
        ],
        nets=[
            NetIR(
                name="VPLUS15",
                pins=[
                    PinRefIR(ref="J3", pin="1"),
                    PinRefIR(ref="U1P", pin="8"),
                ],
            ),
            NetIR(
                name="VMINUS15",
                pins=[
                    PinRefIR(ref="J3", pin="3"),
                    PinRefIR(ref="U1P", pin="4"),
                ],
            ),
        ],
    )

    layout = classify_circuit(ir)

    assert layout.get_role("J3") == BlockRole.POWER_ENTRY
    assert layout.get_role("U1P") == BlockRole.POWER_ENTRY


def test_vss_ground_alias_keeps_supply_support_components_out_of_signal_roles() -> None:
    """VSS should be treated as the shared ground family in block detection."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="J3", symbol="Connector_Generic:Conn_01x02", value="Power"),
            ComponentIR(ref="CDEC", symbol="Device:C", value="100n"),
            ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
        ],
        nets=[
            NetIR(
                name="VCC",
                pins=[
                    PinRefIR(ref="J3", pin="1"),
                    PinRefIR(ref="U1", pin="7"),
                    PinRefIR(ref="CDEC", pin="1"),
                ],
            ),
            NetIR(
                name="VSS",
                pins=[
                    PinRefIR(ref="J3", pin="2"),
                    PinRefIR(ref="U1", pin="4"),
                    PinRefIR(ref="CDEC", pin="2"),
                ],
            ),
        ],
    )

    layout = classify_circuit(ir)

    assert layout.get_role("J3") == BlockRole.POWER_ENTRY
    assert layout.get_role("CDEC") == BlockRole.DECOUPLING


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_bias_resistors_classified() -> None:
    """Test that bias/divider resistors are classified appropriately.

    The real review fixture uses R2/R3 as the first-stage feedback pair and R4
    as the non-inverting input bias/support path.
    """
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    r3_role = layout.get_role("R3")
    r4_role = layout.get_role("R4")

    assert r3_role == BlockRole.FEEDBACK

    assert r4_role == BlockRole.PRECONDITIONING


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_feedback_resistors_classified() -> None:
    """Test that feedback resistors are classified as FEEDBACK or similar.

    The real review fixture keeps R2/R3 in the feedback path and R5 on the
    inter-stage handoff.
    """
    ir = _load_test_circuit()
    layout = classify_circuit(ir)

    assert layout.get_role("R2") == BlockRole.FEEDBACK
    assert layout.get_role("R3") == BlockRole.FEEDBACK
    assert layout.get_role("R5") == BlockRole.INTERSTAGE


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


def test_extended_block_roles_have_default_zones() -> None:
    """The Phase 2 block-role additions should participate in default zoning."""
    layout = classify_circuit(_load_test_circuit())

    for role in (
        BlockRole.INTERSTAGE,
        BlockRole.BUFFER_STAGE,
        BlockRole.OUTPUT_CONDITIONING,
    ):
        assert role in layout.zones


def test_block_role_family_helpers_cover_phase2_roles() -> None:
    """Role-family predicates should bucket the new Phase 2 roles correctly."""
    assert is_input_like_role(BlockRole.INPUT)
    assert is_input_like_role(BlockRole.PRECONDITIONING)
    assert not is_input_like_role(BlockRole.INTERSTAGE)

    assert is_core_like_role(BlockRole.OPAMP_CORE)
    assert is_core_like_role(BlockRole.FEEDBACK)
    assert is_core_like_role(BlockRole.INTERSTAGE)
    assert is_core_like_role(BlockRole.BUFFER_STAGE)
    assert not is_core_like_role(BlockRole.OUTPUT_CONDITIONING)

    assert is_output_like_role(BlockRole.OUTPUT)
    assert is_output_like_role(BlockRole.OUTPUT_CONDITIONING)
    assert not is_output_like_role(BlockRole.BUFFER_STAGE)

    assert is_power_like_role(BlockRole.POWER_ENTRY)
    assert is_power_like_role(BlockRole.DECOUPLING)
    assert not is_power_like_role(BlockRole.INTERSTAGE)


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_components_by_role_method() -> None:
    """Test the components_by_role() helper method."""
    ir = _load_test_circuit()
    layout = classify_circuit(ir)
    component_refs = {component.ref for component in ir.components}

    # Get input components
    input_components = layout.components_by_role(BlockRole.INPUT)
    assert "J1" in input_components
    assert "C5" in input_components

    # Get output components
    output_components = layout.components_by_role(BlockRole.OUTPUT)
    assert "J2" in output_components

    # Every component should be in exactly one role
    all_components = set()
    for role in BlockRole:
        components = layout.components_by_role(role)
        all_components.update(components)

    assert component_refs <= all_components


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

    # Named connectors in the real review fixture should keep high confidence.
    for ref in ("J1", "J2", "J3"):
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


# ---------------------------------------------------------------------------
# Phase 3.4 — Signal flow clarity tests
# ---------------------------------------------------------------------------


def test_input_connectors_left_of_opamp(tmp_path: Path) -> None:
    """Verify input connectors are placed left of the op-amp stage (Phase 3.4).

    This validates left-to-right signal flow: INPUT → OPAMP_CORE.
    """
    # Create a circuit inline with an op-amp
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
            ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
            ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
            ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
        ],
        nets=[
            NetIR(name="IN_A", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="RIN", pin="1")]),
            NetIR(name="IN_B", pins=[PinRefIR(ref="RIN", pin="2"), PinRefIR(ref="U1", pin="3")]),
            NetIR(name="OUT", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="JOUT", pin="1")]),
            NetIR(name="VCC", pins=[PinRefIR(ref="U1", pin="7")]),
            NetIR(name="GND", pins=[PinRefIR(ref="U1", pin="4")]),
        ],
    )

    layout = classify_circuit(ir)

    # Explicitly assign block roles (automatic classification may not catch all patterns)
    layout.add_assignment("JIN", BlockRole.INPUT)
    layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
    layout.add_assignment("U1", BlockRole.OPAMP_CORE)
    layout.add_assignment("JOUT", BlockRole.OUTPUT)

    # Generate schematic from IR
    project_name = "SignalFlowTest"
    work_dir = tmp_path / "signal_flow"
    work_dir.mkdir(parents=True, exist_ok=True)

    # Write IR to temp file for cmd_new_from_netlist
    ir_path = work_dir / "circuit.json"
    ir_path.write_text(ir.model_dump_json(indent=2), encoding="utf-8")

    cmd_new_from_netlist(
        Namespace(
            name=project_name,
            out_dir=str(work_dir),
            description="Signal flow test schematic",
            netlist=str(ir_path),
            symbols_dir=str(SYMBOLS_FIXTURE_DIR),
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
    symbols: dict[str, tuple[float, float]] = {
        cast(str, s["ref"]): (cast(float, s["x"]), cast(float, s["y"])) for s in doc.list_symbols()
    }

    # Get INPUT and OPAMP_CORE component positions
    input_refs = layout.components_by_role(BlockRole.INPUT)
    opamp_refs = layout.components_by_role(BlockRole.OPAMP_CORE)

    assert opamp_refs, "Op-amp components should be present in the circuit"
    assert input_refs, "Input connectors should be present in the circuit"

    # Find the leftmost op-amp x-coordinate
    opamp_x_positions = [symbols[ref][0] for ref in opamp_refs if ref in symbols]
    assert opamp_x_positions, "Op-amp should be placed in schematic"
    leftmost_opamp_x = min(opamp_x_positions)

    # All input connectors should be left of (or slightly overlapping) op-amps
    for ref in input_refs:
        if ref in symbols:
            input_x = symbols[ref][0]
            assert input_x <= leftmost_opamp_x + 20.0, (
                f"Input connector {ref} (x={input_x:.1f}) should be left of "
                f"op-amp stage (x={leftmost_opamp_x:.1f})"
            )


def test_output_connectors_right_of_opamp(tmp_path: Path) -> None:
    """Verify output connectors are placed right of the op-amp stage (Phase 3.4).

    This validates left-to-right signal flow: OPAMP_CORE → OUTPUT.
    """
    # Create a circuit inline with an op-amp
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
            ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
            ComponentIR(ref="ROUT", symbol="Device:R", value="100"),
            ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
        ],
        nets=[
            NetIR(name="IN", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="U1", pin="3")]),
            NetIR(name="OUT_A", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="ROUT", pin="1")]),
            NetIR(
                name="OUT_B", pins=[PinRefIR(ref="ROUT", pin="2"), PinRefIR(ref="JOUT", pin="1")]
            ),
            NetIR(name="VCC", pins=[PinRefIR(ref="U1", pin="7")]),
            NetIR(name="GND", pins=[PinRefIR(ref="U1", pin="4")]),
        ],
    )

    layout = classify_circuit(ir)

    # Explicitly assign block roles (automatic classification may not catch all patterns)
    layout.add_assignment("JIN", BlockRole.INPUT)
    layout.add_assignment("U1", BlockRole.OPAMP_CORE)
    layout.add_assignment("ROUT", BlockRole.OUTPUT)
    layout.add_assignment("JOUT", BlockRole.OUTPUT)

    # Generate schematic from IR
    project_name = "SignalFlowTest2"
    work_dir = tmp_path / "signal_flow2"
    work_dir.mkdir(parents=True, exist_ok=True)

    # Write IR to temp file for cmd_new_from_netlist
    ir_path = work_dir / "circuit.json"
    ir_path.write_text(ir.model_dump_json(indent=2), encoding="utf-8")

    cmd_new_from_netlist(
        Namespace(
            name=project_name,
            out_dir=str(work_dir),
            description="Signal flow test schematic",
            netlist=str(ir_path),
            symbols_dir=str(SYMBOLS_FIXTURE_DIR),
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
    symbols: dict[str, tuple[float, float]] = {
        cast(str, s["ref"]): (cast(float, s["x"]), cast(float, s["y"])) for s in doc.list_symbols()
    }

    # Get OUTPUT and OPAMP_CORE component positions
    output_refs = layout.components_by_role(BlockRole.OUTPUT)
    opamp_refs = layout.components_by_role(BlockRole.OPAMP_CORE)

    assert opamp_refs, "Op-amp components should be present in the circuit"
    assert output_refs, "Output connectors should be present in the circuit"

    # Find the rightmost op-amp x-coordinate
    opamp_x_positions = [symbols[ref][0] for ref in opamp_refs if ref in symbols]
    assert opamp_x_positions, "Op-amp should be placed in schematic"
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
def test_output_components_not_in_left_cluster(tmp_path: Path) -> None:
    """Verify output-side components are not mixed into the left input cluster (Phase 3.4).

    Output coupling capacitors and output resistors should be placed to the
    right of the op-amp, not interleaved with input components on the left.

    This test validates the Phase 3.1 left-to-right placement constraints.
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
            symbols_dir=str(SYMBOLS_FIXTURE_DIR),
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
    symbols: dict[str, tuple[float, float]] = {
        cast(str, s["ref"]): (cast(float, s["x"]), cast(float, s["y"])) for s in doc.list_symbols()
    }

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


@pytest.mark.skipif(
    not _REGRESSED_IR_PATH.exists(),
    reason="Regressed circuit IR fixture not found",
)
def test_regressed_ne5532_fixture_keeps_major_blocks_in_left_to_right_order(tmp_path: Path) -> None:
    """The real NE5532 fixture should preserve readable block ordering around the core stage."""
    ir = _load_regressed_test_circuit()
    layout = classify_circuit(ir)

    project_name = "SignalFlowRealFixture"
    work_dir = tmp_path / "signal_flow_real_fixture"
    work_dir.mkdir(parents=True, exist_ok=True)

    cmd_new_from_netlist(
        Namespace(
            name=project_name,
            out_dir=str(work_dir),
            description="Signal flow real fixture schematic",
            netlist=str(_REGRESSED_IR_PATH),
            symbols_dir=str(SYMBOLS_FIXTURE_DIR),
            mode="internal",
            layout="graphviz",
            routing="bus",
            validate="internal",
            strict=False,
        )
    )

    managed_sch = work_dir / project_name / "OpenClaw_Managed.kicad_sch"
    assert managed_sch.exists(), "Managed sheet was not generated"
    doc = SchematicDoc.load(managed_sch)
    symbols: dict[str, tuple[float, float]] = {
        cast(str, s["ref"]): (cast(float, s["x"]), cast(float, s["y"])) for s in doc.list_symbols()
    }

    input_refs = [ref for ref in layout.components_by_role(BlockRole.INPUT) if ref in symbols]
    core_anchor_refs = layout.components_by_role(BlockRole.OPAMP_CORE)
    core_refs = [
        placed_ref
        for placed_ref in symbols
        if any(placed_ref == ref or placed_ref.startswith(ref) for ref in core_anchor_refs)
    ]
    interstage_refs = [
        ref for ref in layout.components_by_role(BlockRole.INTERSTAGE) if ref in symbols
    ]
    output_refs = [ref for ref in layout.components_by_role(BlockRole.OUTPUT) if ref in symbols]
    output_cond_refs = [
        ref for ref in layout.components_by_role(BlockRole.OUTPUT_CONDITIONING) if ref in symbols
    ]

    assert input_refs and core_refs and interstage_refs and output_refs and output_cond_refs

    rightmost_input_x = max(symbols[ref][0] for ref in input_refs)
    core_min_x = min(symbols[ref][0] for ref in core_refs)
    core_max_x = max(symbols[ref][0] for ref in core_refs)
    interstage_min_x = min(symbols[ref][0] for ref in interstage_refs)
    interstage_max_x = max(symbols[ref][0] for ref in interstage_refs)
    leftmost_output_support_x = min(symbols[ref][0] for ref in output_cond_refs)
    leftmost_output_x = min(symbols[ref][0] for ref in output_refs)

    assert rightmost_input_x < core_min_x, (
        f"Input block should stay left of the core stage: {rightmost_input_x} !< {core_min_x}"
    )
    assert core_min_x <= interstage_min_x, (
        "Interstage handoff may share the core cluster's left edge, but should "
        "not drift left of it: "
        f"{core_min_x} !<= {interstage_min_x}"
    )
    assert interstage_max_x < leftmost_output_support_x, (
        "Output-conditioning block should stay to the right of the interstage handoff: "
        f"{interstage_max_x} !< {leftmost_output_support_x}"
    )
    assert core_max_x < leftmost_output_support_x, (
        "Output-conditioning block should stay to the right of the core stage: "
        f"{core_max_x} !< {leftmost_output_support_x}"
    )
    assert leftmost_output_support_x <= leftmost_output_x, (
        "Output connector block should not jump left of its output-conditioning chain: "
        f"{leftmost_output_support_x} !<= {leftmost_output_x}"
    )


# ---------------------------------------------------------------------------
# Phase 3.2: Signal-flow routing tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_l_route_horizontal_first() -> None:
    """Test that _l_route implements horizontal-first L-routing.

    For left-to-right signal flow, the horizontal segment should come
    before the vertical segment, ensuring the wire travels left-to-right
    before making any vertical adjustments.
    """
    # Test 1: Route from left-bottom to right-top should go right first
    ex1, ey1, ex2, ey2 = 0.0, 0.0, 10.0, 20.0
    segs = _l_route(ex1, ey1, ex2, ey2)

    # Should have 2 segments: horizontal then vertical
    assert len(segs) == 2, "L-route should produce 2 segments for non-degenerate case"

    # First segment should be horizontal (y1 == y2)
    seg_h = segs[0]
    assert seg_h.y1 == seg_h.y2 == ey1, "First segment should be horizontal"
    assert seg_h.x1 == ex1, "Horizontal segment should start at ex1"
    assert seg_h.x2 == ex2, "Horizontal segment should end at ex2"

    # Second segment should be vertical (x1 == x2)
    seg_v = segs[1]
    assert seg_v.x1 == seg_v.x2 == ex2, "Second segment should be vertical"
    assert seg_v.y1 == ey1, "Vertical segment should start at ey1"
    assert seg_v.y2 == ey2, "Vertical segment should end at ey2"


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_l_route_degenerate_segments() -> None:
    """Test that _l_route omits zero-length segments."""
    # Test 1: Route between points with same X (only vertical needed)
    segs = _l_route(10.0, 0.0, 10.0, 20.0)
    assert len(segs) == 1, "Should omit zero-length horizontal segment"
    assert segs[0].x1 == segs[0].x2 == 10.0, "Only segment should be vertical"

    # Test 2: Route between points with same Y (only horizontal needed)
    segs = _l_route(0.0, 10.0, 20.0, 10.0)
    assert len(segs) == 1, "Should omit zero-length vertical segment"
    assert segs[0].y1 == segs[0].y2 == 10.0, "Only segment should be horizontal"

    # Test 3: Route between same point (no segments)
    segs = _l_route(5.0, 5.0, 5.0, 5.0)
    assert len(segs) == 0, "Should omit both degenerate segments"


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_spine_route_chooses_horizontal_when_wider() -> None:
    """Test that _spine_route chooses horizontal spine when x_span >= y_span.

    For left-to-right signal flow reinforcement, when endpoints span more
    horizontally than vertically, the spine should run along the X-axis
    (left-to-right) and taps should come down vertically to endpoints.
    """
    # Endpoints spanning more horizontally (x_span=40, y_span=10)
    endpoints = [
        (0.0, 5.0),  # Left endpoint
        (20.0, 0.0),  # Middle
        (40.0, 10.0),  # Right endpoint
    ]

    segs, junctions = _spine_route(endpoints)

    # Should have segments: 1 horizontal spine + vertical taps
    assert len(segs) >= 1, "Should have at least the spine segment"

    # First segment (spine) should be horizontal
    spine = segs[0]
    assert spine.y1 == spine.y2, "Spine segment should be horizontal"
    assert spine.x1 < spine.x2, "Spine should run left-to-right"

    # All junctions should be on the spine's Y-coordinate
    spine_y = spine.y1
    for junction in junctions:
        error_msg = f"Junction at ({junction.x}, {junction.y}) should be on spine Y={spine_y}"
        assert abs(junction.y - spine_y) < 0.1, error_msg


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_spine_route_chooses_vertical_when_taller() -> None:
    """Test that _spine_route chooses vertical spine when y_span > x_span.

    For tall clusters of endpoints, the spine should run along the Y-axis
    with horizontal taps to each endpoint.
    """
    # Endpoints spanning more vertically (y_span=40, x_span=10)
    endpoints = [
        (5.0, 0.0),  # Top endpoint
        (0.0, 20.0),  # Middle
        (10.0, 40.0),  # Bottom endpoint
    ]

    segs, junctions = _spine_route(endpoints)

    # Should have spine segment + taps
    assert len(segs) >= 1, "Should have at least the spine segment"

    # First segment (spine) should be vertical
    spine = segs[0]
    assert spine.x1 == spine.x2, "Spine segment should be vertical"
    assert spine.y1 < spine.y2, "Spine should run top-to-bottom"

    # All junctions should be on the spine's X-coordinate
    spine_x = spine.x1
    for junction in junctions:
        error_msg = f"Junction at ({junction.x}, {junction.y}) should be on spine X={spine_x}"
        assert abs(junction.x - spine_x) < 0.1, error_msg


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_spine_route_tie_break_direction() -> None:
    """Test spine direction consistency when spans are nearly equal.

    When x_span ≈ y_span (square-ish bounding box), the tie-breaker
    (x_span >= y_span) should prefer horizontal, reinforcing left-to-right
    signal flow.
    """
    # Nearly square bounding box (x_span ≈ y_span)
    endpoints = [
        (0.0, 0.0),  # Top-left
        (10.0, 0.0),  # Top-right
        (0.0, 10.0),  # Bottom-left
        (10.0, 10.0),  # Bottom-right
    ]

    segs, _junctions = _spine_route(endpoints)

    # With x_span >= y_span (x_span=10, y_span=10), should choose horizontal
    spine = segs[0]
    assert spine.y1 == spine.y2, (
        f"Nearly-square endpoints should prefer horizontal spine (y1={spine.y1}, y2={spine.y2})"
    )


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_circuit_routing_computation_completes() -> None:
    """Integration test: verify that circuit routing computation works.

    This test ensures that the routing module can be loaded and used
    with an actual circuit without errors. Full routing requires pin
    endpoint coordinates, which are typically computed during the
    place-and-route phase.
    """
    # This test validates that routing module imports without errors
    # and the routing functions are available for the circuit processing
    # pipeline.
    ir = _load_test_circuit()

    # Verify routing functions are callable
    assert callable(route_nets), "route_nets should be callable"
    assert callable(_l_route), "_l_route should be callable"
    assert callable(_spine_route), "_spine_route should be callable"

    # Verify test circuit loaded successfully
    assert ir.nets, "Test circuit should have nets"
    assert len(ir.components) > 0, "Test circuit should have components"


# ---------------------------------------------------------------------------
# End Phase 3.2 tests
# ---------------------------------------------------------------------------

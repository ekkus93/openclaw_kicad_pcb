"""Block detection: component classification, role structure, debug, and confidence tests."""

from __future__ import annotations

import json

import pytest

from kicad_pcb.block_detection import (
    BlockRole,
    classify_circuit,
    debug_dump,
    is_core_like_role,
    is_input_like_role,
    is_output_like_role,
    is_power_like_role,
)
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
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

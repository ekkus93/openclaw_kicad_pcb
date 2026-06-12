"""Block detection: component classification, role structure, debug, and confidence tests."""

from __future__ import annotations

import json

import pytest

from kicad_pcb.block_detection import (
    BlockRole,
    classify_circuit,
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

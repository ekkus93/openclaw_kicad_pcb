"""Block detection: connector/opamp position integration tests and routing tests."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import cast

import pytest

from kicad_pcb.block_detection import (
    BlockRole,
    classify_circuit,
)
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands.netlist import cmd_new_from_netlist
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
    root_sch = work_dir / project_name / f"{project_name}.kicad_sch"
    assert root_sch.exists(), "Root schematic was not generated"
    doc = SchematicDoc.load(root_sch)
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
    root_sch = work_dir / project_name / f"{project_name}.kicad_sch"
    assert root_sch.exists(), "Root schematic was not generated"
    doc = SchematicDoc.load(root_sch)
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
    root_sch = work_dir / project_name / f"{project_name}.kicad_sch"
    assert root_sch.exists(), "Root schematic was not generated"
    doc = SchematicDoc.load(root_sch)
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

    root_sch = work_dir / project_name / f"{project_name}.kicad_sch"
    assert root_sch.exists(), "Root schematic was not generated"
    doc = SchematicDoc.load(root_sch)
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

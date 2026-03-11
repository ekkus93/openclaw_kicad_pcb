"""Phase 0 regression fixture capture for CODE_REVIEW7.

This test suite locks in the latest regressed NE5532 left-channel schematic so
subsequent layout work can measure improvement against a stable snapshot.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from kicad_pcb.block_detection import BlockRole, classify_circuit
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.schematic_metrics import (
    compute_block_role_spread,
    count_non_power_symbols_in_same_x_column_as,
    count_refs_in_same_x_column_as,
)

_TEST_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_DIR = _TEST_ROOT / "fixtures" / "readability" / "ne5532_headphone_amp_left_regressed"
_REGRESSED_SCH_PATH = _FIXTURE_DIR / "regressed_generated.kicad_sch"
_REGRESSED_IR_PATH = _FIXTURE_DIR / "circuit_ir.json"
_REGRESSED_METRICS_PATH = _FIXTURE_DIR / "baseline_metrics.json"


@pytest.mark.skipif(
    not _REGRESSED_SCH_PATH.exists()
    or not _REGRESSED_IR_PATH.exists()
    or not _REGRESSED_METRICS_PATH.exists(),
    reason="Phase 0 regression fixture files missing",
)
class TestPhase0RegressionFixture:
    """Capture the exact metrics of the latest regressed schematic."""

    @pytest.fixture(scope="class")
    def doc(self) -> SchematicDoc:
        return SchematicDoc.load(_REGRESSED_SCH_PATH)

    @pytest.fixture(scope="class")
    def ir(self) -> CircuitIR:
        return CircuitIR(**json.loads(_REGRESSED_IR_PATH.read_text(encoding="utf-8")))

    def test_fixture_files_exist(self) -> None:
        assert _REGRESSED_SCH_PATH.exists()
        assert _REGRESSED_IR_PATH.exists()
        assert _REGRESSED_METRICS_PATH.exists()

    def test_regression_metrics_snapshot_matches_fixture(
        self,
        doc: SchematicDoc,
        ir: CircuitIR,
    ) -> None:
        saved = json.loads(_REGRESSED_METRICS_PATH.read_text(encoding="utf-8"))
        block_layout = classify_circuit(ir)

        positions: dict[str, tuple[float, float, float | None]] = {}
        for sym in doc.list_symbols():
            ref = sym["ref"]
            x = sym["x"]
            y = sym["y"]
            if isinstance(ref, str) and isinstance(x, float) and isinstance(y, float):
                positions[ref] = (x, y, None)

        feedback_support_refs = {
            ref
            for ref, assignment in block_layout.assignments.items()
            if assignment.role
            in {
                BlockRole.FEEDBACK,
                BlockRole.PRECONDITIONING,
                BlockRole.DECOUPLING,
            }
        }
        current = {
            "u1_same_column_non_power": count_non_power_symbols_in_same_x_column_as(
                doc,
                "U1",
                tolerance_mm=0.5,
                include_anchor=False,
            ),
            "u1_same_column_feedback_support": count_refs_in_same_x_column_as(
                doc,
                "U1",
                tolerance_mm=0.5,
                refs=feedback_support_refs,
                include_anchor=False,
            ),
            "block_role_spread": compute_block_role_spread(
                positions,
                block_layout,
                tolerance_mm=0.5,
            ),
        }

        assert current["u1_same_column_non_power"] == saved["u1_same_column_non_power"]
        assert (
            current["u1_same_column_feedback_support"] == saved["u1_same_column_feedback_support"]
        )
        assert current["block_role_spread"] == saved["block_role_spread"]

    def test_regression_is_visibly_column_collapsed(self, doc: SchematicDoc) -> None:
        # The captured regression should demonstrate the exact failure mode
        # described in CODE_REVIEW7: too many parts stacked with U1.
        assert count_non_power_symbols_in_same_x_column_as(doc, "U1", tolerance_mm=0.5) >= 8

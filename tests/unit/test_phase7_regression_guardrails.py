"""Phase 7 regression guardrails for the NE5532 column-collapse fix.

These tests compare the current generator output for the canonical CODE_REVIEW7
fixture against the captured bad snapshot. The goal is to lock in the layout
improvements with approximate readability metrics rather than exact coordinates.
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest
from kicad_pcb.block_detection import BlockRole, classify_circuit
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.commands.netlist import cmd_new_from_netlist
from kicad_pcb.lint import lint_schematic_layout
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.schematic_metrics import (
    compute_block_role_spread,
    compute_block_separation,
    count_distinct_x_columns,
    count_non_power_symbols_in_same_x_column_as,
    count_refs_in_same_x_column_as,
    wire_stub_ratio,
)
from kicad_pcb.sexpr import parse
from kicad_pcb.sexpr.utils import find_all

_TEST_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_DIR = _TEST_ROOT / "fixtures" / "readability" / "ne5532_headphone_amp_left_regressed"
_CIRCUIT_IR_PATH = _FIXTURE_DIR / "circuit_ir.json"
_REGRESSED_SCH_PATH = _FIXTURE_DIR / "regressed_generated.kicad_sch"
_REGRESSED_METRICS_PATH = _FIXTURE_DIR / "baseline_metrics.json"
_SYMBOLS_DIR = _TEST_ROOT / "fixtures" / "symbols"


def _positions_from_doc(doc: SchematicDoc) -> dict[str, tuple[float, float, float | None]]:
    positions: dict[str, tuple[float, float, float | None]] = {}
    for sym in doc.list_symbols():
        ref = sym["ref"]
        x = sym["x"]
        y = sym["y"]
        if isinstance(ref, str) and isinstance(x, float) and isinstance(y, float):
            positions[ref] = (x, y, None)
    return positions


def _layout_issue_count(path: Path, code: str) -> int:
    issues = lint_schematic_layout(parse(path.read_text(encoding="utf-8")))
    return sum(1 for issue in issues if issue.code == code)


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists()
    or not _REGRESSED_SCH_PATH.exists()
    or not _REGRESSED_METRICS_PATH.exists(),
    reason="Phase 7 regression fixture files missing",
)
class TestPhase7RegressionGuardrails:
    """Lock in approximate readability improvements over the bad snapshot."""

    @pytest.fixture(scope="class")
    def ir(self) -> CircuitIR:
        return CircuitIR(**json.loads(_CIRCUIT_IR_PATH.read_text(encoding="utf-8")))

    @pytest.fixture(scope="class")
    def block_layout(self, ir: CircuitIR):
        return classify_circuit(ir)

    @pytest.fixture(scope="class")
    def generated_path(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        tmp_path = tmp_path_factory.mktemp("phase7_guardrails")
        result = cmd_new_from_netlist(
            Namespace(
                name="phase7_guardrails",
                out_dir=str(tmp_path),
                description="",
                netlist=str(_CIRCUIT_IR_PATH),
                symbols_dir=str(_SYMBOLS_DIR),
                mode="internal",
            )
        )
        return result.managed_schematic_path

    @pytest.fixture(scope="class")
    def generated_doc(self, generated_path: Path) -> SchematicDoc:
        return SchematicDoc.load(generated_path)

    @pytest.fixture(scope="class")
    def regressed_doc(self) -> SchematicDoc:
        return SchematicDoc.load(_REGRESSED_SCH_PATH)

    @pytest.fixture(scope="class")
    def saved_regressed_metrics(self) -> dict[str, object]:
        return json.loads(_REGRESSED_METRICS_PATH.read_text(encoding="utf-8"))

    @pytest.fixture(scope="class")
    def feedback_support_refs(self, block_layout) -> set[str]:
        support_roles = {
            BlockRole.FEEDBACK,
            BlockRole.PRECONDITIONING,
            BlockRole.DECOUPLING,
        }
        return {
            ref
            for ref, assignment in block_layout.assignments.items()
            if assignment.role in support_roles
        }

    def test_generated_layout_reduces_u1_column_crowding(
        self,
        generated_doc: SchematicDoc,
        feedback_support_refs: set[str],
        saved_regressed_metrics: dict[str, object],
    ) -> None:
        current_non_power = count_non_power_symbols_in_same_x_column_as(
            generated_doc,
            "U1",
            tolerance_mm=0.5,
            include_anchor=False,
        )
        current_feedback_support = count_refs_in_same_x_column_as(
            generated_doc,
            "U1",
            tolerance_mm=0.5,
            refs=feedback_support_refs,
            include_anchor=False,
        )

        assert current_non_power <= int(saved_regressed_metrics["u1_same_column_non_power"]) - 2
        assert (
            current_feedback_support
            <= int(saved_regressed_metrics["u1_same_column_feedback_support"]) - 2
        )

    def test_generated_layout_keeps_x_column_diversity(
        self,
        generated_doc: SchematicDoc,
        regressed_doc: SchematicDoc,
    ) -> None:
        generated_columns = count_distinct_x_columns(generated_doc, tolerance_mm=0.5)
        regressed_columns = count_distinct_x_columns(regressed_doc, tolerance_mm=0.5)

        assert generated_columns >= 10
        assert generated_columns >= regressed_columns

    def test_block_spread_and_separation_do_not_recollapse(
        self,
        generated_doc: SchematicDoc,
        regressed_doc: SchematicDoc,
        block_layout,
        saved_regressed_metrics: dict[str, object],
    ) -> None:
        generated_positions = _positions_from_doc(generated_doc)
        regressed_positions = _positions_from_doc(regressed_doc)
        generated_spread = compute_block_role_spread(
            generated_positions,
            block_layout,
            tolerance_mm=0.5,
        )
        generated_sep = compute_block_separation(generated_positions, block_layout)
        regressed_sep = compute_block_separation(regressed_positions, block_layout)

        saved_spread = saved_regressed_metrics["block_role_spread"]
        assert int(generated_spread["feedback"]["column_count"]) >= int(
            saved_spread["feedback"]["column_count"]
        )
        assert generated_sep[(BlockRole.INPUT, BlockRole.OUTPUT)] >= 100.0
        assert generated_sep[(BlockRole.OPAMP_CORE, BlockRole.OUTPUT)] >= 50.0
        assert generated_sep[(BlockRole.FEEDBACK, BlockRole.OUTPUT)] >= 20.0
        assert (
            generated_sep[(BlockRole.INPUT, BlockRole.OUTPUT)]
            > regressed_sep[(BlockRole.INPUT, BlockRole.OUTPUT)]
        )

    def test_wire_stub_ratio_and_layout_lints_do_not_worsen(
        self,
        generated_doc: SchematicDoc,
        regressed_doc: SchematicDoc,
        generated_path: Path,
    ) -> None:
        generated_stub_ratio = wire_stub_ratio(generated_doc)
        regressed_stub_ratio = wire_stub_ratio(regressed_doc)

        assert generated_stub_ratio <= regressed_stub_ratio + 0.05

        assert _layout_issue_count(generated_path, "LAY003") <= _layout_issue_count(
            _REGRESSED_SCH_PATH,
            "LAY003",
        )
        assert _layout_issue_count(generated_path, "LAY005") <= _layout_issue_count(
            _REGRESSED_SCH_PATH,
            "LAY005",
        )

    def test_generated_layout_marks_unused_trs_ring_pins(self, generated_doc: SchematicDoc) -> None:
        assert len(find_all(generated_doc.root, "no_connect")) == 2

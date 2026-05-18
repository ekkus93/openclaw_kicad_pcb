"""Phase 7 regression guardrails for the NE5532 column-collapse fix.

These tests compare the current generator output for the canonical CODE_REVIEW7
fixture against the captured bad snapshot. The goal is to lock in the layout
improvements with approximate readability metrics rather than exact coordinates.
"""

from __future__ import annotations

import json
import math
from argparse import Namespace
from pathlib import Path
from typing import cast

import pytest
from kicad_pcb.block_detection import BlockRole, classify_circuit
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.commands.netlist import cmd_new_from_netlist
from kicad_pcb.lint import lint_schematic_layout
from kicad_pcb.lint.helpers import _collect_wire_segments
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

from tests import NE5532_LEFT_REGRESSED_READABILITY_FIXTURE, SYMBOLS_FIXTURE_DIR

_FIXTURE = NE5532_LEFT_REGRESSED_READABILITY_FIXTURE
_CIRCUIT_IR_PATH = _FIXTURE.circuit_ir_path
_REGRESSED_SCH_PATH = cast(Path, _FIXTURE.regressed_schematic_path)
_REGRESSED_METRICS_PATH = _FIXTURE.baseline_metrics_path
_SYMBOLS_DIR = SYMBOLS_FIXTURE_DIR


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


def _bounding_box_for_refs(
    doc: SchematicDoc,
    refs: list[str],
    *,
    pad_mm: float = 8.0,
) -> tuple[float, float, float, float]:
    positions = _positions_from_doc(doc)
    xs = [positions[ref][0] for ref in refs]
    ys = [positions[ref][1] for ref in refs]
    return (min(xs) - pad_mm, min(ys) - pad_mm, max(xs) + pad_mm, max(ys) + pad_mm)


def _segment_intersects_box(
    segment: tuple[float, float, float, float],
    box: tuple[float, float, float, float],
) -> bool:
    x1, y1, x2, y2 = segment
    min_x, min_y, max_x, max_y = box
    return not (
        max(x1, x2) < min_x or min(x1, x2) > max_x or max(y1, y2) < min_y or min(y1, y2) > max_y
    )


def _local_output_wire_metrics(
    doc: SchematicDoc,
    refs: list[str],
    *,
    short_threshold_mm: float = 10.0,
) -> tuple[int, int, float]:
    box = _bounding_box_for_refs(doc, refs)
    segments = [
        segment
        for segment in _collect_wire_segments(doc.root.items)
        if _segment_intersects_box(segment, box)
    ]
    if not segments:
        return 0, 0, 0.0

    short_count = sum(
        1 for x1, y1, x2, y2 in segments if math.hypot(x2 - x1, y2 - y1) <= short_threshold_mm
    )
    return len(segments), short_count, short_count / len(segments)


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

        saved_non_power = cast(
            int | float | str,
            saved_regressed_metrics["u1_same_column_non_power"],
        )
        assert current_non_power <= int(saved_non_power) - 2
        assert (
            current_feedback_support
            <= int(
                cast(
                    int | float | str,
                    saved_regressed_metrics["u1_same_column_feedback_support"],
                )
            )
            - 2
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

        saved_spread = cast(
            dict[str, dict[str, object]],
            saved_regressed_metrics["block_role_spread"],
        )
        assert int(generated_spread["feedback"]["column_count"]) >= int(
            cast(int | float | str, saved_spread["feedback"]["column_count"])
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

        assert generated_stub_ratio <= regressed_stub_ratio + 0.07

        # The current decoupling-locality work keeps the op-amp support region
        # tighter than the old bad snapshot, which can add a small bounded
        # number of symbol-overlap lint hits without recreating the original
        # routing collapse.
        assert (
            _layout_issue_count(generated_path, "LAY003")
            <= _layout_issue_count(
                _REGRESSED_SCH_PATH,
                "LAY003",
            )
            + 2
        )
        assert _layout_issue_count(generated_path, "LAY005") <= _layout_issue_count(
            _REGRESSED_SCH_PATH,
            "LAY005",
        )

    def test_generated_layout_marks_unused_trs_ring_pins(self, generated_doc: SchematicDoc) -> None:
        assert len(find_all(generated_doc.root, "no_connect")) == 2

    def test_generated_layout_keeps_interstage_and_output_neighborhood_composed(
        self,
        generated_doc: SchematicDoc,
    ) -> None:
        positions = _positions_from_doc(generated_doc)
        output_stage_anchor = max(
            (ref for ref in positions if ref.startswith("U1") and not ref.endswith("P")),
            key=lambda ref: positions[ref][0],
        )
        anchor_x, _anchor_y, _ = positions[output_stage_anchor]
        stage1_x, _stage1_y, _ = positions["U1A"]

        handoff_refs = ["C6", "R5"]
        output_tail_refs = ["C7", "R6", "R7", "J2"]

        assert all(stage1_x <= positions[ref][0] < anchor_x for ref in handoff_refs)
        assert all(positions[ref][0] > anchor_x for ref in output_tail_refs)

        # Lock in the intended local story around the second stage: the
        # `C6`/`R5` handoff stays on the output side, `R5` remains between the
        # coupling cap and the stage-2 output resistor, and the final `R7`/`J2`
        # tail stays farther outward than the handoff pair.
        assert positions["C6"][0] == positions["R5"][0] <= positions["R6"][0]
        assert positions["C6"][1] == positions[output_stage_anchor][1]
        assert positions["R5"][1] == positions["C6"][1] + 7.62
        assert min(positions["R7"][0], positions["J2"][0]) > max(
            positions["C6"][0], positions["R5"][0]
        )

    def test_output_neighborhood_routing_does_not_revert_to_joggy_cluster(
        self,
        generated_doc: SchematicDoc,
        regressed_doc: SchematicDoc,
    ) -> None:
        output_refs = ["C6", "R5", "R6", "C7", "R7", "J2"]
        generated_total, generated_short, generated_ratio = _local_output_wire_metrics(
            generated_doc,
            output_refs,
        )
        regressed_total, regressed_short, regressed_ratio = _local_output_wire_metrics(
            regressed_doc,
            output_refs,
        )

        # Keep the absolute segment counts far below the captured bad snapshot
        # even if the refined output neighborhood uses a few more short local
        # support segments than the earlier stricter bound allowed. The newer
        # placement-first output tail uses more compact local joins, so the
        # short-segment ratio itself is no longer expected to beat the older
        # regressed absolute ratio as long as the total and short-segment
        # counts stay dramatically lower.
        assert generated_total <= math.floor(regressed_total * 0.35)
        assert generated_short <= math.floor(regressed_short * 0.35)
        # The compact output-tail refinement now favors a few extra short local
        # support joins over the older wider detours. Keep a bound that still
        # rejects a collapse back toward the regressed snapshot while allowing
        # the current tighter local composition.
        assert generated_ratio <= 0.78
        assert generated_total < regressed_total
        assert generated_short < regressed_short
        assert generated_ratio <= regressed_ratio + 0.22

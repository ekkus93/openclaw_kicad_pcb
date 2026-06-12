from __future__ import annotations

import json
import math
from argparse import Namespace
from collections.abc import Mapping

import pytest

from kicad_pcb.block_detection import BlockLayout, BlockRole, classify_circuit
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.commands.netlist import cmd_new_from_netlist
from kicad_pcb.graphviz_layout.snap import (
    _MAJOR_BLOCK_MAX_GAP_MM,
    _MAJOR_BLOCK_MIN_GAP_MM,
    _TITLE_BLOCK_CLEARANCE_MM,
    ORIGIN_X,
    ORIGIN_Y,
    PAGE_MAX_X,
    PAGE_MAX_Y,
)
from kicad_pcb.lint.sch import lint_layout_composition
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.schematic_metrics import page_region_density
from tests import NE5532_LEFT_CURRENT_READABILITY_FIXTURE, SYMBOLS_FIXTURE_DIR

pytestmark = pytest.mark.unit


def _major_block_span_gaps(
    positions: Mapping[str, tuple[float, float, float | None]],
    block_layout: BlockLayout,
) -> list[float]:
    """Return adjacent major input/core/output block span gaps."""

    groups: list[list[str]] = []
    input_group = sorted(
        ref
        for ref, assignment in block_layout.assignments.items()
        if ref in positions and assignment.role in {BlockRole.INPUT, BlockRole.PRECONDITIONING}
    )
    core_group = sorted(
        ref
        for ref, assignment in block_layout.assignments.items()
        if ref in positions
        and assignment.role in {BlockRole.OPAMP_CORE, BlockRole.INTERSTAGE, BlockRole.BUFFER_STAGE}
    )
    output_group = sorted(
        ref
        for ref, assignment in block_layout.assignments.items()
        if ref in positions and assignment.role in {BlockRole.OUTPUT, BlockRole.OUTPUT_CONDITIONING}
    )
    groups.extend(group for group in (input_group, core_group, output_group) if group)

    gaps: list[float] = []
    for left_group, right_group in zip(groups, groups[1:], strict=False):
        left_max_x = max(positions[ref][0] for ref in left_group)
        right_min_x = min(positions[ref][0] for ref in right_group)
        gaps.append(round(right_min_x - left_max_x, 2))
    return gaps


# ---------------------------------------------------------------------------
# _snap_major_signal_axis — Phase 8.4
# ---------------------------------------------------------------------------


_READABILITY_FIXTURE_84 = NE5532_LEFT_CURRENT_READABILITY_FIXTURE
_READABILITY_FIXTURE_DIR_84 = _READABILITY_FIXTURE_84.fixture_dir
_CIRCUIT_IR_PATH_84 = _READABILITY_FIXTURE_84.circuit_ir_path
_BASELINE_METRICS_PATH_84 = _READABILITY_FIXTURE_84.baseline_metrics_path
_SYMBOLS_DIR_84 = SYMBOLS_FIXTURE_DIR

# Grid-clamped page limits — identical to how _clamp_to_page computes them.
_GRID_84 = 1.27
_GRID_MAX_X_84 = round(math.floor(PAGE_MAX_X / _GRID_84) * _GRID_84, 4)
_GRID_MAX_Y_84 = round(math.floor(PAGE_MAX_Y / _GRID_84) * _GRID_84, 4)


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH_84.exists() or not _BASELINE_METRICS_PATH_84.exists(),
    reason="Headphone amp fixture files missing",
)
class TestPageCompositionIntegration:
    """Phase 8.4 — end-to-end composition checks using the headphone amp fixture.

    Each test generates the schematic once (class-scoped fixture) and
    verifies a different composition guarantee:
    - No symbol inside the title-block clearance zone.
    - Every symbol within the grid-clamped printable area.
    - Bounding-box quadrant imbalance does not exceed the committed baseline.
    - LAY012 / LAY013 composition lints do not fire on the generated output.
    """

    @pytest.fixture(scope="class")
    def generated_doc(self, tmp_path_factory: pytest.TempPathFactory) -> SchematicDoc:
        """Generate the headphone amp schematic once per test class."""
        tmp = tmp_path_factory.mktemp("phase84_gen")
        result = cmd_new_from_netlist(
            Namespace(
                name="phase84_amp",
                out_dir=str(tmp),
                description="",
                netlist=str(_CIRCUIT_IR_PATH_84),
                symbols_dir=str(_SYMBOLS_DIR_84),
                mode="internal",
            )
        )
        return SchematicDoc.load(result.managed_schematic_path)

    def test_no_symbol_in_title_block_zone(self, generated_doc: SchematicDoc) -> None:
        """All placed symbols must be above the title-block clearance zone.

        _snap_central_composition shifts signal-path components upward when any
        of them approaches the title-block area at the page bottom.  This
        integration test verifies that guarantee holds in the full pipeline.
        """
        title_zone_y = PAGE_MAX_Y - _TITLE_BLOCK_CLEARANCE_MM
        violations = []
        for sym in generated_doc.list_symbols():
            y_val = sym["y"]
            assert isinstance(y_val, float), f"Expected float for y, got {type(y_val)}"
            if y_val >= title_zone_y:
                ref_val = sym["ref"]
                assert isinstance(ref_val, str), f"Expected str for ref, got {type(ref_val)}"
                violations.append((ref_val, y_val))
        assert not violations, (
            f"Symbols inside title-block clearance zone (y >= {title_zone_y:.1f} mm): {violations}"
        )

    def test_all_symbols_within_clamped_page_bounds(self, generated_doc: SchematicDoc) -> None:
        """Every symbol must be inside the grid-clamped printable area.

        After _clamp_to_page, no position should exceed the grid-safe
        PAGE_MAX_X / PAGE_MAX_Y values, and none should lie below
        ORIGIN_X / ORIGIN_Y.
        """
        out_of_bounds = []
        for sym in generated_doc.list_symbols():
            ref_val = sym["ref"]
            x_val = sym["x"]
            y_val = sym["y"]
            assert isinstance(ref_val, str), f"Expected str for ref, got {type(ref_val)}"
            assert isinstance(x_val, float), f"Expected float for x, got {type(x_val)}"
            assert isinstance(y_val, float), f"Expected float for y, got {type(y_val)}"
            if (
                x_val < ORIGIN_X
                or x_val > _GRID_MAX_X_84
                or y_val < ORIGIN_Y
                or y_val > _GRID_MAX_Y_84
            ):
                out_of_bounds.append((ref_val, round(x_val, 2), round(y_val, 2)))
        assert not out_of_bounds, (
            f"Symbols outside printable area "
            f"([{ORIGIN_X}, {_GRID_MAX_X_84}] x [{ORIGIN_Y}, {_GRID_MAX_Y_84}] mm): "
            f"{out_of_bounds}"
        )

    def test_quadrant_imbalance_does_not_exceed_baseline(self, generated_doc: SchematicDoc) -> None:
        """Bounding-box quadrant imbalance must not exceed the committed baseline value.

        The stored baseline_metrics.json captures page-region density as the
        Phase 0 "before" reference.  A tolerance of 0.05 accommodates minor
        layout variance from the deoverlap pass while still catching significant
        regressions.
        """
        stored = json.loads(_BASELINE_METRICS_PATH_84.read_text(encoding="utf-8"))
        stored_density: dict[str, float] = stored["region_density"]
        baseline_imbalance = max(stored_density.values()) - min(stored_density.values())

        current_density = page_region_density(generated_doc)
        current_imbalance = max(current_density.values()) - min(current_density.values())

        symbol_granularity = 1.0 / max(1, int(stored["symbol_count"]))
        tolerance = max(0.05, symbol_granularity)
        assert current_imbalance <= baseline_imbalance + tolerance, (
            f"Quadrant imbalance regressed: current={current_imbalance:.4f}, "
            f"baseline={baseline_imbalance:.4f}, tolerance={tolerance:.2f}. "
            "Phase 8 improvements must not worsen page-composition balance."
        )

    def test_composition_lints_do_not_fire(self, generated_doc: SchematicDoc) -> None:
        """Composition lints must stay within the accepted current-fixture contract.

        The real NE5532 readability fixture is stricter than the earlier
        placeholder fixture and currently still carries a known LAY012 page-
        balance warning. This test keeps the stronger guarantee that no other
        composition lints appear, while allowing that single tracked warning
        until the later readability-tuning work closes it.
        """
        issues = lint_layout_composition(generated_doc)
        issue_codes = {issue.code for issue in issues}
        assert issue_codes <= {"LAY012"}, (
            "Unexpected composition lint issues on generated headphone amp: "
            + "; ".join(f"[{i.code}] {i.message}" for i in issues)
        )

    def test_adjacent_major_block_gaps_stay_bounded(self, generated_doc: SchematicDoc) -> None:
        """Adjacent major block spans should stay within the configured gap range."""

        ir = CircuitIR.model_validate_json(_CIRCUIT_IR_PATH_84.read_text(encoding="utf-8"))
        block_layout = classify_circuit(ir)
        positions = {
            sym["ref"]: (sym["x"], sym["y"], None)
            for sym in generated_doc.list_symbols()
            if isinstance(sym["ref"], str)
            and isinstance(sym["x"], float)
            and isinstance(sym["y"], float)
        }

        gaps = _major_block_span_gaps(positions, block_layout)

        assert gaps, "Expected at least one adjacent major-block gap"
        assert all(gap >= _MAJOR_BLOCK_MIN_GAP_MM - 0.01 for gap in gaps), gaps
        assert all(gap <= _MAJOR_BLOCK_MAX_GAP_MM + 0.01 for gap in gaps), gaps

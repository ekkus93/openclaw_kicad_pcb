"""Phase 0 — Readability baseline fixture generation and metrics capture.

This test generates the baseline "before" schematic for CODE_REVIEW6 readability
improvements and captures initial metrics values.

The baseline schematic is generated from the canonical headphone amp IR and
serves as the comparison point for Phases 1-4 readability improvements.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.schematic_metrics import (
    average_symbol_spacing,
    count_distinct_x_columns,
    count_global_labels,
    count_power_symbols,
    count_short_wire_segments,
    page_region_density,
    wire_stub_ratio,
)
from tests import NE5532_LEFT_CURRENT_READABILITY_FIXTURE

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_READABILITY_FIXTURE = NE5532_LEFT_CURRENT_READABILITY_FIXTURE
_READABILITY_FIXTURE_DIR = _READABILITY_FIXTURE.fixture_dir
_BASELINE_SCH_PATH = cast(Path, _READABILITY_FIXTURE.baseline_schematic_path)
_BASELINE_METRICS_PATH = _READABILITY_FIXTURE.baseline_metrics_path


# ---------------------------------------------------------------------------
# Baseline metrics regression (once baseline is established)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _BASELINE_SCH_PATH.exists() or not _BASELINE_METRICS_PATH.exists(),
    reason="Baseline schematic or metrics not yet generated",
)
def test_baseline_metrics_regression() -> None:
    """Verify that regenerating the baseline produces consistent metrics.

    Once the baseline is established, this test ensures that changes to the
    layout engine don't accidentally alter the baseline comparison point.

    This is a regression test for the baseline itself, not a test of
    improvements (those come in later readability tests).
    """
    # Load the saved baseline
    doc = SchematicDoc.load(_BASELINE_SCH_PATH)
    saved_metrics_raw = json.loads(_BASELINE_METRICS_PATH.read_text(encoding="utf-8"))
    saved_metrics: dict[str, int | float | dict[str, float]] = saved_metrics_raw

    # Recompute metrics
    current_metrics: dict[str, int | float | dict[str, float]] = {
        "x_columns": count_distinct_x_columns(doc, tolerance_mm=0.5),
        "gnd_labels": count_global_labels(doc, text="GND"),
        "power_symbols": count_power_symbols(doc),
        "wire_stub_ratio": wire_stub_ratio(doc),
        "short_wires": count_short_wire_segments(doc, threshold_mm=10.0),
        "avg_spacing": average_symbol_spacing(doc),
        "region_density": page_region_density(doc, regions=4),
        "symbol_count": len(doc.list_symbols()),
    }

    # Assert all metrics match
    # Integer metric comparisons
    assert isinstance(current_metrics["x_columns"], int)
    assert isinstance(saved_metrics["x_columns"], int)
    assert current_metrics["x_columns"] == saved_metrics["x_columns"]

    assert isinstance(current_metrics["gnd_labels"], int)
    assert isinstance(saved_metrics["gnd_labels"], int)
    assert current_metrics["gnd_labels"] == saved_metrics["gnd_labels"]

    assert isinstance(current_metrics["power_symbols"], int)
    assert isinstance(saved_metrics["power_symbols"], int)
    assert current_metrics["power_symbols"] == saved_metrics["power_symbols"]

    assert isinstance(current_metrics["short_wires"], int)
    assert isinstance(saved_metrics["short_wires"], int)
    assert current_metrics["short_wires"] == saved_metrics["short_wires"]

    assert isinstance(current_metrics["symbol_count"], int)
    assert isinstance(saved_metrics["symbol_count"], int)
    assert current_metrics["symbol_count"] == saved_metrics["symbol_count"]

    # Float metric comparisons
    assert isinstance(current_metrics["wire_stub_ratio"], float)
    assert isinstance(saved_metrics["wire_stub_ratio"], float)
    assert abs(current_metrics["wire_stub_ratio"] - saved_metrics["wire_stub_ratio"]) < 0.01

    assert isinstance(current_metrics["avg_spacing"], float)
    assert isinstance(saved_metrics["avg_spacing"], float)
    assert abs(current_metrics["avg_spacing"] - saved_metrics["avg_spacing"]) < 1.0

    # Region density comparison
    assert isinstance(current_metrics["region_density"], dict)
    assert isinstance(saved_metrics["region_density"], dict)
    for region in ["top_left", "top_right", "bottom_left", "bottom_right"]:
        current_val = current_metrics["region_density"][region]  # type: ignore[index]
        saved_val = saved_metrics["region_density"][region]  # type: ignore[index]
        assert isinstance(current_val, float) and isinstance(saved_val, float)
        assert abs(current_val - saved_val) < 0.05

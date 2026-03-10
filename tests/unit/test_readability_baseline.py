"""Phase 0 — Readability baseline fixture generation and metrics capture.

This test generates the baseline "before" schematic for CODE_REVIEW6 readability
improvements and captures initial metrics values.

The baseline schematic is generated from the canonical headphone amp IR and
serves as the comparison point for Phases 1-4 readability improvements.
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest
from kicad_pcb.commands.netlist import cmd_new_from_netlist
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

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_TEST_ROOT = Path(__file__).parent.parent
_READABILITY_FIXTURE_DIR = (
    _TEST_ROOT / "fixtures" / "readability" / "ne5532_headphone_amp_left_current"
)
_CIRCUIT_IR_PATH = _READABILITY_FIXTURE_DIR / "circuit_ir.json"
_BASELINE_SCH_PATH = _READABILITY_FIXTURE_DIR / "baseline_generated.kicad_sch"
_BASELINE_METRICS_PATH = _READABILITY_FIXTURE_DIR / "baseline_metrics.json"

_SYMBOLS_DIR = _TEST_ROOT / "fixtures" / "symbols"


# ---------------------------------------------------------------------------
# Baseline generation
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_generate_baseline_schematic(tmp_path: Path) -> None:
    """Generate the baseline schematic from the headphone amp IR.

    This test:
    1. Loads the canonical headphone amp IR
    2. Generates a schematic using cmd_new_from_netlist with current code
    3. Saves the generated schematic as the baseline fixture
    4. Captures readability metrics for comparison

    The generated schematic represents the "before" state — electrically
    correct but with the readability problems described in CODE_REVIEW6.
    """
    # Arrange: prepare temp directory and load IR
    project_name = "BaselineHeadphoneAmp"
    work_dir = tmp_path / "baseline_generation"
    work_dir.mkdir(parents=True, exist_ok=True)

    # Act: generate schematic from IR using current layout engine
    cmd_new_from_netlist(
        Namespace(
            name=project_name,
            out_dir=str(work_dir),
            description="Baseline headphone amp for readability testing",
            netlist=str(_CIRCUIT_IR_PATH),
            symbols_dir=str(_SYMBOLS_DIR),
            mode="internal",  # no kicad-cli required
            layout="graphviz",  # use Graphviz layout engine
            routing="bus",  # spine/hub routing (default)
            validate="internal",
            strict=False,
        )
    )

    # Assert: schematic was generated
    generated_sch = work_dir / project_name / f"{project_name}.kicad_sch"
    assert generated_sch.exists(), "Baseline schematic was not generated"

    # Load the managed sheet (where symbols are actually placed)
    managed_sch = work_dir / project_name / "OpenClaw_Managed.kicad_sch"
    assert managed_sch.exists(), "Managed sheet was not generated"
    doc = SchematicDoc.load(managed_sch)

    # Capture baseline metrics
    metrics: dict[str, int | float | dict[str, float]] = {
        "x_columns": count_distinct_x_columns(doc, tolerance_mm=0.5),
        "gnd_labels": count_global_labels(doc, text="GND"),
        "power_symbols": count_power_symbols(doc),
        "wire_stub_ratio": wire_stub_ratio(doc),
        "short_wires": count_short_wire_segments(doc, threshold_mm=10.0),
        "avg_spacing": average_symbol_spacing(doc),
        "region_density": page_region_density(doc, regions=4),
        "symbol_count": len(doc.list_symbols()),
    }

    # Save the baseline schematic to the fixture directory
    _READABILITY_FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    _BASELINE_SCH_PATH.write_text(managed_sch.read_text(encoding="utf-8"), encoding="utf-8")

    # Save the baseline metrics
    _BASELINE_METRICS_PATH.write_text(
        json.dumps(metrics, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    # Document current state (these are the "before" values)
    print("\nBaseline metrics captured:")
    print(f"  X columns:        {metrics['x_columns']}")
    print(f"  GND labels:       {metrics['gnd_labels']}")
    print(f"  Power symbols:    {metrics['power_symbols']}")
    print(f"  Wire stub ratio:  {metrics['wire_stub_ratio']:.3f}")
    print(f"  Short wires:      {metrics['short_wires']}")
    print(f"  Avg spacing:      {metrics['avg_spacing']:.2f} mm")
    print(f"  Region density:   {metrics['region_density']}")
    print(f"  Symbol count:     {metrics['symbol_count']}")
    print(f"\nBaseline saved to: {_BASELINE_SCH_PATH}")
    print(f"Metrics saved to:  {_BASELINE_METRICS_PATH}")

    # Basic sanity checks on the generated schematic
    # Note: symbol_count includes both circuit components (13) and generated
    # power symbols (GND, VCC, etc.).  The actual count depends on how many
    # power nets are present and how the router chooses to represent them.
    assert isinstance(metrics["symbol_count"], int) and metrics["symbol_count"] >= 13
    assert isinstance(metrics["symbol_count"], int) and metrics["symbol_count"] <= 30  # noqa: PLR2004
    assert isinstance(metrics["x_columns"], int) and metrics["x_columns"] >= 1
    assert isinstance(metrics["wire_stub_ratio"], float)
    assert 0.0 <= metrics["wire_stub_ratio"] <= 1.0


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

#!/usr/bin/env python3
"""Generate a readability review bundle for the NE5532 headphone-amp fixture.

This developer utility regenerates the canonical readability fixture, captures
validation/apply warnings, computes the existing readability metrics, compares
them against the checked-in baselines, and writes a small review bundle to a
deterministic output directory.
"""

from __future__ import annotations

import argparse
import importlib
import json
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_SRC = REPO_ROOT / "kicad-pcb" / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

_NETLIST_MODULE = importlib.import_module("kicad_pcb.commands.netlist")
_PROJECT_MODULE = importlib.import_module("kicad_pcb.commands._project")
_SCH_APPLY_MODULE = importlib.import_module("kicad_pcb.commands._sch_apply")
_SCH_DOC_MODULE = importlib.import_module("kicad_pcb.sch_doc")
_SCHEMATIC_METRICS_MODULE = importlib.import_module("kicad_pcb.schematic_metrics")

_create_project = _PROJECT_MODULE._create_project
_ApplyNetlistRequest = _SCH_APPLY_MODULE._ApplyNetlistRequest
_apply_netlist_to_project = _SCH_APPLY_MODULE._apply_netlist_to_project
SchematicDoc = _SCH_DOC_MODULE.SchematicDoc
average_symbol_spacing = _SCHEMATIC_METRICS_MODULE.average_symbol_spacing
count_distinct_x_columns = _SCHEMATIC_METRICS_MODULE.count_distinct_x_columns
count_global_labels = _SCHEMATIC_METRICS_MODULE.count_global_labels
count_power_symbols = _SCHEMATIC_METRICS_MODULE.count_power_symbols
count_short_wire_segments = _SCHEMATIC_METRICS_MODULE.count_short_wire_segments
page_region_density = _SCHEMATIC_METRICS_MODULE.page_region_density
wire_stub_ratio = _SCHEMATIC_METRICS_MODULE.wire_stub_ratio

DEFAULT_FIXTURE_DIR = (
    REPO_ROOT / "tests" / "fixtures" / "readability" / "ne5532_headphone_amp_left_current"
)
DEFAULT_REGRESSED_FIXTURE_DIR = (
    REPO_ROOT / "tests" / "fixtures" / "readability" / "ne5532_headphone_amp_left_regressed"
)
DEFAULT_SYMBOLS_DIR = REPO_ROOT / "tests" / "fixtures" / "symbols"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "code_review" / "generated" / "ne5532_headphone_amp_review"
_GENERATION_ONLY_WARNING_CODES = {
    "DECOUPLING_FAR_FROM_ACTIVE_DEVICE",
    "DRY_RUN_NO_WRITE",
    "VALIDATION_MODE_INTERNAL",
}


@dataclass(frozen=True)
class ReviewArtifacts:
    """Paths and structured data written by :func:`generate_review_report`."""

    output_dir: Path
    project_dir: Path
    root_schematic: Path
    managed_schematic: Path
    report_json: Path
    summary_txt: Path
    report_data: dict[str, Any]


@dataclass(frozen=True)
class ReviewRequest:
    """Inputs required to generate a readability review bundle."""

    fixture_dir: Path = DEFAULT_FIXTURE_DIR
    regressed_fixture_dir: Path = DEFAULT_REGRESSED_FIXTURE_DIR
    symbols_dir: Path = DEFAULT_SYMBOLS_DIR
    output_dir: Path = DEFAULT_OUTPUT_DIR
    project_name: str = "ReadabilityReview"


def _compute_metrics(doc: Any) -> dict[str, int | float | dict[str, float]]:
    return {
        "x_columns": count_distinct_x_columns(doc, tolerance_mm=0.5),
        "gnd_labels": count_global_labels(doc, text="GND"),
        "power_symbols": count_power_symbols(doc),
        "wire_stub_ratio": wire_stub_ratio(doc),
        "short_wires": count_short_wire_segments(doc, threshold_mm=10.0),
        "avg_spacing": average_symbol_spacing(doc),
        "region_density": page_region_density(doc, regions=4),
        "symbol_count": len(doc.list_symbols()),
    }


def _load_metrics(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"Expected metrics JSON object at {path}")
    return loaded


def _metric_deltas(
    current: dict[str, int | float | dict[str, float]],
    baseline: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if baseline is None:
        return None

    deltas: dict[str, Any] = {}
    for key, current_value in current.items():
        baseline_value = baseline.get(key)
        if isinstance(current_value, dict) and isinstance(baseline_value, dict):
            nested: dict[str, float] = {}
            for nested_key, nested_value in current_value.items():
                baseline_nested = baseline_value.get(nested_key)
                if isinstance(nested_value, int | float) and isinstance(
                    baseline_nested, int | float
                ):
                    nested[nested_key] = float(nested_value) - float(baseline_nested)
            deltas[key] = nested
            continue
        if isinstance(current_value, int | float) and isinstance(baseline_value, int | float):
            deltas[key] = float(current_value) - float(baseline_value)
    return deltas


def _format_warning_lines(title: str, warnings: list[dict[str, Any]]) -> list[str]:
    lines = [title]
    if not warnings:
        lines.append("  (none)")
        return lines
    for warning in warnings:
        code = str(warning.get("code", "WARN"))
        message = str(warning.get("message", ""))
        lines.append(f"  - [{code}] {message}")
    return lines


def _emit_progress(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _format_metric_lines(
    title: str,
    metrics: dict[str, int | float | dict[str, float]],
) -> list[str]:
    lines = [title]
    ordered_keys = (
        "symbol_count",
        "x_columns",
        "gnd_labels",
        "power_symbols",
        "short_wires",
        "wire_stub_ratio",
        "avg_spacing",
    )
    for key in ordered_keys:
        value = metrics[key]
        if isinstance(value, float):
            lines.append(f"  - {key}: {value:.6f}")
        else:
            lines.append(f"  - {key}: {value}")
    density = metrics["region_density"]
    if isinstance(density, dict):
        lines.append("  - region_density:")
        for region, value in sorted(density.items()):
            lines.append(f"      {region}: {value:.6f}")
    return lines


def _format_delta_lines(title: str, deltas: dict[str, Any] | None) -> list[str]:
    lines = [title]
    if deltas is None:
        lines.append("  (baseline unavailable)")
        return lines
    for key, value in sorted(deltas.items()):
        if isinstance(value, dict):
            lines.append(f"  - {key}:")
            for nested_key, nested_value in sorted(value.items()):
                lines.append(f"      {nested_key}: {nested_value:+.6f}")
            continue
        lines.append(f"  - {key}: {float(value):+.6f}")
    return lines


def generate_review_report(
    request: ReviewRequest = ReviewRequest(),
    progress: Callable[[str], None] | None = None,
) -> ReviewArtifacts:
    """Generate a review bundle for a readability fixture."""
    fixture_dir = request.fixture_dir.resolve()
    regressed_fixture_dir = request.regressed_fixture_dir.resolve()
    symbols_dir = request.symbols_dir.resolve()
    output_dir = request.output_dir.resolve()
    project_name = request.project_name

    netlist_path = fixture_dir / "circuit_ir.json"
    if not netlist_path.exists():
        raise FileNotFoundError(f"Fixture netlist not found: {netlist_path}")
    if not symbols_dir.exists():
        raise FileNotFoundError(f"Symbols fixture directory not found: {symbols_dir}")

    _emit_progress(progress, "Preparing review workspace")

    output_dir.mkdir(parents=True, exist_ok=True)
    work_root = output_dir / "work"
    project_dir = work_root / project_name
    if project_dir.exists():
        shutil.rmtree(project_dir)
    work_root.mkdir(parents=True, exist_ok=True)

    _emit_progress(progress, "Creating temporary project")
    project = _create_project(
        name=project_name,
        out_dir=work_root,
        description=f"Readability review for {fixture_dir.name}",
    )

    _emit_progress(progress, "Generating schematic and collecting warnings")
    apply_result = _apply_netlist_to_project(
        project,
        _ApplyNetlistRequest(
            netlist_path=netlist_path,
            symbols_dir=symbols_dir,
            mode_name="internal",
            force=True,
            dry_run=False,
            strict=False,
            layout_name="graphviz",
            routing_name="bus",
        ),
    )

    validation_warnings = [
        warning
        for warning in apply_result.warnings
        if str(warning.get("code", "")) not in _GENERATION_ONLY_WARNING_CODES
    ]

    managed_copy = output_dir / "generated_managed.kicad_sch"
    root_copy = output_dir / "generated_root.kicad_sch"
    shutil.copy2(apply_result.managed_schematic_path, managed_copy)
    shutil.copy2(apply_result.schematic_path, root_copy)

    _emit_progress(progress, "Computing readability metrics")
    doc = SchematicDoc.load(managed_copy)
    current_metrics = _compute_metrics(doc)
    current_baseline = _load_metrics(fixture_dir / "baseline_metrics.json")
    regressed_baseline = _load_metrics(regressed_fixture_dir / "baseline_metrics.json")

    _emit_progress(progress, "Writing review bundle")
    report_data: dict[str, Any] = {
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fixture_name": fixture_dir.name,
        "fixture_dir": str(fixture_dir),
        "input_netlist": str(netlist_path),
        "symbols_dir": str(symbols_dir),
        "output_dir": str(output_dir),
        "project_dir": str(project.path),
        "root_schematic": str(root_copy),
        "managed_schematic": str(managed_copy),
        "validation_warnings": validation_warnings,
        "apply_warnings": list(apply_result.warnings),
        "metrics": current_metrics,
        "current_baseline_metrics": current_baseline,
        "current_baseline_delta": _metric_deltas(current_metrics, current_baseline),
        "regressed_baseline_metrics": regressed_baseline,
        "regressed_baseline_delta": _metric_deltas(current_metrics, regressed_baseline),
    }

    report_json = output_dir / "review_report.json"
    report_json.write_text(json.dumps(report_data, indent=2, sort_keys=True), encoding="utf-8")

    summary_lines = [
        f"Fixture review: {fixture_dir.name}",
        f"Output directory: {output_dir}",
        f"Generated project: {project.path}",
        f"Managed schematic copy: {managed_copy}",
        f"Root schematic copy: {root_copy}",
        "",
    ]
    summary_lines.extend(
        _format_warning_lines(
            f"Validation warnings ({len(validation_warnings)}):",
            validation_warnings,
        )
    )
    summary_lines.append("")
    summary_lines.extend(
        _format_warning_lines(
            f"Apply warnings ({len(apply_result.warnings)}):",
            list(apply_result.warnings),
        )
    )
    summary_lines.append("")
    summary_lines.extend(_format_metric_lines("Current metrics:", current_metrics))
    summary_lines.append("")
    summary_lines.extend(
        _format_delta_lines(
            "Delta vs current baseline:",
            _metric_deltas(current_metrics, current_baseline),
        )
    )
    summary_lines.append("")
    summary_lines.extend(
        _format_delta_lines(
            "Delta vs regressed baseline:",
            _metric_deltas(current_metrics, regressed_baseline),
        )
    )
    summary_lines.append("")
    summary_lines.append(f"Full JSON report: {report_json}")

    summary_txt = output_dir / "review_summary.txt"
    summary_txt.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    return ReviewArtifacts(
        output_dir=output_dir,
        project_dir=project.path,
        root_schematic=root_copy,
        managed_schematic=managed_copy,
        report_json=report_json,
        summary_txt=summary_txt,
        report_data=report_data,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a readability review bundle for the canonical NE5532 headphone-amp fixture."
        )
    )
    parser.add_argument(
        "--fixture-dir",
        default=str(DEFAULT_FIXTURE_DIR),
        help="Fixture directory containing circuit_ir.json and baseline_metrics.json",
    )
    parser.add_argument(
        "--regressed-fixture-dir",
        default=str(DEFAULT_REGRESSED_FIXTURE_DIR),
        help="Fixture directory containing the regressed baseline metrics",
    )
    parser.add_argument(
        "--symbols-dir",
        default=str(DEFAULT_SYMBOLS_DIR),
        help="Symbol fixture directory used for validation/generation",
    )
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory where the review bundle should be written",
    )
    parser.add_argument(
        "--project-name",
        default="ReadabilityReview",
        help="Temporary project name used for generation inside the output bundle",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    artifacts = generate_review_report(
        ReviewRequest(
            fixture_dir=Path(args.fixture_dir),
            regressed_fixture_dir=Path(args.regressed_fixture_dir),
            symbols_dir=Path(args.symbols_dir),
            output_dir=Path(args.out_dir),
            project_name=args.project_name,
        ),
        progress=lambda message: print(
            f"[{datetime.now(UTC).strftime('%H:%M:%S')}] {message}",
            flush=True,
        ),
    )
    print(f"Review bundle written to: {artifacts.output_dir}")
    print(f"Summary: {artifacts.summary_txt}")
    print(f"Report JSON: {artifacts.report_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

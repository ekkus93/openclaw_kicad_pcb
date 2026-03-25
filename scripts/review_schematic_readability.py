#!/usr/bin/env python3
"""Generate a readability review bundle for the NE5532 headphone-amp fixture.

This developer utility regenerates the canonical readability fixture, captures
validation/apply warnings, computes the existing readability metrics, compares
them against the checked-in baselines, and writes a small review bundle to a
deterministic output directory.
"""

from __future__ import annotations

import argparse
import html
import importlib
import json
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_SRC = REPO_ROOT / "kicad-pcb" / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from tests import (  # noqa: E402
    NE5532_LEFT_CURRENT_READABILITY_FIXTURE,
    NE5532_LEFT_REGRESSED_READABILITY_FIXTURE,
    SYMBOLS_FIXTURE_DIR,
)

_NETLIST_MODULE = importlib.import_module("kicad_pcb.commands.netlist")
_PROJECT_MODULE = importlib.import_module("kicad_pcb.commands._project")
_SCH_APPLY_MODULE = importlib.import_module("kicad_pcb.commands._sch_apply")
_SCH_DOC_MODULE = importlib.import_module("kicad_pcb.sch_doc")
_SCHEMATIC_METRICS_MODULE = importlib.import_module("kicad_pcb.schematic_metrics")
_ADAPTERS_MODULE = importlib.import_module("kicad_pcb.adapters")
_RUNNER_MODULE = importlib.import_module("kicad_pcb.runner")
_ERRORS_MODULE = importlib.import_module("kicad_pcb.errors")
_SEXPR_NODES_MODULE = importlib.import_module("kicad_pcb.sexpr.nodes")

_create_project = _PROJECT_MODULE._create_project
_ApplyNetlistRequest = _SCH_APPLY_MODULE._ApplyNetlistRequest
_apply_netlist_to_project = _SCH_APPLY_MODULE._apply_netlist_to_project
SchematicDoc = _SCH_DOC_MODULE.SchematicDoc
KicadCliAdapter = _ADAPTERS_MODULE.KicadCliAdapter
find_kicad_cli = _RUNNER_MODULE.find_kicad_cli
check_kicad = _RUNNER_MODULE.check_kicad
ToolError = _ERRORS_MODULE.ToolError
AtomNode = _SEXPR_NODES_MODULE.AtomNode
ListNode = _SEXPR_NODES_MODULE.ListNode
StringNode = _SEXPR_NODES_MODULE.StringNode
average_symbol_spacing = _SCHEMATIC_METRICS_MODULE.average_symbol_spacing
count_distinct_x_columns = _SCHEMATIC_METRICS_MODULE.count_distinct_x_columns
count_global_labels = _SCHEMATIC_METRICS_MODULE.count_global_labels
count_power_symbols = _SCHEMATIC_METRICS_MODULE.count_power_symbols
count_short_wire_segments = _SCHEMATIC_METRICS_MODULE.count_short_wire_segments
page_region_density = _SCHEMATIC_METRICS_MODULE.page_region_density
wire_stub_ratio = _SCHEMATIC_METRICS_MODULE.wire_stub_ratio

DEFAULT_FIXTURE_DIR = NE5532_LEFT_CURRENT_READABILITY_FIXTURE.fixture_dir
DEFAULT_REGRESSED_FIXTURE_DIR = NE5532_LEFT_REGRESSED_READABILITY_FIXTURE.fixture_dir
DEFAULT_SYMBOLS_DIR = SYMBOLS_FIXTURE_DIR
DEFAULT_OUTPUT_DIR = REPO_ROOT / "code_review" / "generated" / "ne5532_headphone_amp_review"
BASELINE_SCHEMATIC_NAME = "baseline_generated.kicad_sch"
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
    preview_renders: tuple[PreviewRender, ...] = ()


@dataclass(frozen=True)
class PreviewRender:
    """Rendered comparison artifact emitted into the review bundle."""

    name: str
    source_schematic: Path
    svg_file: Path | None = None
    png_file: Path | None = None
    status: str = "skipped"
    note: str | None = None


@dataclass(frozen=True)
class ReviewBundleContext:
    """Shared state used to write the review JSON and summary bundle."""

    fixture_dir: Path
    netlist_path: Path
    symbols_dir: Path
    output_dir: Path
    project_dir: Path
    root_copy: Path
    managed_copy: Path
    preview_renders: tuple[PreviewRender, ...]
    validation_warnings: list[dict[str, Any]]
    apply_warnings: tuple[dict[str, Any], ...]
    current_metrics: dict[str, int | float | dict[str, float]]
    current_baseline: dict[str, Any] | None
    regressed_baseline: dict[str, Any] | None


@dataclass(frozen=True)
class ReviewRequest:
    """Inputs required to generate a readability review bundle."""

    fixture_dir: Path = DEFAULT_FIXTURE_DIR
    regressed_fixture_dir: Path = DEFAULT_REGRESSED_FIXTURE_DIR
    symbols_dir: Path = DEFAULT_SYMBOLS_DIR
    output_dir: Path = DEFAULT_OUTPUT_DIR
    project_name: str = "ReadabilityReview"


class SymbolPreviewEntry(TypedDict):
    ref: str
    symbol_id: str
    value: str
    uuid: str
    x: float
    y: float
    unit: str


def _parse_float_atom(node: Any) -> float | None:
    if not isinstance(node, AtomNode):
        return None
    try:
        return float(node.value)
    except ValueError:
        return None


def _symbol_preview_entries(doc: Any) -> list[SymbolPreviewEntry]:
    entries: list[SymbolPreviewEntry] = []
    for symbol in doc.list_symbols():
        x_value = symbol.get("x")
        y_value = symbol.get("y")
        if not isinstance(x_value, (int, float)) or not isinstance(y_value, (int, float)):
            continue
        entries.append(
            {
                "ref": str(symbol.get("ref", "")),
                "symbol_id": str(symbol.get("symbol_id", "")),
                "value": str(symbol.get("value", "")),
                "uuid": str(symbol.get("uuid", "")),
                "x": float(x_value),
                "y": float(y_value),
                "unit": str(symbol.get("unit", "")),
            }
        )
    return entries


def _parse_xy_node(node: Any) -> tuple[float, float] | None:
    if not isinstance(node, ListNode) or node.key != "xy" or len(node.items) < 3:
        return None
    x = _parse_float_atom(node.items[1])
    y = _parse_float_atom(node.items[2])
    if x is None or y is None:
        return None
    return (x, y)


def _node_at_position(node: Any) -> tuple[float, float] | None:
    if not isinstance(node, ListNode) or node.key != "at" or len(node.items) < 3:
        return None
    x = _parse_float_atom(node.items[1])
    y = _parse_float_atom(node.items[2])
    if x is None or y is None:
        return None
    return (x, y)


def _extract_wire_segments(doc: Any) -> list[tuple[float, float, float, float]]:
    segments: list[tuple[float, float, float, float]] = []
    for item in doc.root.items:
        if not isinstance(item, ListNode) or item.key != "wire":
            continue
        pts_node = next(
            (child for child in item.items if isinstance(child, ListNode) and child.key == "pts"),
            None,
        )
        if pts_node is None:
            continue
        points = [
            point
            for point in (_parse_xy_node(child) for child in pts_node.items)
            if point is not None
        ]
        for start, end in zip(points, points[1:], strict=False):
            segments.append((start[0], start[1], end[0], end[1]))
    return segments


def _extract_label_positions(doc: Any) -> list[tuple[str, float, float]]:
    labels: list[tuple[str, float, float]] = []
    for item in doc.root.items:
        if not isinstance(item, ListNode) or item.key not in {"label", "global_label"}:
            continue
        if len(item.items) < 2 or not isinstance(item.items[1], StringNode):
            continue
        at_node = next(
            (child for child in item.items if isinstance(child, ListNode) and child.key == "at"),
            None,
        )
        coords = _node_at_position(at_node)
        if coords is None:
            continue
        labels.append((item.items[1].value, coords[0], coords[1]))
    return labels


def _extract_junction_positions(doc: Any) -> list[tuple[float, float]]:
    junctions: list[tuple[float, float]] = []
    for item in doc.root.items:
        if not isinstance(item, ListNode) or item.key != "junction":
            continue
        at_node = next(
            (child for child in item.items if isinstance(child, ListNode) and child.key == "at"),
            None,
        )
        coords = _node_at_position(at_node)
        if coords is not None:
            junctions.append(coords)
    return junctions


def _extract_sheet_boxes(doc: Any) -> list[tuple[float, float, float, float, str]]:
    sheets: list[tuple[float, float, float, float, str]] = []
    for item in doc.root.items:
        if not isinstance(item, ListNode) or item.key != "sheet":
            continue
        at_node = next(
            (child for child in item.items if isinstance(child, ListNode) and child.key == "at"),
            None,
        )
        size_node = next(
            (child for child in item.items if isinstance(child, ListNode) and child.key == "size"),
            None,
        )
        if at_node is None or size_node is None:
            continue
        at_coords = _node_at_position(at_node)
        width = _parse_float_atom(size_node.items[1]) if len(size_node.items) >= 3 else None
        height = _parse_float_atom(size_node.items[2]) if len(size_node.items) >= 3 else None
        if at_coords is None or width is None or height is None:
            continue
        sheet_name = "sheet"
        for child in item.items:
            if (
                isinstance(child, ListNode)
                and child.key == "property"
                and len(child.items) >= 3
                and isinstance(child.items[1], StringNode)
                and isinstance(child.items[2], StringNode)
                and child.items[1].value == "Sheetname"
            ):
                sheet_name = child.items[2].value
                break
        sheets.append((at_coords[0], at_coords[1], width, height, sheet_name))
    return sheets


def _collect_preview_bounds(
    symbols: list[SymbolPreviewEntry],
    wires: list[tuple[float, float, float, float]],
    labels: list[tuple[str, float, float]],
    junctions: list[tuple[float, float]],
    sheets: list[tuple[float, float, float, float, str]],
) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for symbol in symbols:
        xs.append(symbol["x"])
        ys.append(symbol["y"])
    for x1, y1, x2, y2 in wires:
        xs.extend((x1, x2))
        ys.extend((y1, y2))
    for _name, x, y in labels:
        xs.append(x)
        ys.append(y)
    for x, y in junctions:
        xs.append(x)
        ys.append(y)
    for x, y, width, height, _sheet_name in sheets:
        xs.extend((x, x + width))
        ys.extend((y, y + height))

    if not xs or not ys:
        return (0.0, 100.0, 0.0, 100.0)
    return (min(xs), max(xs), min(ys), max(ys))


def _append_wire_svg_lines(
    svg_lines: list[str],
    wires: list[tuple[float, float, float, float]],
    project_x: Callable[[float], float],
    project_y: Callable[[float], float],
) -> None:
    for x1, y1, x2, y2 in wires:
        svg_lines.append(
            "  <line "
            f'x1="{project_x(x1):.1f}" y1="{project_y(y1):.1f}" '
            f'x2="{project_x(x2):.1f}" y2="{project_y(y2):.1f}" '
            'stroke="#0f172a" stroke-width="2" stroke-linecap="round"/>'
        )


def _append_sheet_svg_lines(
    svg_lines: list[str],
    sheets: list[tuple[float, float, float, float, str]],
    project_x: Callable[[float], float],
    project_y: Callable[[float], float],
    scale: float,
) -> None:
    for x, y, width, height, sheet_name in sheets:
        svg_lines.append(
            "  <rect "
            f'x="{project_x(x):.1f}" y="{project_y(y):.1f}" '
            f'width="{width * scale:.1f}" height="{height * scale:.1f}" '
            'fill="#eff6ff" stroke="#2563eb" stroke-width="2" rx="8"/>'
        )
        svg_lines.append(
            "  <text "
            f'x="{project_x(x) + 8:.1f}" y="{project_y(y) + 20:.1f}" '
            'font-family="monospace" font-size="16" fill="#1d4ed8">'
            f"{html.escape(sheet_name)}</text>"
        )


def _append_symbol_svg_lines(
    svg_lines: list[str],
    symbols: list[SymbolPreviewEntry],
    project_x: Callable[[float], float],
    project_y: Callable[[float], float],
) -> None:
    for symbol in symbols:
        x = project_x(symbol["x"])
        y = project_y(symbol["y"])
        ref = html.escape(symbol["ref"])
        value = html.escape(symbol["value"])
        symbol_id = html.escape(symbol["symbol_id"])
        svg_lines.append(
            "  <rect "
            f'x="{x - 28:.1f}" y="{y - 16:.1f}" width="56" height="32" '
            'fill="#f8fafc" stroke="#334155" stroke-width="2" rx="6"/>'
        )
        svg_lines.append(
            "  <text "
            f'x="{x:.1f}" y="{y - 2:.1f}" text-anchor="middle" '
            'font-family="monospace" font-size="13" fill="#0f172a">'
            f"{ref}</text>"
        )
        if value:
            svg_lines.append(
                "  <text "
                f'x="{x:.1f}" y="{y + 12:.1f}" text-anchor="middle" '
                'font-family="monospace" font-size="10" fill="#475569">'
                f"{value}</text>"
            )
        elif symbol_id:
            svg_lines.append(
                "  <text "
                f'x="{x:.1f}" y="{y + 12:.1f}" text-anchor="middle" '
                'font-family="monospace" font-size="10" fill="#475569">'
                f"{symbol_id}</text>"
            )


def _append_label_svg_lines(
    svg_lines: list[str],
    labels: list[tuple[str, float, float]],
    project_x: Callable[[float], float],
    project_y: Callable[[float], float],
) -> None:
    for label, x, y in labels:
        svg_lines.append(
            "  <text "
            f'x="{project_x(x) + 6:.1f}" y="{project_y(y) - 6:.1f}" '
            'font-family="monospace" font-size="11" fill="#7c3aed">'
            f"{html.escape(label)}</text>"
        )


def _append_junction_svg_lines(
    svg_lines: list[str],
    junctions: list[tuple[float, float]],
    project_x: Callable[[float], float],
    project_y: Callable[[float], float],
) -> None:
    for x, y in junctions:
        svg_lines.append(
            f'  <circle cx="{project_x(x):.1f}" cy="{project_y(y):.1f}" r="3.5" fill="#0f172a"/>'
        )


def _write_internal_svg_preview(source_schematic: Path, svg_file: Path) -> str:
    doc = SchematicDoc.load(source_schematic)
    symbols = _symbol_preview_entries(doc)
    wires = _extract_wire_segments(doc)
    labels = _extract_label_positions(doc)
    junctions = _extract_junction_positions(doc)
    sheets = _extract_sheet_boxes(doc)
    min_x, max_x, min_y, max_y = _collect_preview_bounds(
        symbols,
        wires,
        labels,
        junctions,
        sheets,
    )
    padding_mm = 12.0
    scale = 8.0
    view_width = max((max_x - min_x) + (padding_mm * 2), 40.0)
    view_height = max((max_y - min_y) + (padding_mm * 2), 30.0)

    def project_x(value: float) -> float:
        return (value - min_x + padding_mm) * scale

    def project_y(value: float) -> float:
        return (value - min_y + padding_mm) * scale

    width_px = view_width * scale
    height_px = view_height * scale
    svg_lines = [
        (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{width_px:.0f}" height="{height_px:.0f}" '
            f'viewBox="0 0 {width_px:.0f} {height_px:.0f}">'
        ),
        '  <rect width="100%" height="100%" fill="#fffdf8"/>',
        (
            '  <text x="24" y="32" font-family="monospace" '
            f'font-size="18" fill="#334155">{html.escape(source_schematic.name)}</text>'
        ),
    ]
    _append_wire_svg_lines(svg_lines, wires, project_x, project_y)
    _append_sheet_svg_lines(svg_lines, sheets, project_x, project_y, scale)
    _append_symbol_svg_lines(svg_lines, symbols, project_x, project_y)
    _append_label_svg_lines(svg_lines, labels, project_x, project_y)
    _append_junction_svg_lines(svg_lines, junctions, project_x, project_y)
    svg_lines.append("</svg>")
    svg_file.write_text("\n".join(svg_lines) + "\n", encoding="utf-8")
    return "Rendered via internal schematic fallback"


def _default_png_renderer(svg_file: Path, png_file: Path) -> bool:
    try:
        cairosvg = importlib.import_module("cairosvg")
    except ImportError:
        return False
    cairosvg.svg2png(url=str(svg_file), write_to=str(png_file))
    return png_file.exists()


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


def _render_schematic_preview(
    *,
    name: str,
    source_schematic: Path,
    output_dir: Path,
    cli: Any | None,
    png_renderer: Callable[[Path, Path], bool] | None,
) -> PreviewRender:
    if not source_schematic.exists():
        return PreviewRender(
            name=name,
            source_schematic=source_schematic,
            status="skipped",
            note="Source schematic not found",
        )

    resolved_cli = cli
    if resolved_cli is None:
        try:
            check_kicad()
        except ToolError as exc:
            return PreviewRender(
                name=name,
                source_schematic=source_schematic,
                status="skipped",
                note=str(exc).splitlines()[0],
            )
        resolved_cli = KicadCliAdapter(kicad_cli=find_kicad_cli())

    export_dir = output_dir / f"{name}_export"
    if export_dir.exists():
        shutil.rmtree(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)

    svg_file = output_dir / f"{name}.svg"
    fallback_note: str | None = None
    result = resolved_cli.export_svg_sch(source_schematic, export_dir)
    exported_svg = export_dir / f"{source_schematic.stem}.svg"
    if not exported_svg.exists():
        exported_candidates = sorted(export_dir.rglob("*.svg"))
        if exported_candidates:
            exported_svg = exported_candidates[0]

    if result.returncode == 0 and exported_svg.exists():
        shutil.copy2(exported_svg, svg_file)
    else:
        note = getattr(result, "output_text", lambda: "")()
        try:
            fallback_note = _write_internal_svg_preview(source_schematic, svg_file)
        except Exception as exc:
            shutil.rmtree(export_dir)
            return PreviewRender(
                name=name,
                source_schematic=source_schematic,
                status="failed",
                note=(note or "Schematic preview generation failed") + f" | fallback failed: {exc}",
            )
        if note:
            fallback_note = f"{fallback_note}; KiCad CLI: {note}"
    shutil.rmtree(export_dir)

    resolved_png_renderer = png_renderer or _default_png_renderer
    png_path = output_dir / f"{name}.png"
    png_file: Path | None = png_path
    png_note: str | None = None
    try:
        png_written = resolved_png_renderer(svg_file, png_path)
    except Exception as exc:
        png_written = False
        png_note = f"PNG conversion failed: {exc}"

    if not png_written:
        png_file = None
        if png_note is None:
            png_note = "PNG conversion unavailable"

    return PreviewRender(
        name=name,
        source_schematic=source_schematic,
        svg_file=svg_file,
        png_file=png_file,
        status="rendered",
        note="; ".join(note for note in (fallback_note, png_note) if note),
    )


def _format_preview_lines(preview_renders: tuple[PreviewRender, ...]) -> list[str]:
    lines = ["Preview renders:"]
    if not preview_renders:
        lines.append("  (none)")
        return lines
    for preview in preview_renders:
        lines.append(f"  - {preview.name}: {preview.status}")
        if preview.svg_file is not None:
            lines.append(f"      svg: {preview.svg_file}")
        if preview.png_file is not None:
            lines.append(f"      png: {preview.png_file}")
        if preview.note:
            lines.append(f"      note: {preview.note}")
    return lines


def _preview_render_report_entry(preview: PreviewRender) -> dict[str, Any]:
    return {
        "name": preview.name,
        "source_schematic": str(preview.source_schematic),
        "svg_file": str(preview.svg_file) if preview.svg_file is not None else None,
        "png_file": str(preview.png_file) if preview.png_file is not None else None,
        "status": preview.status,
        "note": preview.note,
    }


def _build_report_data(
    context: ReviewBundleContext,
) -> dict[str, Any]:
    return {
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fixture_name": context.fixture_dir.name,
        "fixture_dir": str(context.fixture_dir),
        "input_netlist": str(context.netlist_path),
        "symbols_dir": str(context.symbols_dir),
        "output_dir": str(context.output_dir),
        "project_dir": str(context.project_dir),
        "root_schematic": str(context.root_copy),
        "managed_schematic": str(context.managed_copy),
        "preview_renders": [
            _preview_render_report_entry(preview) for preview in context.preview_renders
        ],
        "validation_warnings": context.validation_warnings,
        "apply_warnings": list(context.apply_warnings),
        "metrics": context.current_metrics,
        "current_baseline_metrics": context.current_baseline,
        "current_baseline_delta": _metric_deltas(
            context.current_metrics,
            context.current_baseline,
        ),
        "regressed_baseline_metrics": context.regressed_baseline,
        "regressed_baseline_delta": _metric_deltas(
            context.current_metrics,
            context.regressed_baseline,
        ),
    }


def _build_summary_lines(
    context: ReviewBundleContext,
    report_json: Path,
) -> list[str]:
    summary_lines = [
        f"Fixture review: {context.fixture_dir.name}",
        f"Output directory: {context.output_dir}",
        f"Generated project: {context.project_dir}",
        f"Managed schematic copy: {context.managed_copy}",
        f"Root schematic copy: {context.root_copy}",
        "",
    ]
    summary_lines.extend(_format_preview_lines(context.preview_renders))
    summary_lines.append("")
    summary_lines.extend(
        _format_warning_lines(
            f"Validation warnings ({len(context.validation_warnings)}):",
            context.validation_warnings,
        )
    )
    summary_lines.append("")
    summary_lines.extend(
        _format_warning_lines(
            f"Apply warnings ({len(context.apply_warnings)}):",
            list(context.apply_warnings),
        )
    )
    summary_lines.append("")
    summary_lines.extend(_format_metric_lines("Current metrics:", context.current_metrics))
    summary_lines.append("")
    summary_lines.extend(
        _format_delta_lines(
            "Delta vs current baseline:",
            _metric_deltas(context.current_metrics, context.current_baseline),
        )
    )
    summary_lines.append("")
    summary_lines.extend(
        _format_delta_lines(
            "Delta vs regressed baseline:",
            _metric_deltas(context.current_metrics, context.regressed_baseline),
        )
    )
    summary_lines.append("")
    summary_lines.append(f"Full JSON report: {report_json}")
    return summary_lines


def generate_review_report(
    request: ReviewRequest = ReviewRequest(),
    progress: Callable[[str], None] | None = None,
    preview_cli: Any | None = None,
    png_renderer: Callable[[Path, Path], bool] | None = None,
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

    _emit_progress(progress, "Rendering preview snapshots")
    preview_renders = (
        _render_schematic_preview(
            name="current_baseline_preview",
            source_schematic=fixture_dir / BASELINE_SCHEMATIC_NAME,
            output_dir=output_dir,
            cli=preview_cli,
            png_renderer=png_renderer,
        ),
        _render_schematic_preview(
            name="improved_output_preview",
            source_schematic=managed_copy,
            output_dir=output_dir,
            cli=preview_cli,
            png_renderer=png_renderer,
        ),
    )

    _emit_progress(progress, "Computing readability metrics")
    doc = SchematicDoc.load(managed_copy)
    current_metrics = _compute_metrics(doc)
    current_baseline = _load_metrics(fixture_dir / "baseline_metrics.json")
    regressed_baseline = _load_metrics(regressed_fixture_dir / "baseline_metrics.json")
    bundle_context = ReviewBundleContext(
        fixture_dir=fixture_dir,
        netlist_path=netlist_path,
        symbols_dir=symbols_dir,
        output_dir=output_dir,
        project_dir=project.path,
        root_copy=root_copy,
        managed_copy=managed_copy,
        preview_renders=preview_renders,
        validation_warnings=validation_warnings,
        apply_warnings=apply_result.warnings,
        current_metrics=current_metrics,
        current_baseline=current_baseline,
        regressed_baseline=regressed_baseline,
    )

    _emit_progress(progress, "Writing review bundle")
    report_data = _build_report_data(bundle_context)

    report_json = output_dir / "review_report.json"
    report_json.write_text(json.dumps(report_data, indent=2, sort_keys=True), encoding="utf-8")

    summary_lines = _build_summary_lines(bundle_context, report_json)

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
        preview_renders=preview_renders,
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

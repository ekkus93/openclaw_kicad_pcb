"""Generated-schematic validation and warning-report helpers for apply-netlist."""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from ..circuit_ir import CircuitIR
from ..errors import ErrorCode, UserError
from ..models import ProjectRef
from ..results import GeneratedSchematicDiagnostics
from ..sch_doc import SchematicDoc
from ..sexpr.nodes import ListNode
from ..sexpr.parser import parse
from ..sexpr.serializer import serialize

WARNING_REPORT_FILE = "warnings.json"
MIN_COMPONENT_PLACEMENT_RATIO = 0.8


class WarningReportRequest(Protocol):
    """Minimal request surface needed to serialize the warning sidecar."""

    @property
    def netlist_path(self) -> Path: ...

    @property
    def mode_name(self) -> str | None: ...


def _cleanup_new_managed_file(managed_sch_path: Path, original_error: Exception) -> None:
    """Best-effort cleanup for a newly-created managed schematic after failure.

    Suppresses race-style missing-file errors only. Other cleanup errors are
    attached to *original_error* so the primary failure remains the raised
    exception.
    """
    try:
        managed_sch_path.unlink(missing_ok=True)
    except FileNotFoundError:
        return
    except OSError as cleanup_error:
        cleanup_note = (
            "Managed-sheet cleanup failed after apply-netlist error: "
            f"{managed_sch_path} ({cleanup_error})"
        )
        add_note = getattr(original_error, "add_note", None)
        if callable(add_note):
            add_note(cleanup_note)
        else:
            setattr(original_error, "cleanup_note", cleanup_note)


def _write_warning_report(  # noqa: PLR0913
    *,
    project: ProjectRef,
    request: WarningReportRequest,
    warnings: Sequence[dict[str, object]],
    schematic_path: Path,
    stats: dict[str, int],
    kicad_cli_used: bool,
    symbols_dirs: tuple[str, ...],
    generated_schematic_diagnostics: GeneratedSchematicDiagnostics | None,
) -> Path:
    """Write a deterministic advisory-warning sidecar for a generated project."""
    warning_report_path = project.path / WARNING_REPORT_FILE
    payload = {
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "project_name": project.name,
        "project_path": str(project.path),
        "netlist_path": str(request.netlist_path),
        "root_schematic_path": str(project.sch_file),
        "schematic_path": str(schematic_path),
        "validation_mode": request.mode_name or "kicad",
        "kicad_cli_used": kicad_cli_used,
        "symbols_dirs_used": list(symbols_dirs),
        "managed_stats": dict(stats),
        "generated_schematic_diagnostics": (
            generated_schematic_diagnostics.as_dict()
            if generated_schematic_diagnostics is not None
            else None
        ),
        "warning_count": len(warnings),
        "warnings": list(warnings),
    }
    warning_report_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return warning_report_path


def _format_binding_key(ref: str, pin: str, net_name: str) -> str:
    return f"{ref}.{pin}@{net_name}"


def _hard_failure(
    code: str,
    message: str,
    *,
    details: dict[str, object],
) -> dict[str, object]:
    return {
        "severity": "hard_fail",
        "code": code,
        "message": message,
        "details": details,
    }


def _record_debug_stage(
    debug_capture: dict[str, object] | None,
    stage: str,
    **details: object,
) -> None:
    if debug_capture is None:
        return
    stage_markers = cast(
        list[dict[str, object]],
        debug_capture.setdefault("pipeline_stage_markers", []),
    )
    stage_markers.append({"stage": stage, **details})


def _reparse_generated_schematic(serialized: str, schematic_path: Path) -> SchematicDoc:
    """Reparse generated schematic text and preserve structured failure details."""
    try:
        return SchematicDoc(cast(ListNode, parse(serialized)))
    except Exception as exc:
        failure = _hard_failure(
            "REPARSE_FAILED",
            "Generated schematic could not be reparsed by the internal document model.",
            details={"schematic_path": str(schematic_path)},
        )
        diagnostics = GeneratedSchematicDiagnostics(
            symbol_count=0,
            wire_count=0,
            label_count=0,
            global_label_count=0,
            junction_count=0,
            binding_marker_count=0,
            hard_failures=(failure,),
        )
        raise UserError(
            "Generated managed schematic failed reparse validation",
            code=ErrorCode.PARSE_ERROR,
            details={
                "schematic_path": str(schematic_path),
                "generated_schematic_diagnostics": diagnostics.as_dict(),
            },
        ) from exc


def validate_generated_schematic(  # noqa: PLR0913
    *,
    doc: SchematicDoc,
    generation_ir: CircuitIR,
    schematic_path: Path,
    expected_wire_count: int,
    min_component_placement_ratio: float = MIN_COMPONENT_PLACEMENT_RATIO,
) -> GeneratedSchematicDiagnostics:
    """Finalize root instance paths, reparse, and enforce structural invariants."""

    doc.qualify_root_symbol_instance_paths()
    serialized = serialize(doc.root)
    reparsed_doc = _reparse_generated_schematic(serialized, schematic_path)

    expected_ref_list = [component.ref for component in generation_ir.components]
    expected_refs = sorted(set(expected_ref_list))
    actual_symbol_entries = reparsed_doc.list_symbols()
    actual_refs = sorted(
        {
            ref
            for entry in actual_symbol_entries
            if isinstance((ref := entry.get("ref")), str) and ref in expected_refs
        }
    )
    unresolved_refs = tuple(sorted(set(expected_refs) - set(actual_refs)))

    expected_bindings = [
        (pin.ref, pin.pin, net.name) for net in generation_ir.nets for pin in net.pins
    ]
    actual_bindings = [
        (binding["ref"], binding["pin"], binding["net_name"])
        for binding in reparsed_doc.extract_pin_label_bindings()
    ]

    expected_binding_counter = Counter(expected_bindings)
    actual_binding_counter = Counter(actual_bindings)
    missing_bindings = tuple(
        sorted(
            _format_binding_key(ref, pin, net_name)
            for (ref, pin, net_name), count in (
                expected_binding_counter - actual_binding_counter
            ).items()
            for _ in range(count)
        )
    )
    unexpected_bindings = tuple(
        sorted(
            _format_binding_key(ref, pin, net_name)
            for (ref, pin, net_name), count in (
                actual_binding_counter - expected_binding_counter
            ).items()
            for _ in range(count)
        )
    )
    duplicate_bindings = tuple(
        sorted(
            _format_binding_key(ref, pin, net_name)
            for (ref, pin, net_name), count in actual_binding_counter.items()
            if count > expected_binding_counter.get((ref, pin, net_name), 0)
        )
    )

    symbol_count = reparsed_doc.count_nodes("symbol")
    wire_count = reparsed_doc.count_nodes("wire")
    local_label_count = reparsed_doc.count_nodes("label")
    global_label_count = reparsed_doc.count_nodes("global_label")
    label_count = local_label_count + global_label_count
    junction_count = reparsed_doc.count_nodes("junction")
    binding_marker_count = len(actual_bindings)
    expected_components = len(expected_ref_list)
    hard_failures: list[dict[str, object]] = []

    if expected_components and symbol_count == 0:
        hard_failures.append(
            _hard_failure(
                "EMPTY_SYMBOL_GRAPH",
                "Generation produced no schematic symbols for a non-empty design.",
                details={
                    "expected_components": expected_components,
                    "symbol_count": symbol_count,
                },
            )
        )

    if expected_components:
        min_required = math.ceil(expected_components * min_component_placement_ratio)
        if symbol_count < min_required:
            hard_failures.append(
                _hard_failure(
                    "INSUFFICIENT_SYMBOLS",
                    "Generated schematic contains fewer placed symbols than required.",
                    details={
                        "expected_components": expected_components,
                        "symbol_count": symbol_count,
                        "min_component_placement_ratio": min_component_placement_ratio,
                        "min_required_symbols": min_required,
                    },
                )
            )

    if expected_wire_count > 0 and wire_count == 0:
        hard_failures.append(
            _hard_failure(
                "MISSING_WIRES",
                "Generated schematic is missing wires that the router expected to emit.",
                details={
                    "expected_wire_count": expected_wire_count,
                    "wire_count": wire_count,
                },
            )
        )

    if unresolved_refs:
        hard_failures.append(
            _hard_failure(
                "UNRESOLVED_REFS",
                "Some generated component references are missing from the emitted schematic.",
                details={"unresolved_refs": list(unresolved_refs)},
            )
        )

    if missing_bindings:
        hard_failures.append(
            _hard_failure(
                "MISSING_BINDINGS",
                "Generated schematic is missing one or more declared net bindings.",
                details={"missing_bindings": list(missing_bindings)},
            )
        )

    if unexpected_bindings:
        hard_failures.append(
            _hard_failure(
                "UNEXPECTED_BINDINGS",
                "Generated schematic contains unexpected net bindings not declared in the IR.",
                details={"unexpected_bindings": list(unexpected_bindings)},
            )
        )

    if duplicate_bindings:
        hard_failures.append(
            _hard_failure(
                "DUPLICATE_BINDINGS",
                "Generated schematic contains duplicated pin-to-net bindings.",
                details={"duplicate_bindings": list(duplicate_bindings)},
            )
        )

    if expected_components and binding_marker_count == 0:
        hard_failures.append(
            _hard_failure(
                "PSEUDO_POPULATED_SCHEMATIC",
                (
                    "Generated schematic lacks binding markers and does not carry "
                    "enough structure to be trusted."
                ),
                details={
                    "expected_binding_count": len(expected_bindings),
                    "binding_marker_count": binding_marker_count,
                },
            )
        )

    diagnostics = GeneratedSchematicDiagnostics(
        symbol_count=symbol_count,
        wire_count=wire_count,
        label_count=label_count,
        global_label_count=global_label_count,
        junction_count=junction_count,
        binding_marker_count=binding_marker_count,
        unresolved_refs=unresolved_refs,
        missing_bindings=missing_bindings,
        unexpected_bindings=unexpected_bindings,
        duplicate_bindings=duplicate_bindings,
        hard_failures=tuple(hard_failures),
    )

    if diagnostics.hard_failures:
        raise UserError(
            "Generated managed schematic failed structural validation",
            code=ErrorCode.EMPTY_GENERATION,
            details={
                "schematic_path": str(schematic_path),
                "expected_components": expected_components,
                "found_symbols": symbol_count,
                "min_component_placement_ratio": min_component_placement_ratio,
                "min_required_symbols": (
                    math.ceil(expected_components * min_component_placement_ratio)
                    if expected_components
                    else 0
                ),
                "generated_schematic_diagnostics": diagnostics.as_dict(),
            },
        )

    return diagnostics


def resolve_schematic_paths(project: ProjectRef) -> tuple[Path, Path]:
    """Return ``(root_sch_path, managed_sch_path)`` for *project*.

    The root schematic (``<name>.kicad_sch``) is intentionally **thin** — it
    contains only the project header and a ``(sheet ...)`` reference to the
    managed sheet. All generated content (symbols, wires, net labels) lives in
    the managed sheet (``OpenClaw_Managed.kicad_sch``).

    Neither path is guaranteed to exist on disk; callers should check with
    ``Path.exists()`` before loading.
    """
    return project.sch_file, project.path / "OpenClaw_Managed.kicad_sch"

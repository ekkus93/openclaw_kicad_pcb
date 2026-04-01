"""Schematic generation engine: symbol placement, pin transforms, managed-sheet lifecycle."""

from __future__ import annotations

import json
import math
import shutil
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict, cast

from ..adapters import KicadCliAdapter
from ..block_detection import classify_circuit
from ..circuit_ir import CircuitIR
from ..component_types import (
    TIER_SPACING_MM,
    component_type,
    is_power_net,
    normalize_gnd_net_name,
    power_rail_polarity,
)
from ..errors import ErrorCode, ToolError, UserError
from ..fs import _atomic_write, _new_uuid
from ..graphviz_layout import DEFAULT_LAYOUT_HEURISTIC_POLICY, LayoutHeuristicPolicy
from ..ir.validate import validate_circuit_ir, validate_ir_symbols
from ..layout import compute_orientations
from ..layout_engine import (
    LayoutEngine,
    make_layout_engine,
)
from ..models import ProjectRef
from ..pipeline import ValidationMode, mutate_and_validate_sch
from ..results import ApplyNetlistResult, GeneratedSchematicDiagnostics
from ..router import (
    DEFAULT_LABEL_POLICY,
    DEFAULT_ROUTING_HEURISTIC_POLICY,
    LABEL_MODE_POLICIES,
    LabelModeName,
    LabelPolicy,
    PinAnchor,
    RouteDecision,
    RoutingHeuristicPolicy,
    route_nets,
    write_routing,
)
from ..runner import find_kicad_cli
from ..sch_doc import SchematicDoc, read_lib_symbol_def_flat
from ..sexpr.nodes import ListNode
from ..sexpr.parser import parse
from ..sexpr.serializer import serialize
from ..symbol_index import SymbolIndex
from ..tier import assign_tiers
from ._project import minimal_schematic_text
from ._validate import advisory_warnings, raise_for_blocking_advisories

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MANAGED_SHEET_NAME = "OpenClaw_Managed"
MANAGED_SHEET_FILE = "OpenClaw_Managed.kicad_sch"
WARNING_REPORT_FILE = "OpenClaw_Warnings.json"
MIN_COMPONENT_PLACEMENT_RATIO = 0.8
_LOCAL_DECOUPLING_DISTANCE_WARN_MM = TIER_SPACING_MM * 1.5


def _prefer_decoupling_side_candidates(
    *,
    cap_y: float,
    rail_polarity: str | None,
    candidate_refs: list[str],
    raw_layout: dict[str, tuple[float, float, float | None]],
) -> list[str]:
    if rail_polarity == "positive":
        same_side = [ref for ref in candidate_refs if raw_layout[ref][1] > cap_y]
        return same_side or candidate_refs
    if rail_polarity == "negative":
        same_side = [ref for ref in candidate_refs if raw_layout[ref][1] < cap_y]
        return same_side or candidate_refs
    return candidate_refs


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
    request: _ApplyNetlistRequest,
    warnings: list[dict[str, object]],
    managed_sch_path: Path,
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
        "managed_schematic_path": str(managed_sch_path),
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


def validate_generated_schematic(  # noqa: PLR0913
    *,
    doc: SchematicDoc,
    generation_ir: CircuitIR,
    managed_sch_path: Path,
    expected_wire_count: int,
    min_component_placement_ratio: float = MIN_COMPONENT_PLACEMENT_RATIO,
) -> GeneratedSchematicDiagnostics:
    """Reparse the generated schematic and enforce hard structural invariants."""

    serialized = serialize(doc.root)
    try:
        reparsed_doc = SchematicDoc(cast(ListNode, parse(serialized)))
    except Exception as exc:
        failure = _hard_failure(
            "REPARSE_FAILED",
            "Generated schematic could not be reparsed by the internal document model.",
            details={"managed_schematic_path": str(managed_sch_path)},
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
                "managed_schematic_path": str(managed_sch_path),
                "generated_schematic_diagnostics": diagnostics.as_dict(),
            },
        ) from exc

    expected_refs = sorted(component.ref for component in generation_ir.components)
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
    expected_components = len(expected_refs)
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
                "managed_schematic_path": str(managed_sch_path),
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


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def resolve_schematic_paths(project: ProjectRef) -> tuple[Path, Path]:
    """Return ``(root_sch_path, managed_sch_path)`` for *project*.

    The root schematic (``<name>.kicad_sch``) is intentionally **thin** — it
    contains only the project header and a ``(sheet ...)`` reference to the
    managed sheet.  All generated content (symbols, wires, net labels) lives
    in the managed sheet (``OpenClaw_Managed.kicad_sch``).

    Neither path is guaranteed to exist on disk; callers should check with
    ``Path.exists()`` before loading.
    """
    return project.sch_file, project.path / MANAGED_SHEET_FILE


@dataclass(frozen=True)
class SchematicHeuristicProfile:
    """Bundle layout and routing heuristic policies under one named profile."""

    name: str
    layout_policy: LayoutHeuristicPolicy = DEFAULT_LAYOUT_HEURISTIC_POLICY
    routing_policy: RoutingHeuristicPolicy = DEFAULT_ROUTING_HEURISTIC_POLICY


ANALOG_AUDIO_HEURISTIC_PROFILE = SchematicHeuristicProfile(
    name="analog_audio",
    routing_policy=RoutingHeuristicPolicy(
        enable_compact_output_tails=True,
        enable_compact_local_ground_clusters=True,
        enable_small_analog_local_routing=True,
    ),
)
GENERIC_DIGITAL_HEURISTIC_PROFILE = SchematicHeuristicProfile(
    name="generic_digital",
    layout_policy=LayoutHeuristicPolicy(
        enable_decoupling_snap=False,
        enable_opamp_locality=False,
        enable_input_stage_cohesion=False,
        enable_output_stage_cohesion=False,
    ),
    routing_policy=RoutingHeuristicPolicy(
        enable_compact_output_tails=False,
        enable_compact_local_ground_clusters=False,
        enable_small_analog_local_routing=False,
    ),
)
POWER_SUPPLY_HEURISTIC_PROFILE = SchematicHeuristicProfile(
    name="power_supply",
    layout_policy=LayoutHeuristicPolicy(
        enable_decoupling_snap=False,
        enable_opamp_locality=False,
        enable_input_stage_cohesion=False,
        enable_output_stage_cohesion=False,
    ),
    routing_policy=RoutingHeuristicPolicy(
        enable_compact_output_tails=False,
        enable_compact_local_ground_clusters=True,
        enable_small_analog_local_routing=False,
    ),
)
DENSE_DEBUG_HEURISTIC_PROFILE = SchematicHeuristicProfile(
    name="dense_debug",
    layout_policy=LayoutHeuristicPolicy(
        enable_decoupling_snap=False,
        enable_opamp_locality=False,
        enable_input_stage_cohesion=False,
        enable_output_stage_cohesion=False,
    ),
    routing_policy=RoutingHeuristicPolicy(
        enable_compact_output_tails=False,
        enable_compact_local_ground_clusters=False,
        enable_small_analog_local_routing=False,
    ),
)
SCHEMATIC_HEURISTIC_PROFILES: dict[str, SchematicHeuristicProfile] = {
    profile.name: profile
    for profile in (
        ANALOG_AUDIO_HEURISTIC_PROFILE,
        GENERIC_DIGITAL_HEURISTIC_PROFILE,
        POWER_SUPPLY_HEURISTIC_PROFILE,
        DENSE_DEBUG_HEURISTIC_PROFILE,
    )
}
DEFAULT_SCHEMATIC_HEURISTIC_PROFILE = ANALOG_AUDIO_HEURISTIC_PROFILE


# ---------------------------------------------------------------------------
# Request dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ApplyNetlistRequest:
    netlist_path: Path
    symbols_dir: Path | None
    mode_name: str | None
    force: bool
    dry_run: bool
    backup: bool = False
    strict: bool = False
    layout_name: str | None = None
    routing_name: str | None = None
    label_mode_name: str | None = None
    heuristic_profile_name: str | None = None
    debug_dump_path: Path | None = None
    heuristic_profile: SchematicHeuristicProfile = DEFAULT_SCHEMATIC_HEURISTIC_PROFILE


@dataclass(frozen=True)
class _PlacedSymbolSpec:
    unit: int
    pin_nums: tuple[str, ...]


class _UnitSplitDebugEntry(TypedDict):
    placed_ref: str
    pin_nums: list[str]
    symbol: str
    unit: int


# ---------------------------------------------------------------------------
# Core apply logic
# ---------------------------------------------------------------------------


def _apply_netlist_to_project(
    project: ProjectRef,
    request: _ApplyNetlistRequest,
) -> ApplyNetlistResult:
    active_heuristic_profile = _resolve_heuristic_profile(
        request.heuristic_profile_name,
        default=request.heuristic_profile,
    )
    active_label_policy = _resolve_label_policy(request.label_mode_name)
    ir = CircuitIR.load(request.netlist_path)
    validate_circuit_ir(ir)

    mode = _resolve_mode(request.mode_name, default=ValidationMode.LINT)
    warnings: list[dict[str, object]] = []

    symbol_index = SymbolIndex(symbols_dir=request.symbols_dir)
    validate_ir_symbols(ir, symbol_index)
    raise_for_blocking_advisories(ir, symbol_index)
    warnings.extend(advisory_warnings(ir, symbol_index))
    generation_ir, placed_symbol_specs = _expand_generation_ir(ir, symbol_index)
    debug_capture: dict[str, object] = {
        "schematic_debug_artifacts": [
            "heuristic_profile_name",
            "label_mode_name",
            "validated_pipeline_path",
            "pipeline_stage_markers",
            "unit_splitting",
            "net_classification",
            "final_route_choices",
            "routing_heuristic_policy",
        ],
        "validated_pipeline_path": {
            "entrypoint": "apply-netlist",
            "schema_validation": "CircuitIR.load",
            "semantic_validation": "validate_circuit_ir",
            "symbol_pin_validation": "validate_ir_symbols",
            "schematic_emission": "mutate_and_validate_sch",
            "post_generation_reparse": "validate_generated_schematic",
            "artifact_finalize": "warning_report_or_dry_run",
        },
        "heuristic_profile_name": active_heuristic_profile.name,
        "label_mode_name": active_label_policy.mode_name,
        "unit_splitting": _build_unit_splitting_debug(
            source_ir=ir,
            generation_ir=generation_ir,
            placed_symbol_specs=placed_symbol_specs,
        ),
    }
    _record_debug_stage(
        debug_capture,
        "ir_creation",
        netlist_path=str(request.netlist_path),
        component_count=len(ir.components),
        net_count=len(ir.nets),
    )
    _record_debug_stage(
        debug_capture,
        "semantic_validation",
        validator="validate_circuit_ir+validate_ir_symbols",
        component_count=len(ir.components),
        net_count=len(ir.nets),
    )

    # Pre-flight: check kicad-cli availability BEFORE touching the filesystem.
    # This prevents a half-initialised project where the root schematic has been
    # adopted and the managed schematic is an empty stub, but the generation
    # itself cannot run because a required tool is missing.
    cli: KicadCliAdapter | None = None
    if mode >= ValidationMode.KICAD:
        if shutil.which("kicad-cli") is None:
            raise ToolError(
                "kicad-cli is required for --mode kicad",
                code=ErrorCode.KICAD_CLI_MISSING,
                details={
                    "hint": "Install KiCad or use --mode internal",
                },
            )
        cli = KicadCliAdapter(kicad_cli=find_kicad_cli())
    else:
        warnings.append(
            {
                "code": "VALIDATION_MODE_INTERNAL",
                "message": "Using internal validation mode (kicad-cli not required).",
                "details": {"kicad_cli_used": False},
            }
        )

    sheet_uuid = _ensure_project_root_owned(project, force=request.force, dry_run=request.dry_run)

    managed_sch_path = project.path / MANAGED_SHEET_FILE
    # Track whether we are about to create the managed file for the first time.
    # If generation subsequently fails we delete the empty stub so the project
    # is left in a clean, retryable state rather than having a misleading
    # zero-content managed schematic on disk.
    managed_was_absent = not managed_sch_path.exists()
    _ensure_managed_file_exists(managed_sch_path, dry_run=request.dry_run)

    stats: dict[str, int] = {
        "symbols": 0,
        "wires": 0,
        "labels": 0,
        "global_labels": 0,
        "power_symbols": 0,
        "junctions": 0,
        "binding_markers": 0,
    }
    diagnostics_capture: dict[str, GeneratedSchematicDiagnostics] = {}

    try:
        _record_debug_stage(
            debug_capture,
            "schematic_emission",
            managed_schematic_path=str(managed_sch_path),
            validation_mode=mode.name,
            dry_run=request.dry_run,
        )
        mutate_and_validate_sch(
            managed_sch_path,
            _build_managed_mutator(
                ir=ir,
                generation_ir=generation_ir,
                project=project,
                symbol_index=symbol_index,
                placed_symbol_specs=placed_symbol_specs,
                sheet_uuid=sheet_uuid,
                request=request,
                active_heuristic_profile=active_heuristic_profile,
                active_label_policy=active_label_policy,
                stats=stats,
                warnings=warnings,
                managed_sch_path=managed_sch_path,
                diagnostics_capture=diagnostics_capture,
                debug_capture=debug_capture,
            ),
            mode=mode,
            cli=cli,
            operation="apply-netlist",
            dry_run=request.dry_run,
            backup=request.backup,
            strict=request.strict,
        )
    except Exception as exc:
        # If the managed schematic was newly created as an empty stub and the
        # mutation failed, remove it so that the project is left in a clean state.
        # A subsequent retry will reinitialise the file from scratch.
        # Suppress only race-style missing-file errors so they do not shadow
        # the original exception; surface other cleanup failures as notes.
        if managed_was_absent and not request.dry_run:
            _cleanup_new_managed_file(managed_sch_path, exc)
        raise

    if request.dry_run:
        warnings.append(
            {
                "code": "DRY_RUN_NO_WRITE",
                "message": ("Dry-run mode: validation passed but no changes were written to disk."),
                "details": {
                    "root_schematic_path": str(project.sch_file),
                    "managed_schematic_path": str(managed_sch_path),
                    "symbols_validated": stats["symbols"],
                    "nets_validated": len(ir.nets),
                },
            }
        )
        _record_debug_stage(
            debug_capture,
            "artifact_finalize",
            status="dry_run",
            managed_schematic_path=str(managed_sch_path),
        )

    warning_report_path: Path | None = None
    if not request.dry_run:
        warning_report_path = _write_warning_report(
            project=project,
            request=request,
            warnings=warnings,
            managed_sch_path=managed_sch_path,
            stats=stats,
            kicad_cli_used=cli is not None,
            symbols_dirs=tuple(str(d) for d in symbol_index.directories),
            generated_schematic_diagnostics=diagnostics_capture.get("generated_schematic"),
        )
        _record_debug_stage(
            debug_capture,
            "artifact_finalize",
            status="written",
            managed_schematic_path=str(managed_sch_path),
            warning_report_path=str(warning_report_path),
        )

    if request.debug_dump_path is not None:
        _write_schematic_debug_dump(request.debug_dump_path, debug_capture)

    return ApplyNetlistResult(
        schematic_path=project.sch_file,
        managed_schematic_path=managed_sch_path,
        symbols_added=stats["symbols"],
        symbols_updated=0,
        managed_items_written=(
            stats["symbols"] + stats["wires"] + stats["labels"] + stats["binding_markers"]
        ),
        nets_applied=len(ir.nets),
        kicad_cli_used=cli is not None,
        heuristic_profile_name=active_heuristic_profile.name,
        label_mode_name=active_label_policy.mode_name,
        dry_run=request.dry_run,
        warnings=tuple(warnings),
        warning_report_path=warning_report_path,
        debug_dump_path=request.debug_dump_path,
        symbols_dirs_used=tuple(str(d) for d in symbol_index.directories),
        generated_schematic_diagnostics=diagnostics_capture.get("generated_schematic"),
    )


# ---------------------------------------------------------------------------
# Managed mutator factory
# ---------------------------------------------------------------------------


def _build_managed_mutator(  # noqa: PLR0913
    *,
    ir: CircuitIR,
    generation_ir: CircuitIR,
    project: ProjectRef,
    symbol_index: SymbolIndex,
    placed_symbol_specs: dict[str, _PlacedSymbolSpec],
    sheet_uuid: str,
    request: _ApplyNetlistRequest,
    active_heuristic_profile: SchematicHeuristicProfile,
    active_label_policy: LabelPolicy,
    stats: dict[str, int],
    warnings: list[dict[str, object]],
    managed_sch_path: Path,
    diagnostics_capture: dict[str, GeneratedSchematicDiagnostics],
    debug_capture: dict[str, object] | None = None,
) -> Callable[[SchematicDoc], None]:
    """Return a ``SchematicDoc`` mutator that reconstructs the managed sheet from *ir*.

    All mutable state (*stats*, *warnings*) is passed explicitly so the factory
    parameters remain easy to inspect and test in isolation.
    """

    def _mutate(doc: SchematicDoc) -> None:
        # Authoritative sync for managed sheet file: replace all generated
        # content by reconstructing from IR each run.
        root = parse(minimal_schematic_text())
        if not isinstance(root, ListNode):
            raise UserError("Managed schematic template parse failed", code=ErrorCode.PARSE_ERROR)
        doc.root = root
        doc.ensure_openclaw_marker()

        _engine = _resolve_layout(
            request.layout_name,
            cache_path=project.path / "openclaw_layout_cache.json",
            debug_dump_path=request.debug_dump_path,
            heuristic_profile=active_heuristic_profile,
            strict=request.strict,
        )
        (
            symbol_positions,
            pin_endpoints,
            pin_anchors,
            symbol_defs_missing,
            raw_layout,
        ) = _write_symbols(
            doc=doc,
            ir=generation_ir,
            symbol_index=symbol_index,
            placed_symbol_specs=placed_symbol_specs,
            project_name=project.name,
            stats=stats,
            engine=_engine,
            strict=request.strict,
        )
        warnings.extend(_layout_decoupling_distance_warnings(generation_ir, raw_layout))
        _write_unused_connector_no_connects(
            doc=doc,
            ir=generation_ir,
            symbol_index=symbol_index,
            pin_endpoints=pin_endpoints,
        )
        _tiers = assign_tiers(generation_ir, strict=request.strict)
        block_layout = classify_circuit(generation_ir)
        routing = route_nets(
            ir=generation_ir,
            pin_endpoints=pin_endpoints,
            pin_anchors=pin_anchors,
            block_layout=block_layout,
            tiers=_tiers,
            positions=raw_layout,
            policy=active_label_policy,
            use_bus=_resolve_routing(request.routing_name),
            heuristic_policy=active_heuristic_profile.routing_policy,
            strict=request.strict,
        )
        if debug_capture is not None:
            debug_capture.update(
                {
                    "net_classification": _build_net_classification_summary(
                        routing.route_decisions
                    ),
                    "final_route_choices": _serialize_route_decisions(routing.route_decisions),
                    "routing_heuristic_policy": _serialize_routing_heuristic_policy(
                        active_heuristic_profile.routing_policy
                    ),
                    "heuristic_profile_name": active_heuristic_profile.name,
                    "label_mode_name": active_label_policy.mode_name,
                }
            )
        write_routing(
            doc=doc,
            routing=routing,
            new_uuid=_new_uuid,
            stats=stats,
            # Power symbols come from the KiCad system library; pass None so
            # resolve_symbol_dirs() auto-discovers /usr/share/kicad/symbols.
            # The project's component symbols_dir is unrelated to power.kicad_sym.
            symbols_dir=None,
            project_name=project.name,
            strict=request.strict,
        )

        if symbol_defs_missing:
            warnings.append(
                {
                    "code": "SYMBOL_DEF_NOT_EMBEDDED",
                    "message": "Some symbol definitions could not be embedded.",
                    "details": {"symbols": sorted(symbol_defs_missing)},
                }
            )

        # Qualify all bare (path "/" …) entries so KiCad can resolve the
        # sub-sheet hierarchy and assign correct reference annotations.
        doc.update_managed_path(sheet_uuid)
        diagnostics_capture["generated_schematic"] = validate_generated_schematic(
            doc=doc,
            generation_ir=generation_ir,
            managed_sch_path=managed_sch_path,
            expected_wire_count=len(routing.wires),
        )
        _record_debug_stage(
            debug_capture,
            "post_generation_reparse",
            managed_schematic_path=str(managed_sch_path),
            hard_failure_count=len(diagnostics_capture["generated_schematic"].hard_failures),
            symbol_count=diagnostics_capture["generated_schematic"].symbol_count,
            wire_count=diagnostics_capture["generated_schematic"].wire_count,
        )

    return _mutate


def _layout_decoupling_distance_warnings(
    ir: CircuitIR,
    raw_layout: dict[str, tuple[float, float, float | None]],
) -> list[dict[str, object]]:
    component_nets: dict[str, set[str]] = {}
    for net in ir.nets:
        for pin_ref in net.pins:
            component_nets.setdefault(pin_ref.ref, set()).add(net.name)

    active_ics_by_rail: dict[str, list[str]] = {}
    for component in ir.components:
        if component_type(component.ref) != "ic":
            continue
        if component.ref not in raw_layout:
            continue

        nets = component_nets.get(component.ref, set())
        if not nets or not any(not is_power_net(net_name) for net_name in nets):
            continue

        for net_name in nets:
            if power_rail_polarity(net_name) is not None:
                active_ics_by_rail.setdefault(net_name, []).append(component.ref)

    warnings: list[dict[str, object]] = []
    for component in ir.components:
        if component_type(component.ref) != "passive" or not component.ref.upper().startswith("C"):
            continue
        if component.ref not in raw_layout:
            continue

        component_net_names = sorted(component_nets.get(component.ref, set()))
        if len(component_net_names) != 2:
            continue

        rail_net: str | None = None
        reference_net: str | None = None
        for net_name in component_net_names:
            if normalize_gnd_net_name(net_name) == "GND":
                reference_net = net_name
            elif power_rail_polarity(net_name) is not None:
                rail_net = net_name
        if rail_net is None or reference_net is None:
            continue

        candidate_refs = active_ics_by_rail.get(rail_net, [])
        if not candidate_refs:
            continue

        cap_x, cap_y, _ = raw_layout[component.ref]
        rail_polarity = power_rail_polarity(rail_net)
        candidate_refs = _prefer_decoupling_side_candidates(
            cap_y=cap_y,
            rail_polarity=rail_polarity,
            candidate_refs=candidate_refs,
            raw_layout=raw_layout,
        )
        nearest_ref = min(
            candidate_refs,
            key=lambda ref: math.dist((cap_x, cap_y), raw_layout[ref][:2]),
        )
        nearest_x, nearest_y, _ = raw_layout[nearest_ref]
        distance_mm = math.dist((cap_x, cap_y), (nearest_x, nearest_y))
        if distance_mm <= _LOCAL_DECOUPLING_DISTANCE_WARN_MM:
            continue

        warnings.append(
            {
                "code": "DECOUPLING_FAR_FROM_ACTIVE_DEVICE",
                "message": (
                    f"Decoupling capacitor {component.ref} between {rail_net} and {reference_net} "
                    f"is placed {distance_mm:.2f} mm from active device {nearest_ref}, which is "
                    "too far to read as local support circuitry."
                ),
                "details": {
                    "capacitor_ref": component.ref,
                    "rail_net": rail_net,
                    "rail_polarity": rail_polarity,
                    "reference_net": reference_net,
                    "nearest_active_ref": nearest_ref,
                    "distance_mm": round(distance_mm, 2),
                    "max_local_distance_mm": round(_LOCAL_DECOUPLING_DISTANCE_WARN_MM, 2),
                },
            }
        )

    return warnings


# ---------------------------------------------------------------------------
# Symbol placement helpers
# ---------------------------------------------------------------------------


def _transform_pin_at(
    pin_at: dict[str, tuple[float, float, float]],
    origin_x: float,
    origin_y: float,
    rotation: int,
) -> dict[str, tuple[float, float, float]]:
    """Apply *rotation* and origin translation to a library-space pin map.

    ``pin_at`` maps ``pin_num -> (px, py, pa)`` in library space.
    Returns ``{pin_num: (schematic_x, schematic_y, schematic_angle)}``.

    When *rotation* is 0 this reduces to a pure translation (no trig):
      ``schematic_{x,y} = origin_{x,y} + p_{x,y}``.

    For non-zero *rotation* θ (degrees), the standard 2-D rotation is applied:
      ``schematic_x = origin_x + cos(θ)·px − sin(θ)·py``
      ``schematic_y = origin_y + sin(θ)·px + cos(θ)·py``
      ``schematic_angle = (pa + θ) % 360``
    """
    if rotation == 0:
        return {
            pin_num: (origin_x + px, origin_y + py, pa) for pin_num, (px, py, pa) in pin_at.items()
        }
    theta = math.radians(rotation)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    return {
        pin_num: (
            origin_x + cos_t * px - sin_t * py,
            origin_y + sin_t * px + cos_t * py,
            (pa + rotation) % 360,
        )
        for pin_num, (px, py, pa) in pin_at.items()
    }


def _sorted_unit_keys(unit_pins: dict[str, list[str]]) -> list[str]:
    return sorted(unit_pins, key=lambda key: (int(key), key))


def _symbol_unit_pins(symbol: str, symbol_index: SymbolIndex) -> dict[str, list[str]]:
    return {unit: list(pins) for unit, pins in symbol_index.get_unit_pins(symbol).items()}


def _expand_generation_ir(
    ir: CircuitIR,
    symbol_index: SymbolIndex,
) -> tuple[CircuitIR, dict[str, _PlacedSymbolSpec]]:
    """Expand multi-unit devices into explicit placed refs for generation."""
    ref_to_used_pins: dict[str, set[str]] = {component.ref: set() for component in ir.components}
    for net in ir.nets:
        for pin_ref in net.pins:
            used_pins = ref_to_used_pins.get(pin_ref.ref)
            if used_pins is not None:
                used_pins.add(pin_ref.pin)

    multi_unit_pin_to_unit: dict[str, dict[str, str]] = {}
    placed_symbol_specs: dict[str, _PlacedSymbolSpec] = {}
    expanded_components = []
    ref_rewrite: dict[tuple[str, str], str] = {}

    for component in ir.components:
        all_symbol_pins = tuple(sorted(symbol_index.get_pins(component.symbol)))
        unit_pins = _symbol_unit_pins(component.symbol, symbol_index)
        if len(unit_pins) <= 1:
            expanded_components.append(component.model_copy(deep=True))
            placed_symbol_specs[component.ref] = _PlacedSymbolSpec(unit=1, pin_nums=all_symbol_pins)
            continue

        (
            component_copies,
            component_specs,
            pin_to_unit,
            component_ref_rewrite,
        ) = _expand_multi_unit_component(
            component=component,
            used_pins=ref_to_used_pins[component.ref],
            unit_pins=unit_pins,
            nets=ir.nets,
            fallback_pins=all_symbol_pins,
        )
        expanded_components.extend(component_copies)
        placed_symbol_specs.update(component_specs)
        if pin_to_unit:
            multi_unit_pin_to_unit[component.ref] = pin_to_unit
            ref_rewrite.update(component_ref_rewrite)

    expanded_nets = []
    for net in ir.nets:
        expanded_pins = []
        for pin_ref in net.pins:
            placed_pin_to_unit = multi_unit_pin_to_unit.get(pin_ref.ref)
            if placed_pin_to_unit is None:
                expanded_pins.append(pin_ref.model_copy(deep=True))
                continue
            unit = placed_pin_to_unit[pin_ref.pin]
            expanded_ref = ref_rewrite[(pin_ref.ref, unit)]
            expanded_pins.append(pin_ref.model_copy(update={"ref": expanded_ref}))
        expanded_nets.append(net.model_copy(update={"pins": expanded_pins}, deep=True))

    return ir.model_copy(
        update={"components": expanded_components, "nets": expanded_nets},
        deep=True,
    ), placed_symbol_specs


def _expand_multi_unit_component(
    *,
    component,
    used_pins: set[str],
    unit_pins: dict[str, list[str]],
    nets,
    fallback_pins: tuple[str, ...],
) -> tuple[list, dict[str, _PlacedSymbolSpec], dict[str, str], dict[tuple[str, str], str]]:
    pin_to_unit = _pin_to_unit_map(component.symbol, unit_pins)
    if not used_pins:
        return (
            [component.model_copy(deep=True)],
            {component.ref: _PlacedSymbolSpec(unit=1, pin_nums=fallback_pins)},
            {},
            {},
        )

    missing_pins = sorted(pin_num for pin_num in used_pins if pin_num not in pin_to_unit)
    if missing_pins:
        raise UserError(
            "Multi-unit symbol pins could not be assigned to KiCad units",
            code=ErrorCode.IR_SEMANTIC_INVALID,
            details={
                "ref": component.ref,
                "symbol": component.symbol,
                "missing_pins": missing_pins,
                "known_unit_pins": {unit: sorted(pins) for unit, pins in unit_pins.items()},
            },
        )

    used_units = _sorted_unit_keys(
        {unit: unit_pins[unit] for unit in {pin_to_unit[pin_num] for pin_num in used_pins}}
    )
    unit_nets = _component_unit_nets(component.ref, pin_to_unit, nets, used_units)
    suffix_by_unit = _suffix_by_unit(used_units, unit_nets)

    component_copies = []
    component_specs: dict[str, _PlacedSymbolSpec] = {}
    ref_rewrite: dict[tuple[str, str], str] = {}
    for unit in used_units:
        expanded_ref = f"{component.ref}{suffix_by_unit[unit]}"
        component_copies.append(component.model_copy(update={"ref": expanded_ref}, deep=True))
        component_specs[expanded_ref] = _PlacedSymbolSpec(
            unit=int(unit),
            pin_nums=tuple(sorted(unit_pins[unit])),
        )
        ref_rewrite[(component.ref, unit)] = expanded_ref
    return component_copies, component_specs, pin_to_unit, ref_rewrite


def _build_unit_splitting_debug(
    *,
    source_ir: CircuitIR,
    generation_ir: CircuitIR,
    placed_symbol_specs: dict[str, _PlacedSymbolSpec],
) -> dict[str, object]:
    """Summarize how logical device refs expanded into placed KiCad units."""
    source_refs = {component.ref for component in source_ir.components}
    grouped: dict[str, list[_UnitSplitDebugEntry]] = {}
    for component in generation_ir.components:
        placed_ref = component.ref
        source_ref = placed_ref
        if (
            placed_ref not in source_refs
            and placed_ref[:-1] in source_refs
            and placed_ref[-1].isalpha()
        ):
            source_ref = placed_ref[:-1]
        spec = placed_symbol_specs[placed_ref]
        grouped.setdefault(source_ref, []).append(
            {
                "placed_ref": placed_ref,
                "pin_nums": list(spec.pin_nums),
                "symbol": component.symbol,
                "unit": spec.unit,
            }
        )

    expanded_devices = []
    for source_ref in sorted(grouped):
        units = grouped[source_ref]
        if len(units) <= 1 and units[0]["placed_ref"] == source_ref:
            continue
        expanded_devices.append(
            {
                "source_ref": source_ref,
                "placed_refs": [entry["placed_ref"] for entry in units],
                "units": list(units),
            }
        )

    return {
        "original_component_count": len(source_ir.components),
        "placed_component_count": len(generation_ir.components),
        "expanded_device_count": len(expanded_devices),
        "expanded_devices": expanded_devices,
    }


def _build_net_classification_summary(decisions: list[RouteDecision]) -> list[dict[str, object]]:
    """Return the coarse per-net classification debug summary."""
    return [
        {
            "net_name": decision.net_name,
            "classification": decision.classification,
            "pin_count": decision.pin_count,
            "known_pin_count": decision.known_pin_count,
            "unknown_pin_count": decision.unknown_pin_count,
        }
        for decision in decisions
    ]


def _serialize_route_decisions(decisions: list[RouteDecision]) -> list[dict[str, object]]:
    """Return the final routing strategy selected for each net."""
    return [
        {
            "net_name": decision.net_name,
            "classification": decision.classification,
            "strategy": decision.strategy,
            "pin_count": decision.pin_count,
            "known_pin_count": decision.known_pin_count,
            "unknown_pin_count": decision.unknown_pin_count,
            "use_bus": decision.use_bus,
            "heuristic_override": decision.heuristic_override,
        }
        for decision in decisions
    ]


def _serialize_routing_heuristic_policy(policy: RoutingHeuristicPolicy) -> dict[str, bool]:
    """Return the active routing-heuristic toggles for debug dumps."""
    return {
        "enable_compact_output_tails": policy.enable_compact_output_tails,
        "enable_compact_local_ground_clusters": policy.enable_compact_local_ground_clusters,
    }


def _write_schematic_debug_dump(path: Path, payload: dict[str, object]) -> None:
    """Merge post-generation schematic debug details into the JSON sidecar."""
    merged: dict[str, object] = {}
    if path.exists():
        merged = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
    merged.update(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")


def _pin_to_unit_map(symbol: str, unit_pins: dict[str, list[str]]) -> dict[str, str]:
    pin_to_unit: dict[str, str] = {}
    for unit, pins in unit_pins.items():
        for pin_num in pins:
            existing_unit = pin_to_unit.get(pin_num)
            if existing_unit is not None and existing_unit != unit:
                raise UserError(
                    "Multi-unit symbol pin belongs to multiple KiCad units",
                    code=ErrorCode.IR_SEMANTIC_INVALID,
                    details={
                        "symbol": symbol,
                        "pin": pin_num,
                        "units": sorted({existing_unit, unit}),
                    },
                )
            pin_to_unit[pin_num] = unit
    return pin_to_unit


def _component_unit_nets(
    ref: str,
    pin_to_unit: dict[str, str],
    nets,
    used_units: list[str],
) -> dict[str, set[str]]:
    unit_nets: dict[str, set[str]] = {unit: set() for unit in used_units}
    for net in nets:
        for pin_ref in net.pins:
            if pin_ref.ref == ref:
                unit_nets[pin_to_unit[pin_ref.pin]].add(net.name)
    return unit_nets


def _suffix_by_unit(used_units: list[str], unit_nets: dict[str, set[str]]) -> dict[str, str]:
    power_units = {
        unit
        for unit in used_units
        if unit_nets[unit] and all(is_power_net(net_name) for net_name in unit_nets[unit])
    }
    suffix_by_unit: dict[str, str] = {}
    next_letter = ord("A")
    for unit in used_units:
        if len(power_units) == 1 and unit in power_units:
            suffix_by_unit[unit] = "P"
            continue
        suffix_by_unit[unit] = chr(next_letter)
        next_letter += 1
    return suffix_by_unit


def _write_symbols(  # noqa: PLR0913
    *,
    doc: SchematicDoc,
    ir: CircuitIR,
    symbol_index: SymbolIndex,
    placed_symbol_specs: dict[str, _PlacedSymbolSpec] | None = None,
    project_name: str,
    stats: dict[str, int],
    engine: LayoutEngine | None = None,
    cache_path: Path | None = None,
    strict: bool = False,
) -> tuple[
    dict[str, tuple[float, float]],
    dict[tuple[str, str], tuple[float, float, float]],
    dict[tuple[str, str], PinAnchor],
    set[str],
    dict[str, tuple[float, float, float | None]],
]:
    """Place all symbols from *ir* into *doc*.

        Returns a 5-tuple of:
    * ``symbol_positions``  — ``{ref: (x, y)}`` placed-symbol origins.
    * ``pin_endpoints``     — ``{(ref, pin_num): (x, y, angle)}`` actual
      pin connection-point coordinates in schematic space, derived from the
      library symbol's ``(pin ... (at x y angle) ...)`` data translated by
      the symbol placement position.  *angle* is the KiCad pin direction
      (0=right, 90=down, 180=left, 270=up) pointing **from the endpoint
      toward the symbol body** — wire stubs extend in the opposite direction.
        * ``pin_anchors``       — ``{(ref, pin_num): PinAnchor(...)}`` explicit
            placed-unit anchor ownership plus the same schematic-space endpoint
            geometry used by the router.
    * ``symbol_defs_missing`` — set of symbol ids whose library def was
      not found (embedded as best-effort empty stubs).
    * ``raw_layout``        — ``{ref: (x, y, rotation)}`` full layout positions
      (including rotation) as returned by the layout engine; used by the router
      for body-crossing avoidance.
    """
    symbol_positions: dict[str, tuple[float, float]] = {}
    # (ref, pin_number) -> (schematic_x, schematic_y, pin_angle)
    pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {}
    pin_anchors: dict[tuple[str, str], PinAnchor] = {}
    symbol_defs_missing: set[str] = set()

    if engine is None:
        engine = make_layout_engine(cache_path=cache_path, strict=strict)
    raw_layout = engine.compute_symbol_positions(ir)
    # Build plain (x, y) map for coordinate lookup and orientation computation.
    layout: dict[str, tuple[float, float]] = {
        ref: (pos[0], pos[1]) for ref, pos in raw_layout.items()
    }
    # Prefer engine-provided rotation (non-None) over computing it separately.
    # GraphvizLayoutEngine always returns a float rotation; NoneLayoutEngine returns
    # None, in which case we fall back to the standalone compute_orientations call.
    _need_fallback_orientations = any(pos[2] is None for pos in raw_layout.values())
    if _need_fallback_orientations:
        if strict:
            missing_rotation_refs = sorted(
                ref for ref, (_x, _y, rot) in raw_layout.items() if rot is None
            )
            raise UserError(
                "Layout engine returned missing symbol rotations in strict mode",
                code=ErrorCode.IR_SEMANTIC_INVALID,
                details={"refs_missing_rotation": missing_rotation_refs},
            )
        tiers: dict[str, int] = assign_tiers(ir, strict=strict)
        orientations: dict[str, int] = compute_orientations(
            ir,
            layout,
            tiers,
            placed_pin_numbers={
                ref: spec.pin_nums for ref, spec in (placed_symbol_specs or {}).items()
            }
            or None,
        )
    else:
        orientations = {ref: int(pos[2]) for ref, pos in raw_layout.items() if pos[2] is not None}

    for component in sorted(ir.components, key=lambda c: c.ref):
        x, y = layout[component.ref]
        placed_symbol = (placed_symbol_specs or {}).get(component.ref)
        if placed_symbol is None:
            placed_symbol = _PlacedSymbolSpec(
                unit=1,
                pin_nums=tuple(sorted(symbol_index.get_pins(component.symbol))),
            )
        valid_pins = list(placed_symbol.pin_nums)
        pin_uuids = [_new_uuid() for _ in valid_pins]

        if not _embed_symbol_if_found(doc=doc, symbol=component.symbol, symbol_index=symbol_index):
            symbol_defs_missing.add(component.symbol)

        doc.add_symbol(
            component.symbol,
            component.ref,
            component.value or component.ref,
            component.footprint or "",
            x,
            y,
            _new_uuid(),
            valid_pins,
            pin_uuids,
            project_name,
            unit=placed_symbol.unit,
            rotation=orientations.get(component.ref, 0),
        )
        symbol_positions[component.ref] = (x, y)

        # Compute pin endpoint positions in schematic space.
        # Pin (at px py angle) in library space is transformed by rotation θ
        # via _transform_pin_at; when θ=0 this is a pure translation.
        rotation = orientations.get(component.ref, 0)
        pin_at = _resolve_placed_symbol_pin_at(
            component.symbol,
            placed_symbol,
            symbol_index,
        )
        transformed = _transform_pin_at(pin_at, x, y, rotation)
        for pin_num, endpoint in transformed.items():
            pin_endpoints[(component.ref, pin_num)] = endpoint
            pin_anchors[(component.ref, pin_num)] = PinAnchor(
                ref=component.ref,
                pin=pin_num,
                x=endpoint[0],
                y=endpoint[1],
                angle=endpoint[2],
                unit=placed_symbol.unit,
            )

        stats["symbols"] += 1

    return symbol_positions, pin_endpoints, pin_anchors, symbol_defs_missing, raw_layout


def _resolve_placed_symbol_pin_at(
    symbol: str,
    placed_symbol: _PlacedSymbolSpec,
    symbol_index: SymbolIndex,
) -> dict[str, tuple[float, float, float]]:
    """Return unit-local pin geometry for a placed symbol when available."""
    unit_pin_at = symbol_index.get_unit_pin_at(symbol)
    if unit_pin_at:
        pin_at = unit_pin_at.get(str(placed_symbol.unit), {})
        if pin_at:
            return {
                pin_num: coords
                for pin_num, coords in pin_at.items()
                if pin_num in placed_symbol.pin_nums
            }

    lib_name, sym_name = symbol.split(":", 1)
    for directory in symbol_index.directories:
        from ..sch_doc import read_lib_symbol_pin_at  # noqa: PLC0415

        pin_at = read_lib_symbol_pin_at(lib_name, sym_name, symbols_dir=directory)
        if pin_at:
            return {
                pin_num: coords
                for pin_num, coords in pin_at.items()
                if pin_num in placed_symbol.pin_nums
            }
    return {}


def _embed_symbol_if_found(*, doc: SchematicDoc, symbol: str, symbol_index: SymbolIndex) -> bool:
    lib_name, sym_name = symbol.split(":", 1)
    for directory in symbol_index.directories:
        sym_def = read_lib_symbol_def_flat(lib_name, sym_name, symbols_dir=directory)
        if sym_def is not None:
            doc.embed_lib_symbol(sym_def)
            return True
    return False


def _write_unused_connector_no_connects(
    *,
    doc: SchematicDoc,
    ir: CircuitIR,
    symbol_index: SymbolIndex,
    pin_endpoints: dict[tuple[str, str], tuple[float, float, float]],
) -> None:
    """Place KiCad no-connect markers on unused connector pins."""
    used_pins_by_ref: dict[str, set[str]] = {}
    for net in ir.nets:
        for pin_ref in net.pins:
            used_pins_by_ref.setdefault(pin_ref.ref, set()).add(pin_ref.pin)

    for component in ir.components:
        if component_type(component.ref) != "connector":
            continue

        all_pins = sorted(symbol_index.get_pins(component.symbol))
        used_pins = used_pins_by_ref.get(component.ref, set())
        for pin_num in all_pins:
            if pin_num in used_pins:
                continue
            endpoint = pin_endpoints.get((component.ref, pin_num))
            if endpoint is None:
                continue
            doc.add_no_connect(endpoint[0], endpoint[1], _new_uuid())


# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------


def _resolve_mode(mode_name: str | None, *, default: ValidationMode) -> ValidationMode:
    """Map a mode/validate flag string to a :class:`~kicad_pcb.pipeline.ValidationMode`.

    Accepted values
    ---------------
    ``none``                 → :attr:`~ValidationMode.NONE`
    ``syntax``               → :attr:`~ValidationMode.SYNTAX`
    ``lint`` / ``internal``  → :attr:`~ValidationMode.LINT`  (``internal`` is legacy)
    ``kicad``                → :attr:`~ValidationMode.KICAD`
    ``full``                 → :attr:`~ValidationMode.FULL`
    """
    if mode_name is None:
        return default
    raw = mode_name.strip().lower()
    if raw == "none":
        return ValidationMode.NONE
    if raw == "syntax":
        return ValidationMode.SYNTAX
    if raw in {"internal", "syntax_lint", "lint"}:
        return ValidationMode.LINT
    if raw == "kicad":
        return ValidationMode.KICAD
    if raw == "full":
        return ValidationMode.FULL
    raise UserError(
        f"Unknown mode '{mode_name}'",
        code=ErrorCode.USER_ERROR,
        details={"allowed": ["none", "syntax", "lint", "kicad", "full"]},
    )


def _resolve_layout(
    layout_name: str | None,
    *,
    cache_path: Path | None = None,
    debug_dump_path: Path | None = None,
    heuristic_profile: SchematicHeuristicProfile = DEFAULT_SCHEMATIC_HEURISTIC_PROFILE,
    strict: bool = False,
) -> LayoutEngine:
    """Return the layout engine requested by *layout_name*.

    Accepted values
    ---------------
    ``None`` / ``graphviz`` — :func:`make_layout_engine`; raises if ``dot`` is absent.
    """
    name = (layout_name or "graphviz").strip().lower()
    if name == "graphviz":
        return make_layout_engine(
            cache_path=cache_path,
            debug_dump_path=debug_dump_path,
            heuristic_profile_name=heuristic_profile.name,
            layout_heuristic_policy=heuristic_profile.layout_policy,
            strict=strict,
        )
    raise UserError(
        f"Unknown layout engine '{layout_name}'",
        code=ErrorCode.USER_ERROR,
        details={"allowed": ["graphviz"]},
    )


def _resolve_heuristic_profile(
    profile_name: str | None,
    *,
    default: SchematicHeuristicProfile = DEFAULT_SCHEMATIC_HEURISTIC_PROFILE,
) -> SchematicHeuristicProfile:
    """Return the bundled heuristic profile requested by *profile_name*."""
    if profile_name is None:
        return default
    name = profile_name.strip().lower()
    try:
        return SCHEMATIC_HEURISTIC_PROFILES[name]
    except KeyError as exc:
        raise UserError(
            f"Unknown heuristic profile '{profile_name}'",
            code=ErrorCode.USER_ERROR,
            details={"allowed": sorted(SCHEMATIC_HEURISTIC_PROFILES)},
        ) from exc


def _resolve_label_policy(
    label_mode_name: str | None,
    *,
    default: LabelPolicy = DEFAULT_LABEL_POLICY,
) -> LabelPolicy:
    """Return the bundled label policy requested by *label_mode_name*."""

    if label_mode_name is None:
        return default
    name = label_mode_name.strip().lower()
    if name not in LABEL_MODE_POLICIES:
        raise UserError(
            f"Unknown label mode '{label_mode_name}'",
            code=ErrorCode.USER_ERROR,
            details={"allowed": sorted(LABEL_MODE_POLICIES)},
        )
    return LABEL_MODE_POLICIES[cast(LabelModeName, name)]


def _resolve_routing(routing_name: str | None) -> bool:
    """Return ``use_bus`` bool from *routing_name*.

    Accepted values
    ---------------
    ``bus`` / ``hub`` / ``None``  — spine/hub routing (``use_bus=True``, default).
    ``labels``                    — label-stub routing (``use_bus=False``).
    """
    name = (routing_name or "bus").strip().lower()
    if name in {"bus", "hub"}:
        return True
    if name == "labels":
        return False
    raise UserError(
        f"Unknown routing style '{routing_name}'",
        code=ErrorCode.USER_ERROR,
        details={"allowed": ["bus", "hub", "labels"]},
    )


# ---------------------------------------------------------------------------
# Managed-sheet lifecycle helpers
# ---------------------------------------------------------------------------


def _ensure_managed_file_exists(path: Path, *, dry_run: bool) -> None:
    if path.exists():
        return
    if dry_run:
        raise UserError(
            "Managed schematic file does not exist for dry-run",
            code=ErrorCode.IO_ERROR,
            details={
                "path": str(path),
                "hint": "Run once without --dry-run to initialise OpenClaw managed sheet",
            },
        )
    _atomic_write(
        path,
        minimal_schematic_text(),
        root="kicad_sch",
        operation="create-managed-sheet",
    )


def _ensure_project_root_owned(project: ProjectRef, *, force: bool, dry_run: bool) -> str:
    """Prepare the root schematic for managed-sheet use; return the managed sheet UUID."""
    _captured_uuid: list[str] = []

    def _mutate(doc: SchematicDoc) -> None:
        if not doc.has_openclaw_marker() and not force:
            raise UserError(
                "Schematic is not OpenClaw-managed. Use --force to adopt.",
                code=ErrorCode.NOT_OWNED,
                details={"path": str(project.sch_file)},
            )
        doc.ensure_openclaw_marker()
        uuid = doc.ensure_managed_sheet(
            sheet_name=MANAGED_SHEET_NAME,
            sheet_file=MANAGED_SHEET_FILE,
            sheet_uuid=_new_uuid(),
        )
        _captured_uuid.append(uuid)

    mutate_and_validate_sch(
        project.sch_file,
        _mutate,
        mode=ValidationMode.LINT,
        operation="prepare-managed-sheet",
        dry_run=dry_run,
    )
    # _mutate is always called exactly once by mutate_and_validate_sch
    # (dry_run skips the write but still calls the mutator for validation).
    return _captured_uuid[0] if _captured_uuid else _new_uuid()

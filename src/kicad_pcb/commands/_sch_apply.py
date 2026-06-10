"""Schematic generation engine: symbol placement, pin transforms, managed-sheet lifecycle."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from .. import placeholder_symbol as _placeholder_mod
from ..adapters import KicadCliAdapter
from ..block_detection import classify_circuit
from ..circuit_ir import CircuitIR
from ..errors import ErrorCode, ToolError, UserError
from ..fs import _new_uuid
from ..ir.validate import IrSymbolValidationResult, validate_circuit_ir, validate_ir_symbols
from ..models import ProjectRef
from ..pipeline import ValidationMode, mutate_and_validate_sch
from ..results import ApplyNetlistResult, GeneratedSchematicDiagnostics
from ..router import (
    LabelPolicy,
    route_nets,
    write_routing,
)
from ..runner import find_kicad_cli
from ..sch_doc import SchematicDoc
from ..sexpr.nodes import ListNode
from ..sexpr.parser import parse
from ..symbol_index import SymbolIndex
from ..tier import assign_tiers
from ._project import minimal_schematic_text
from ._sch_apply_artifacts import (
    _record_debug_stage,
    _write_warning_report,
    resolve_schematic_paths,  # noqa: F401
    validate_generated_schematic,
)
from ._sch_apply_embed import (
    _embed_symbol_if_found,  # noqa: F401
    _resolve_placed_symbol_pin_at,  # noqa: F401
    _write_unused_connector_no_connects,
)
from ._sch_apply_expand import (
    _build_unit_splitting_debug,
    _expand_generation_ir,  # noqa: F401
)
from ._sch_apply_resolve import (
    _ensure_project_root_owned,
    _resolve_heuristic_profile,  # noqa: F401
    _resolve_label_policy,  # noqa: F401
    _resolve_layout,
    _resolve_mode,  # noqa: F401
    _resolve_routing,  # noqa: F401
)
from ._sch_apply_types import (
    ANALOG_AUDIO_HEURISTIC_PROFILE,  # noqa: F401
    DEFAULT_SCHEMATIC_HEURISTIC_PROFILE,  # noqa: F401
    DENSE_DEBUG_HEURISTIC_PROFILE,  # noqa: F401
    GENERIC_DIGITAL_HEURISTIC_PROFILE,  # noqa: F401
    MANAGED_SHEET_FILE,  # noqa: F401
    MANAGED_SHEET_NAME,  # noqa: F401
    POWER_SUPPLY_HEURISTIC_PROFILE,  # noqa: F401
    SCHEMATIC_HEURISTIC_PROFILES,  # noqa: F401
    SchematicHeuristicProfile,  # noqa: F401
    _ApplyNetlistRequest,  # noqa: F401
    _PlacedSymbolSpec,  # noqa: F401
)
from ._sch_apply_write import (
    _build_net_classification_summary,
    _layout_decoupling_distance_warnings,
    _serialize_route_decisions,
    _serialize_routing_heuristic_policy,
    _transform_pin_at,  # noqa: F401
    _write_schematic_debug_dump,
    _write_symbols,  # noqa: F401
)
from ._validate import advisory_warnings, raise_for_blocking_advisories

__all__ = [
    "MANAGED_SHEET_NAME",
    "MANAGED_SHEET_FILE",
    "SCHEMATIC_HEURISTIC_PROFILES",
    "ANALOG_AUDIO_HEURISTIC_PROFILE",
    "GENERIC_DIGITAL_HEURISTIC_PROFILE",
    "POWER_SUPPLY_HEURISTIC_PROFILE",
    "DENSE_DEBUG_HEURISTIC_PROFILE",
    "DEFAULT_SCHEMATIC_HEURISTIC_PROFILE",
    "SchematicHeuristicProfile",
    "_ApplyNetlistRequest",
    "_PlacedSymbolSpec",
    "_apply_netlist_to_project",
    "_build_managed_mutator",
    "_expand_generation_ir",
    "_write_symbols",
    "_transform_pin_at",
    "_resolve_placed_symbol_pin_at",
    "_embed_symbol_if_found",
    "_resolve_mode",
    "_resolve_layout",
    "_resolve_heuristic_profile",
    "_resolve_label_policy",
    "_resolve_routing",
    "resolve_schematic_paths",
]


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
    _sym_result: IrSymbolValidationResult = validate_ir_symbols(ir, symbol_index)
    # Build placeholder symbols for unknown library refs and register them so
    # every downstream call to get_pins / get_unit_pins / get_unit_pin_at works
    # transparently without any further special-casing.
    _placeholders: dict[str, _placeholder_mod.PlaceholderSymbol] = {}
    for _sym_id, _ir_pins in _sym_result.unknown_symbols.items():
        _ph = _placeholder_mod.build(_sym_id, _ir_pins)
        _placeholders[_sym_id] = _ph
        symbol_index.register_placeholder(_sym_id, _ir_pins, _ph.pin_at)
        warnings.append(
            {
                "code": "SYMBOL_PLACEHOLDER_USED",
                "severity": "warning",
                "message": (
                    f"Symbol '{_sym_id}' not found in libraries — "
                    f"a generic placeholder was used. Replace with the real "
                    f"symbol before fabricating."
                ),
                "details": {
                    "symbol": _sym_id,
                    "pins": sorted(_ir_pins),
                },
            }
        )
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

    _ensure_project_root_owned(project, force=request.force, dry_run=request.dry_run)

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
            schematic_path=str(project.sch_file),
            validation_mode=mode.name,
            dry_run=request.dry_run,
        )
        mutate_and_validate_sch(
            project.sch_file,
            _build_managed_mutator(
                ir=ir,
                generation_ir=generation_ir,
                project=project,
                symbol_index=symbol_index,
                placed_symbol_specs=placed_symbol_specs,
                placeholders=_placeholders,
                request=request,
                active_heuristic_profile=active_heuristic_profile,
                active_label_policy=active_label_policy,
                stats=stats,
                warnings=warnings,
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
    except Exception:
        raise

    if request.dry_run:
        warnings.append(
            {
                "code": "DRY_RUN_NO_WRITE",
                "message": ("Dry-run mode: validation passed but no changes were written to disk."),
                "details": {
                    "schematic_path": str(project.sch_file),
                    "symbols_validated": stats["symbols"],
                    "nets_validated": len(ir.nets),
                },
            }
        )
        _record_debug_stage(
            debug_capture,
            "artifact_finalize",
            status="dry_run",
            schematic_path=str(project.sch_file),
        )

    warning_report_path: Path | None = None
    if not request.dry_run:
        warning_report_path = _write_warning_report(
            project=project,
            request=request,
            warnings=warnings,
            schematic_path=project.sch_file,
            stats=stats,
            kicad_cli_used=cli is not None,
            symbols_dirs=tuple(str(d) for d in symbol_index.directories),
            generated_schematic_diagnostics=diagnostics_capture.get("generated_schematic"),
        )
        _record_debug_stage(
            debug_capture,
            "artifact_finalize",
            status="written",
            schematic_path=str(project.sch_file),
            warning_report_path=str(warning_report_path),
        )

    if request.debug_dump_path is not None:
        _write_schematic_debug_dump(request.debug_dump_path, debug_capture)

    return ApplyNetlistResult(
        schematic_path=project.sch_file,
        managed_schematic_path=project.sch_file,
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


def _build_managed_mutator(  # noqa: PLR0913
    *,
    ir: CircuitIR,
    generation_ir: CircuitIR,
    project: ProjectRef,
    symbol_index: SymbolIndex,
    placed_symbol_specs: dict[str, _PlacedSymbolSpec],
    placeholders: dict[str, _placeholder_mod.PlaceholderSymbol],
    request: _ApplyNetlistRequest,
    active_heuristic_profile: SchematicHeuristicProfile,
    active_label_policy: LabelPolicy,
    stats: dict[str, int],
    warnings: list[dict[str, object]],
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
            cache_path=project.path / "layout_cache.json",
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
            placeholders=placeholders,
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

        diagnostics_capture["generated_schematic"] = validate_generated_schematic(
            doc=doc,
            generation_ir=generation_ir,
            schematic_path=project.sch_file,
            expected_wire_count=len(routing.wires),
        )
        _record_debug_stage(
            debug_capture,
            "post_generation_reparse",
            schematic_path=str(project.sch_file),
            hard_failure_count=len(diagnostics_capture["generated_schematic"].hard_failures),
            symbol_count=diagnostics_capture["generated_schematic"].symbol_count,
            wire_count=diagnostics_capture["generated_schematic"].wire_count,
        )

    return _mutate

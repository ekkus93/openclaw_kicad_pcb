"""Circuit IR validation and generation services."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.commands._project import create_project_files
from kicad_pcb.commands._sch_apply import _apply_netlist_to_project, _ApplyNetlistRequest
from kicad_pcb.commands._validate import (
    advisory_warnings,
    full_validate,
    raise_for_blocking_advisories,
)
from kicad_pcb.errors import ErrorCode, KiCadError, UserError
from kicad_pcb.ir.autofix import autofix_circuit_ir
from kicad_pcb.symbol_index import SymbolIndex

from ..errors import kicad_error_to_payload, unexpected_error_to_payload
from ..schemas import CreateJobFromNetlistRequest, JobDetail, ValidateNetlistResponse
from ..settings import WebSettings
from .artifacts import create_project_zip, list_artifacts
from .jobs import JobRecord, create_job_workspace, update_job_status, write_job

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedNetlist:
    """Validated raw netlist payload plus deterministic repair metadata."""

    netlist_json: dict[str, Any]
    component_count: int
    net_count: int
    warnings: list[dict[str, Any]]
    symbols_dirs_used: list[str]
    fixes_applied: list[str]
    auto_fixed: bool


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Serialize JSON payloads deterministically."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _parse_symbols_dir(symbols_dir: str | None) -> Path | None:
    """Convert an optional client-supplied symbols dir string to a path."""

    if symbols_dir is None or not symbols_dir.strip():
        return None
    return Path(symbols_dir).expanduser().resolve()


def _job_relative_path(job: JobRecord, path: Path | None) -> str | None:
    """Return a path string relative to the job workspace when possible."""

    if path is None:
        return None
    try:
        return str(path.resolve().relative_to(job.work_dir.resolve()))
    except ValueError:
        return str(path)


def _validate_path(
    *,
    netlist_path: Path,
    symbols_dir: Path | None,
) -> tuple[SymbolIndex, Any]:
    """Run the direct validation stack and return the symbol index plus IR."""

    symbol_index = SymbolIndex(symbols_dir=symbols_dir)
    ir = full_validate(netlist_path, symbol_index)
    raise_for_blocking_advisories(ir, symbol_index)
    return symbol_index, ir


def _validate_with_optional_autofix(
    *,
    netlist_path: Path,
    raw_netlist_json: dict[str, Any],
    symbols_dir: Path | None,
    auto_fix: bool,
) -> tuple[Path, SymbolIndex, Any, list[str]]:
    """Validate a netlist file, optionally repairing deterministic issues first."""

    try:
        symbol_index, ir = _validate_path(netlist_path=netlist_path, symbols_dir=symbols_dir)
        return netlist_path, symbol_index, ir, []
    except UserError as first_error:
        if not auto_fix:
            raise

        symbol_index = SymbolIndex(symbols_dir=symbols_dir)
        outcome = autofix_circuit_ir(raw_netlist_json, symbol_index=symbol_index)
        if not outcome.fixes_applied:
            raise first_error

        fixed_path = netlist_path.with_name(f"{netlist_path.stem}.autofix.json")
        _write_json(fixed_path, outcome.ir_dict)
        try:
            fixed_symbol_index, fixed_ir = _validate_path(
                netlist_path=fixed_path,
                symbols_dir=symbols_dir,
            )
        except UserError as retry_error:
            fixes_summary = (
                "\n".join(f"  - {entry}" for entry in outcome.fixes_applied) or "  - none"
            )
            raise UserError(
                "Auto-fix applied changes but validation still failed.\n"
                f"{fixes_summary}\n"
                f"Remaining error: {retry_error}",
                code=ErrorCode.IR_SCHEMA_INVALID,
            ) from retry_error
        return fixed_path, fixed_symbol_index, fixed_ir, list(outcome.fixes_applied)


def prepare_netlist_dict(
    *,
    netlist_json: dict[str, Any],
    symbols_dir: Path | None,
    auto_fix: bool,
) -> PreparedNetlist:
    """Validate and optionally repair a raw Circuit IR payload."""

    with tempfile.TemporaryDirectory(prefix="kicad-pcb-web-prepare-") as temp_dir:
        netlist_path = Path(temp_dir) / "circuit_ir.json"
        _write_json(netlist_path, netlist_json)
        effective_path, symbol_index, ir, fixes_applied = _validate_with_optional_autofix(
            netlist_path=netlist_path,
            raw_netlist_json=netlist_json,
            symbols_dir=symbols_dir,
            auto_fix=auto_fix,
        )
        prepared_json = json.loads(effective_path.read_text(encoding="utf-8"))
        warnings = advisory_warnings(ir, symbol_index)

    return PreparedNetlist(
        netlist_json=prepared_json,
        component_count=len(ir.components),
        net_count=len(ir.nets),
        warnings=warnings,
        symbols_dirs_used=[str(path) for path in symbol_index.directories],
        fixes_applied=fixes_applied,
        auto_fixed=bool(fixes_applied),
    )


def validate_netlist_dict(
    *,
    netlist_json: dict[str, Any],
    symbols_dir: Path | None,
) -> ValidateNetlistResponse:
    """Validate a raw Circuit IR payload without creating a project."""

    prepared = prepare_netlist_dict(
        netlist_json=netlist_json,
        symbols_dir=symbols_dir,
        auto_fix=False,
    )

    return ValidateNetlistResponse(
        valid=True,
        component_count=prepared.component_count,
        net_count=prepared.net_count,
        warnings=prepared.warnings,
        symbols_dirs_used=prepared.symbols_dirs_used,
    )


def _generate_schematic_preview(
    schematic_path: Path,
    artifacts_dir: Path,
) -> Path | None:
    """Export a PNG preview of the schematic into *artifacts_dir*.

    Returns the PNG path on success, ``None`` if any step fails.
    This is always best-effort — failures are logged but never raise.
    """
    kicad_cli_bin = shutil.which("kicad-cli")
    rsvg_bin = shutil.which("rsvg-convert")
    if not kicad_cli_bin or not rsvg_bin:
        return None
    if not schematic_path.is_file():
        return None

    png_path = artifacts_dir / "schematic_preview.png"

    try:
        return _run_preview(kicad_cli_bin, rsvg_bin, schematic_path, artifacts_dir, png_path)
    except Exception:
        LOGGER.debug("Schematic preview generation failed", exc_info=True)
        return None


def _run_preview(
    kicad_cli_bin: str,
    rsvg_bin: str,
    schematic_path: Path,
    artifacts_dir: Path,
    png_path: Path,
) -> Path | None:
    # kicad-cli sch export svg --output takes a *directory*; it writes a
    # file named after the schematic inside that directory.  Use a temp
    # sub-directory so we can reliably find the produced SVG.
    svg_dir = artifacts_dir / "_svg_tmp"
    svg_dir.mkdir(exist_ok=True)

    cli = KicadCliAdapter(kicad_cli=kicad_cli_bin)
    result = cli.export_svg_sch(schematic_path, svg_dir)
    if result.returncode != 0:
        return None

    svgs = list(svg_dir.glob("*.svg"))
    if not svgs:
        return None

    conv = subprocess.run(
        [rsvg_bin, "--output", str(png_path), str(svgs[0])],
        capture_output=True,
        timeout=30,
        check=False,
    )
    shutil.rmtree(svg_dir, ignore_errors=True)
    return png_path if conv.returncode == 0 and png_path.exists() else None


def generate_project_from_netlist_job(
    *,
    settings: WebSettings,
    request: CreateJobFromNetlistRequest,
) -> JobDetail:
    """Generate a project inside a job workspace and return the final job detail."""

    record = create_job_workspace(settings, request.project_name, request.model_dump(mode="json"))
    _write_json(record.input_path, request.netlist_json)
    record = update_job_status(record, status="running")

    symbols_dir = _parse_symbols_dir(request.symbols_dir)

    try:
        effective_netlist_path, symbol_index, ir, fixes_applied = _validate_with_optional_autofix(
            netlist_path=record.input_path,
            raw_netlist_json=request.netlist_json,
            symbols_dir=symbols_dir,
            auto_fix=request.auto_fix,
        )

        project = create_project_files(
            name=record.project_name,
            out_dir=record.work_dir / "project",
            description="Generated by KiCad PCB Web App",
        )
        apply_result = _apply_netlist_to_project(
            project,
            _ApplyNetlistRequest(
                netlist_path=effective_netlist_path,
                symbols_dir=symbols_dir,
                mode_name=request.validation,
                force=True,
                dry_run=False,
                backup=False,
                strict=request.strict,
                layout_name=request.layout,
                routing_name=request.routing,
                label_mode_name=request.label_mode,
                heuristic_profile_name=request.heuristic_profile,
                debug_dump_path=record.artifacts_dir / "debug.json",
            ),
        )

        if (
            apply_result.warning_report_path is not None
            and apply_result.warning_report_path.is_file()
        ):
            shutil.copy2(apply_result.warning_report_path, record.artifacts_dir / "warnings.json")

        preview_path = _generate_schematic_preview(
            apply_result.schematic_path,
            record.artifacts_dir,
        )
        if preview_path is None:
            LOGGER.warning(
                "Schematic preview generation failed for job %s — "
                "kicad-cli or rsvg-convert may be unavailable or the export failed. "
                "Check that kicad-cli >= 9 and rsvg-convert are installed.",
                record.id,
            )
        project_zip_path = create_project_zip(project.path, record.artifacts_dir)
        warnings_artifact_path = record.artifacts_dir / "warnings.json"
        result_payload: dict[str, Any] = {
            "project_dir": _job_relative_path(record, project.path),
            "input_path": _job_relative_path(record, effective_netlist_path),
            "project_zip": _job_relative_path(record, project_zip_path),
            "schematic_path": _job_relative_path(record, apply_result.schematic_path),
            "managed_schematic_path": _job_relative_path(
                record,
                apply_result.managed_schematic_path,
            ),
            "warning_report_path": (
                _job_relative_path(record, warnings_artifact_path)
                if warnings_artifact_path.is_file()
                else None
            ),
            "debug_dump_path": _job_relative_path(record, apply_result.debug_dump_path),
            "symbols_added": apply_result.symbols_added,
            "nets_applied": apply_result.nets_applied,
            "component_count": len(ir.components),
            "net_count": len(ir.nets),
            "kicad_cli_used": apply_result.kicad_cli_used,
            "heuristic_profile_name": apply_result.heuristic_profile_name,
            "label_mode_name": apply_result.label_mode_name,
            "symbols_dirs_used": [str(path) for path in symbol_index.directories],
            "warnings": list(apply_result.warnings),
            "generated_schematic_diagnostics": (
                apply_result.generated_schematic_diagnostics.as_dict()
                if apply_result.generated_schematic_diagnostics is not None
                else None
            ),
            "fixes_applied": fixes_applied,
            "auto_fixed": bool(fixes_applied),
        }
        record = update_job_status(record, status="succeeded", result=result_payload, error=None)
        return record.to_detail(artifacts=list_artifacts(record.work_dir))
    except KiCadError as exc:
        error_payload = cast(dict[str, Any], kicad_error_to_payload(exc)["error"])
        record = update_job_status(record, status="failed", error=error_payload, result=None)
        return record.to_detail(artifacts=list_artifacts(record.work_dir))
    except Exception:
        LOGGER.exception("Unexpected web job failure for %s", record.id)
        error_payload = cast(dict[str, Any], unexpected_error_to_payload()["error"])
        record = update_job_status(record, status="failed", error=error_payload, result=None)
        write_job(record)
        return record.to_detail(artifacts=list_artifacts(record.work_dir))

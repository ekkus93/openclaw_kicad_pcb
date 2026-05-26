"""Typed result objects returned by command handlers.

Phase 2.4: command functions return structured results; the CLI layer
(formatting.py / cli.py) is responsible for formatting and printing them.
No ``print()`` calls belong in command modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .lint import LintIssue as _LintFinding
    from .models import FootprintMoveSpec, ProjectRef, SessionRef, ValidationResult


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NewSessionResult:
    """Result of the ``new-session`` command."""

    name: str
    uuid: str
    path: Path
    created: str
    description: str = ""


@dataclass(frozen=True)
class SessionInfoResult:
    """Result of the ``session-info`` command."""

    session: SessionRef
    project_count: int = 0
    netlist_files: tuple[str, ...] = field(default_factory=tuple)
    zip_files: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# project
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NewProjectResult:
    """Result of the ``new`` command."""

    name: str
    path: Path
    description: str
    files: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class InfoResult:
    """Result of the ``info`` command."""

    project: ProjectRef
    files: tuple[tuple[str, int], ...] = field(default_factory=tuple)  # (name, bytes)


@dataclass(frozen=True)
class OpenResult:
    """Result of the ``open`` command."""

    name: str
    path: Path


@dataclass(frozen=True)
class ModelCorpusFixtureResult:
    """One fixture summary row for model-corpus CLI output."""

    fixture_id: str
    status: str
    source_file_name: str
    symbol_count: int
    wire_count: int
    label_count: int
    has_circuit_ir: bool


@dataclass(frozen=True)
class ModelCorpusIngestResult:
    """Result of the ``model-corpus ingest`` command."""

    source_dir: Path
    out_dir: Path
    accepted_count: int
    partial_count: int
    rejected_count: int
    fixture_count: int
    report_path: Path
    summary_path: Path
    fixtures: tuple[ModelCorpusFixtureResult, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ModelCorpusListResult:
    """Result of the ``model-corpus list`` command."""

    corpus_dir: Path
    fixtures: tuple[ModelCorpusFixtureResult, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ModelCorpusEvaluateResult:
    """Result of the ``model-corpus evaluate`` command."""

    corpus_dir: Path
    out_dir: Path
    fixture_count: int
    evaluated_count: int
    skipped_count: int
    failed_count: int
    summary_json_path: Path
    summary_md_path: Path


@dataclass(frozen=True)
class InfoSchResult:
    """Result of the ``info-sch`` command."""

    project_path: Path
    schematic_path: Path
    owned_by_openclaw: bool
    symbols: tuple[dict[str, object], ...] = field(default_factory=tuple)
    pin_net_bindings: tuple[dict[str, str], ...] = field(default_factory=tuple)
    warnings: tuple[dict[str, object], ...] = field(default_factory=tuple)
    managed_schematic_path: Path | None = None
    symbol_count: int = 0
    label_count: int = 0
    managed_symbol_count: int = 0
    managed_label_count: int = 0


@dataclass(frozen=True)
class GeneratedSchematicDiagnostics:
    """Structured post-generation validation summary for a managed schematic."""

    symbol_count: int
    wire_count: int
    label_count: int
    global_label_count: int = 0
    junction_count: int = 0
    binding_marker_count: int = 0
    unresolved_refs: tuple[str, ...] = field(default_factory=tuple)
    missing_bindings: tuple[str, ...] = field(default_factory=tuple)
    unexpected_bindings: tuple[str, ...] = field(default_factory=tuple)
    duplicate_bindings: tuple[str, ...] = field(default_factory=tuple)
    hard_failures: tuple[dict[str, object], ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        """Return a stable JSON-serializable summary."""

        return {
            "symbol_count": self.symbol_count,
            "wire_count": self.wire_count,
            "label_count": self.label_count,
            "global_label_count": self.global_label_count,
            "junction_count": self.junction_count,
            "binding_marker_count": self.binding_marker_count,
            "unresolved_refs": list(self.unresolved_refs),
            "missing_bindings": list(self.missing_bindings),
            "unexpected_bindings": list(self.unexpected_bindings),
            "duplicate_bindings": list(self.duplicate_bindings),
            "hard_failures": list(self.hard_failures),
        }


@dataclass(frozen=True)
class ApplyNetlistResult:
    """Result of the ``apply-netlist`` command."""

    schematic_path: Path
    managed_schematic_path: Path
    symbols_added: int
    symbols_updated: int
    managed_items_written: int
    nets_applied: int
    kicad_cli_used: bool
    heuristic_profile_name: str
    label_mode_name: str
    dry_run: bool = False
    warnings: tuple[dict[str, object], ...] = field(default_factory=tuple)
    warning_report_path: Path | None = None
    debug_dump_path: Path | None = None
    symbols_dirs_used: tuple[str, ...] = field(default_factory=tuple)
    generated_schematic_diagnostics: GeneratedSchematicDiagnostics | None = None


@dataclass(frozen=True)
class NewFromNetlistResult:
    """Result of the ``new-from-netlist`` command."""

    name: str
    path: Path
    schematic_path: Path
    managed_schematic_path: Path
    symbols_added: int
    nets_applied: int
    kicad_cli_used: bool
    heuristic_profile_name: str
    label_mode_name: str
    warnings: tuple[dict[str, object], ...] = field(default_factory=tuple)
    warning_report_path: Path | None = None
    debug_dump_path: Path | None = None
    symbols_dirs_used: tuple[str, ...] = field(default_factory=tuple)
    generated_schematic_diagnostics: GeneratedSchematicDiagnostics | None = None
    zip_path: Path | None = None
    session_path: Path | None = None


@dataclass(frozen=True)
class ValidateNetlistResult:
    """Result of the ``validate-netlist`` command."""

    valid: bool
    netlist_path: Path
    component_count: int
    net_count: int
    warnings: tuple[dict[str, object], ...] = field(default_factory=tuple)
    symbols_dirs_used: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class FixNetlistResult:
    """Result of the ``fix-netlist`` command."""

    #: ``True`` when all validation errors were resolved by auto-fix.
    fixed: bool
    #: Path to the fixed JSON file that was written.
    output_path: Path
    #: Human-readable descriptions of every change applied.
    fixes_applied: tuple[str, ...] = field(default_factory=tuple)
    #: Errors that could not be resolved deterministically.
    remaining_errors: tuple[str, ...] = field(default_factory=tuple)
    component_count: int = 0
    net_count: int = 0
    #: ``True`` when pin alias validation was skipped (no symbol library available).
    pin_validation_skipped: bool = False


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DrcResult:
    """Result of the ``drc`` command."""

    passed: bool
    stderr: str
    validation: ValidationResult | None = None


@dataclass(frozen=True)
class ErcResult:
    """Result of the ``erc`` command."""

    passed: bool
    stderr: str


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExportGerbersResult:
    """Result of the ``export-gerbers`` command."""

    output_dir: Path
    files: tuple[Path, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ExportDrillResult:
    """Result of the ``export-drill`` command."""

    output_dir: Path


@dataclass(frozen=True)
class ExportBomResult:
    """Result of the ``export-bom`` command."""

    output_file: Path
    lines: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PackageFabResult:
    """Result of the ``package-for-fab`` command."""

    output_path: Path
    size_bytes: int


@dataclass(frozen=True)
class ExportPosResult:
    """Result of the ``export-pos`` command."""

    output_file: Path
    component_count: int


@dataclass(frozen=True)
class Export3dResult:
    """Result of the ``export-3d`` command."""

    output_file: Path
    size_bytes: int


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreviewSchematicResult:
    """Result of the ``preview-schematic`` command."""

    svg_file: Path
    png_file: Path | None = None


@dataclass(frozen=True)
class PreviewPcbResult:
    """Result of the ``preview-pcb`` command."""

    layer_files: tuple[tuple[str, Path], ...] = field(default_factory=tuple)
    glb_file: Path | None = None


# ---------------------------------------------------------------------------
# pcb
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SetBoardSizeResult:
    """Result of the ``set-board-size`` command."""

    width: float
    height: float
    pcb_file_name: str
    dry_run: bool = False


@dataclass(frozen=True)
class ImportNetlistResult:
    """Result of the ``import-netlist`` command."""

    netlist_file: Path
    # Each entry: (ref, value, footprint)
    components: tuple[tuple[str, str, str], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AutoPlaceResult:
    """Result of the ``auto-place`` command."""

    placed: tuple[FootprintMoveSpec, ...]
    spacing: float
    dry_run: bool = False


@dataclass(frozen=True)
class AutoRouteResult:
    """Result of the ``auto-route`` command (success path only; failures raise)."""

    ses_file_name: str
    routes_imported: bool


# ---------------------------------------------------------------------------
# sch
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AddComponentResult:
    """Result of the ``add-component`` command."""

    ref: str
    lib_sym: str
    value: str
    x: float
    y: float
    pins: tuple[str, ...]
    has_footprint: bool
    dry_run: bool = False


@dataclass(frozen=True)
class AddNetResult:
    """Result of the ``add-net`` command."""

    name: str
    x: float
    y: float
    dry_run: bool = False


@dataclass(frozen=True)
class ConnectResult:
    """Result of the ``connect`` command."""

    x1: float
    y1: float
    x2: float
    y2: float
    dry_run: bool = False


@dataclass(frozen=True)
class ApplyPatternResult:
    """Result of the ``apply-pattern`` command (Phase 9.2)."""

    pattern: str
    #: Reference designators for every component placed by the pattern.
    components: tuple[str, ...]
    #: Net names referenced by the pattern (labels placed on the schematic).
    nets: tuple[str, ...]
    dry_run: bool = False


# ---------------------------------------------------------------------------
# lint / validate / format  (Phase 6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LintFileResult:
    """Result of the ``lint-sch`` / ``lint-pcb`` command."""

    path: Path
    issues: tuple[_LintFinding, ...]  # type: ignore[valid-type]
    error_count: int
    warning_count: int
    ok: bool  # True when no ERROR-severity issues are present


@dataclass(frozen=True)
class ValidateFileResult:
    """Result of the ``validate-sch`` / ``validate-pcb`` command."""

    path: Path
    syntax_ok: bool
    lint_issues: tuple[_LintFinding, ...]  # type: ignore[valid-type]
    lint_error_count: int
    lint_warning_count: int
    kicad_checked: bool
    kicad_ok: bool
    ok: bool  # overall: syntax + no lint errors (+ kicad when checked)


@dataclass(frozen=True)
class FormatFileResult:
    """Result of the ``format-sch`` / ``format-pcb`` command."""

    path: Path
    changed: bool
    size_bytes: int


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DoctorCheckItem:
    """A single health-check entry in a :class:`DoctorResult`."""

    status: Literal["ok", "warn", "error", "info"]
    label: str
    message: str
    detail: str | None = None


@dataclass(frozen=True)
class DoctorResult:
    """Result of the ``doctor`` command."""

    overall_ok: bool
    checks: tuple[DoctorCheckItem, ...]


# ---------------------------------------------------------------------------
# external
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PcbwayQuoteResult:
    """Result of the ``pcbway-quote`` command."""

    quantity: int
    layers: int
    thickness: float
    board_cost: float
    shipping: float
    total: float
    gerber_zip: Path | None = None


# ---------------------------------------------------------------------------
# search-symbols
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SymbolMatch:
    """A single symbol that matched a ``search-symbols`` query."""

    symbol_id: str  # e.g. "Device:C_Polarized"
    description: str  # value of ki_description property, or ""
    pin_count: int


@dataclass(frozen=True)
class SearchSymbolsResult:
    """Result of the ``search-symbols`` command."""

    query: str
    matches: tuple[SymbolMatch, ...]
    symbols_dirs: tuple[str, ...]  # directories that were searched


@dataclass(frozen=True)
class BuildSymbolIndexResult:
    """Result of the ``build-symbol-index`` command."""

    dirs_scanned: tuple[str, ...]
    files_scanned: int
    files_updated: int
    total_indexed_symbols: int


@dataclass(frozen=True)
class DebugSymbolResult:
    """Result of the ``debug-symbol`` command."""

    symbol_id: str  # "Lib:Name"
    extends_base: str | None  # "Lib:BaseName" if symbol uses (extends ...), else None
    pin_numbers: tuple[str, ...]  # resolved pin numbers, including inherited ones
    pin_count: int

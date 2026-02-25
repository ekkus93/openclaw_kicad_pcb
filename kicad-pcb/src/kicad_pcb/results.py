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
    from .models import FootprintMoveSpec, ProjectRef, ValidationResult


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


@dataclass(frozen=True)
class AddNetResult:
    """Result of the ``add-net`` command."""

    name: str
    x: float
    y: float


@dataclass(frozen=True)
class ConnectResult:
    """Result of the ``connect`` command."""

    x1: float
    y1: float
    x2: float
    y2: float


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

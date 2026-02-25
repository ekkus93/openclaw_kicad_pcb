"""Typed domain models for the kicad-pcb skill.

All models are dataclasses.  Frozen (immutable) value objects are used wherever
possible; ``ValidationResult`` is mutable because it accumulates issues.

Factory class-methods (``from_args``, ``from_dict``, etc.) centralise the
argument-normalisation and string-parsing logic that was previously scattered
across command functions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .errors import UserError

# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectRef:
    """A reference to an on-disk KiCad project directory.

    Replaces the plain ``dict`` previously threaded through all command
    functions.  Use :meth:`from_dict` / :meth:`to_dict` for JSON serialisation
    so the stored format remains backward-compatible.
    """

    name: str
    path: Path
    created: str = ""
    description: str = ""

    # ------------------------------------------------------------------
    # Convenience file-path properties
    # ------------------------------------------------------------------

    @property
    def sch_file(self) -> Path:
        """Absolute path to the project schematic file."""
        return self.path / f"{self.name}.kicad_sch"

    @property
    def pcb_file(self) -> Path:
        """Absolute path to the project PCB file."""
        return self.path / f"{self.name}.kicad_pcb"

    @property
    def pro_file(self) -> Path:
        """Absolute path to the project manifest file."""
        return self.path / f"{self.name}.kicad_pro"

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, d: dict) -> ProjectRef:
        """Construct from the JSON dict stored in ``current_project.json``."""
        return cls(
            name=d["name"],
            path=Path(d["path"]),
            created=d.get("created", d.get("opened", "")),
            description=d.get("description", ""),
        )

    def to_dict(self) -> dict:
        """Serialise to the JSON dict stored in ``current_project.json``."""
        return {
            "name": self.name,
            "path": str(self.path),
            "created": self.created,
            "description": self.description,
        }


# ---------------------------------------------------------------------------
# Schematic operations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComponentSpec:
    """Specification for a symbol to be added to the schematic.

    ``lib_sym`` must use the ``Library:Symbol`` format (e.g. ``Device:R``).
    """

    lib_sym: str
    ref: str
    value: str
    footprint: str = ""

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def lib_name(self) -> str:
        """Library portion of ``lib_sym`` (e.g. ``Device``)."""
        return self.lib_sym.split(":", 1)[0]

    @property
    def sym_name(self) -> str:
        """Symbol portion of ``lib_sym`` (e.g. ``R``)."""
        return self.lib_sym.split(":", 1)[1]

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_args(cls, args: object) -> ComponentSpec:
        """Parse from argparse namespace; raises :class:`.UserError` on bad input."""
        lib_sym: str = getattr(args, "lib_sym", "")
        if ":" not in lib_sym:
            raise UserError("Format must be Library:Symbol  e.g. Device:R")
        sym_name = lib_sym.split(":", 1)[1]
        return cls(
            lib_sym=lib_sym,
            ref=getattr(args, "ref", ""),
            value=getattr(args, "value", None) or sym_name,
            footprint=getattr(args, "footprint", None) or "",
        )


@dataclass(frozen=True)
class WireSegment:
    """A wire between two schematic coordinate pairs (mm)."""

    x1: float
    y1: float
    x2: float
    y2: float

    @classmethod
    def from_args(cls, args: object) -> WireSegment:
        """Parse ``--from X,Y --to X,Y`` CLI arguments."""
        try:
            x1, y1 = (float(v) for v in getattr(args, "from_pt", "").split(","))
            x2, y2 = (float(v) for v in getattr(args, "to_pt", "").split(","))
        except ValueError:
            raise UserError("Coordinates must be x,y  e.g. --from 50.8,76.2")
        return cls(x1=x1, y1=y1, x2=x2, y2=y2)


@dataclass(frozen=True)
class NetLabelSpec:
    """Specification for a net label to be added to the schematic (mm)."""

    name: str
    x: float = 50.8
    y: float = 50.8

    @classmethod
    def from_args(cls, args: object) -> NetLabelSpec:
        """Parse from argparse namespace."""
        return cls(
            name=getattr(args, "name", ""),
            x=getattr(args, "x", None) or 50.8,
            y=getattr(args, "y", None) or 50.8,
        )


# ---------------------------------------------------------------------------
# PCB operations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoardOutlineRect:
    """Rectangular board outline dimensions in mm."""

    width: float
    height: float

    @classmethod
    def from_args(cls, args: object) -> BoardOutlineRect:
        """Parse ``WxH`` size string from argparse namespace."""
        size_str: str = getattr(args, "size", "")
        try:
            w, h = (float(v) for v in size_str.lower().split("x"))
        except ValueError:
            raise UserError("Size must be WxH in mm  e.g. 50x30")
        if w <= 0 or h <= 0:
            raise UserError(f"Board dimensions must be positive (got {w}×{h})")
        return cls(width=w, height=h)

    @property
    def corners(self) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        """Four edge segments forming a closed rectangle at origin."""
        w, h = self.width, self.height
        return [
            ((0.0, 0.0), (w, 0.0)),
            ((w, 0.0), (w, h)),
            ((w, h), (0.0, h)),
            ((0.0, h), (0.0, 0.0)),
        ]


@dataclass(frozen=True)
class FootprintMoveSpec:
    """The target placement for a single footprint during auto-place."""

    ref: str
    x: float
    y: float


# ---------------------------------------------------------------------------
# Validation / linting
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LintIssue:
    """A single issue reported by DRC, ERC, or a custom lint rule."""

    severity: str  # "error" | "warning" | "info" | "unknown"
    description: str

    @classmethod
    def from_dict(cls, d: dict) -> LintIssue:
        """Construct from a KiCad DRC/ERC JSON violation entry."""
        return cls(
            severity=d.get("severity", "unknown"),
            description=d.get("description", "No description"),
        )


@dataclass
class ValidationResult:
    """Aggregated result of a DRC or ERC run."""

    passed: bool
    issues: list[LintIssue] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def from_report(cls, report: dict, returncode: int) -> ValidationResult:
        """Build from a parsed kicad-cli JSON report dict."""
        violations = report.get("violations", [])
        issues = [LintIssue.from_dict(v) for v in violations]
        return cls(passed=(returncode == 0 and not issues), issues=issues)

    # ------------------------------------------------------------------
    # Derived counts
    # ------------------------------------------------------------------

    @property
    def error_count(self) -> int:
        """Number of error-severity issues."""
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        """Number of warning-severity issues."""
        return sum(1 for i in self.issues if i.severity == "warning")

"""Public types and fix-suggestion catalogue for the lint subsystem.

This module contains only data types and static metadata — no AST traversal.
All three rule domains (SCH, LAY, PCB) import their shared types from here.

Types
-----
LintSeverity    Severity level enum (WARNING / ERROR).
LintIssue       Immutable frozen dataclass for a single finding.
LintError       Exception raised when ERROR-level issues are found.

Constants
---------
LINT_SUGGESTIONS    Human-readable fix hints keyed by rule code.
_ERR / _WARN        Module-level aliases used throughout the rule implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..errors import KiCadError

__all__ = [
    "LintError",
    "LintIssue",
    "LintSeverity",
    "LINT_SUGGESTIONS",
]


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class LintSeverity(Enum):
    """Severity level of a lint finding."""

    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class LintIssue:
    """A single structured lint finding."""

    severity: LintSeverity
    code: str
    message: str
    path: str | None = None  # optional AST path context (e.g. "kicad_sch/symbol")


class LintError(KiCadError):
    """Raised by the validation pipeline when ERROR-level lint issues are found."""

    def __init__(self, message: str, issues: list[LintIssue]) -> None:
        super().__init__(message)
        self.issues: list[LintIssue] = issues


# ---------------------------------------------------------------------------
# Internal severity aliases (re-imported by lint_helpers, lint_sch, lint_pcb)
# ---------------------------------------------------------------------------

_ERR = LintSeverity.ERROR
_WARN = LintSeverity.WARNING


# ---------------------------------------------------------------------------
# Fix suggestions (presented by the CLI diagnostics layer)
# ---------------------------------------------------------------------------

LINT_SUGGESTIONS: dict[str, str] = {
    "SCH001": "Check that the file is a KiCad schematic (.kicad_sch).",
    "SCH002": "Re-save the schematic in KiCad to regenerate unique UUIDs.",
    "SCH003": "Rename duplicate reference designators (e.g. change second R1 to R2).",
    "SCH004": "Add a 'Reference' property to the symbol in KiCad's symbol editor.",
    "SCH005": "Add a 'Value' property to the symbol in KiCad's symbol editor.",
    "SCH006": "Ensure the '(at x y)' coordinates contain valid numbers.",
    "SCH007": "Ensure wire '(pts (xy …) (xy …))' contains valid numeric coordinates.",
    "SCH008": "Embed the symbol definition via 'Save Symbol Copy' in KiCad.",
    "SCH009": "Embed the missing library symbol or check that the lib_id matches.",
    "SCH010": (
        "Add an '(at x y rotation)' node to every label/global_label/hierarchical_label "
        "so KiCad knows where to place it on the schematic."
    ),
    "PCB001": "Check that the file is a KiCad PCB layout (.kicad_pcb).",
    "PCB002": "Re-save the PCB in KiCad to regenerate unique UUIDs.",
    "PCB003": "Ensure every footprint has an '(at x y)' placement node.",
    "PCB004": "Ensure the footprint '(at x y [rotation])' contains valid numbers.",
    "PCB005": (
        "Add a board outline: run `set-board-size WxH` or draw Edge.Cuts in KiCad's PCB editor."
    ),
    "PCB006": "Close the board outline — all Edge.Cuts segments must connect end-to-end.",
    "PCB007": "Check that Edge.Cuts dimensions are non-zero and within a sane range.",
    "PCB008": (
        "Ensure all Edge.Cuts gr_lines have a valid '(layer \"Edge.Cuts\")' "
        "node and a numeric width."
    ),
    "PCB009": (
        "Move footprints closer to the origin (coordinates should be < 10 000 mm from origin)."
    ),
    "PCB010": (
        "Add a '(layer \"<layer_name>\")' node to every gr_line/gr_arc/gr_rect/gr_poly/gr_curve."
    ),
    "PCB011": (
        "Add a '(layers \"<Cu_layer>\")' node to every pad "
        "so KiCad knows which copper layers it belongs to."
    ),
    "LAY001": (
        "Reduce repeated net labels by connecting symbols with wires "
        "instead of placing the same label stub more than 3 times."
    ),
    "LAY002": (
        "Replace short stub wires + labels with direct wire connections between symbols "
        "to improve schematic readability."
    ),
    "LAY003": (
        "Move overlapping symbols apart by re-running 'apply-netlist' "
        "or manually repositioning them in KiCad's schematic editor."
    ),
    "LAY004": (
        "Move the symbol inside the A4 page area (0–297 × 0–210 mm). "
        "Re-run 'apply-netlist' to recompute positions from the netlist."
    ),
    "LAY005": (
        "Add net labels or wires to connect isolated wire islands, "
        "or verify that all schematic sections are intentionally separate sheets."
    ),
    "LAY007": (
        "Reduce wire crossings by re-running the layout or adjusting component "
        "placement order; check signal-flow ordering."
    ),
}

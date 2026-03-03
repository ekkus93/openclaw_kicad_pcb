"""Structural lint rules — public facade.

All rule implementations live in:
  lint_types   — LintSeverity, LintIssue, LintError, LINT_SUGGESTIONS
  lint_helpers — shared private AST helpers
  lint_sch     — lint_schematic, lint_schematic_layout (SCH + LAY rules)
  lint_pcb     — lint_pcb (PCB rules)

Import from this module for backwards compatibility with existing call sites.
"""

from __future__ import annotations

from .lint_pcb import lint_pcb
from .lint_sch import lint_schematic, lint_schematic_layout
from .lint_types import LINT_SUGGESTIONS, LintError, LintIssue, LintSeverity

__all__ = [
    "LintError",
    "LintIssue",
    "LintSeverity",
    "LINT_SUGGESTIONS",
    "lint_pcb",
    "lint_schematic",
    "lint_schematic_layout",
]

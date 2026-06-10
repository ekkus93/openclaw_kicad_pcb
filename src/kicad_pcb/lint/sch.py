"""Schematic (SCH) and layout-readability (LAY) lint rules.

Structural checks
-----------------
:func:`lint_schematic` — SCH001–SCH010
    Enforce correctness invariants: valid root, unique UUIDs, required
    properties, well-formed AST nodes.  Call on every schematic; failures
    here may block downstream processing.

Readability checks
------------------
:func:`lint_schematic_layout` — LAY001–LAY005
    Detect visual/readability problems on structurally valid schematics
    (label-stub style, overlapping symbols, isolated wire islands).  Call
    when you want design-quality gating, not just structural validity.

Crowding checks (Phase 2)
------------------------
:func:`lint_layout_crowding` — LAY006, LAY008
    Detect local crowding and insufficient inter-block spacing on generated
    layouts (Phase 2.4 CODE_REVIEW6 roadmap). Uses block detection and
    density metrics to enforce spacing standards.

:func:`lint_layout_wire_crossings` — LAY007
    Detect excessive wire crossing density (signal flow optimization).

Composition checks (Phase 8)
----------------------------
:func:`lint_layout_composition` — LAY012, LAY013
    Detect poor page balance and awkward central composition in generated
    schematics (Phase 8.3 CODE_REVIEW6 roadmap). Uses the page-composition
    helpers from the snap pass plus block-role information when available.

Wire quality checks (Phase 6)
------------------------------
:func:`lint_wire_quality` — LAY009, LAY010
    Detect excessive short wire segments and over-routed local connections
    (Phase 6.2 CODE_REVIEW6 roadmap). Flags nets with too many short jogs
    and simple connections using unnecessary routing complexity.

:func:`lint_local_direct_wiring` — LAY011
    Detect nearby 2-pin nets that could have been wired directly but weren't
    (Phase 6.3 CODE_REVIEW6 roadmap). Prefers simple local connections over
    unnecessarily complex routing.

Both functions take the root :class:`~kicad_pcb.sexpr.nodes.ListNode`
produced by parsing a ``.kicad_sch`` file.
"""

from __future__ import annotations

from ._sch_composition import lint_layout_composition
from ._sch_layout import lint_layout_crowding, lint_layout_wire_crossings, lint_schematic_layout
from ._sch_structural import lint_schematic
from ._sch_wire_quality import lint_local_direct_wiring, lint_wire_quality
from .defs import LintIssue

__all__ = [
    "lint_schematic",
    "lint_schematic_layout",
    "lint_layout_composition",
    "lint_layout_crowding",
    "lint_layout_wire_crossings",
    "lint_wire_quality",
    "lint_local_direct_wiring",
    "LintIssue",
]

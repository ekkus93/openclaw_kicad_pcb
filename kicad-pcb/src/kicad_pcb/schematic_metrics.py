"""Schematic readability metrics.

Provides lightweight, read-only analysis functions over a parsed
:class:`~kicad_pcb.sch_doc.SchematicDoc` (or its root ``ListNode``).  All
functions are pure — they never mutate the document.

Typical usage::

    from pathlib import Path
    from kicad_pcb.sch_doc import SchematicDoc
    from kicad_pcb.schematic_metrics import (
        count_distinct_x_columns,
        count_global_labels,
        run_layout_lints,
        wire_stub_ratio,
    )

    doc = SchematicDoc.load(Path("OpenClaw_Managed.kicad_sch"))
    print(count_distinct_x_columns(doc))
    print(count_global_labels(doc, text="GND"))
    print(run_layout_lints(doc))
    print(wire_stub_ratio(doc))

These helpers are used by acceptance tests (Phase 6 golden tests) to enforce
readability criteria on generated schematics.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kicad_pcb.sch_doc import SchematicDoc

from kicad_pcb.lint.helpers import _collect_wire_segments
from kicad_pcb.lint.sch import LintIssue, lint_schematic_layout
from kicad_pcb.sexpr.nodes import ListNode, StringNode
from kicad_pcb.sexpr.utils import walk

__all__ = [
    "count_distinct_x_columns",
    "count_global_labels",
    "run_layout_lints",
    "wire_stub_ratio",
]


def count_distinct_x_columns(
    doc: SchematicDoc,
    *,
    tolerance_mm: float = 0.5,
) -> int:
    """Return the number of visually distinct x-column positions.

    Two symbols are considered to share a column when their x-coordinates
    differ by less than *tolerance_mm*.  The algorithm divides the x axis
    into buckets of width *tolerance_mm* and counts non-empty buckets.

    A higher column count indicates better horizontal spreading of components
    across the schematic — a signal-flow layout should produce ≥ 4 columns
    for a multi-stage circuit.

    Parameters
    ----------
    doc:
        The schematic document to analyse.
    tolerance_mm:
        Column-width tolerance in millimetres (default: 0.5 mm, approximately
        two KiCad grid steps).

    Returns
    -------
    int
        Number of distinct x-column buckets that contain at least one symbol.
    """
    symbols = doc.list_symbols()
    if not symbols:
        return 0
    buckets: set[int] = set()
    for sym in symbols:
        x = float(sym["x"])  # type: ignore[arg-type]
        buckets.add(int(x / tolerance_mm))
    return len(buckets)


def count_global_labels(
    doc: SchematicDoc,
    *,
    text: str = "GND",
) -> int:
    """Return the number of ``(global_label "text" …)`` nodes in *doc*.

    KiCad global labels are used to connect power nets (GND, VCC, etc.) across
    schematic sheets.  For a single-sheet design a lower count indicates
    cleaner power routing — ideally replaced by power symbols.

    Parameters
    ----------
    doc:
        The schematic document to analyse.
    text:
        The label text to match (case-sensitive, default ``"GND"``).

    Returns
    -------
    int
        Count of matching ``(global_label "text" …)`` nodes in the schematic.
    """
    count = 0
    for node in walk(doc.root):
        if (
            isinstance(node, ListNode)
            and node.key == "global_label"
            and len(node.items) >= 2  # noqa: PLR2004
            and isinstance(node.items[1], StringNode)
            and node.items[1].value == text
        ):
            count += 1
    return count


def run_layout_lints(doc: SchematicDoc) -> list[LintIssue]:
    """Run all layout lint rules and return the resulting issues.

    Thin wrapper around :func:`~kicad_pcb.lint.sch.lint_schematic_layout` that
    accepts a :class:`~kicad_pcb.sch_doc.SchematicDoc` directly (rather than a
    raw ``ListNode``).

    Parameters
    ----------
    doc:
        The schematic document to lint.

    Returns
    -------
    list[LintIssue]
        All lint issues found (may be empty for a clean schematic).
    """
    return lint_schematic_layout(doc.root)


def wire_stub_ratio(
    doc: SchematicDoc,
    *,
    stub_len_mm: float = 5.08,
    tol: float = 0.2,
) -> float:
    """Return the fraction of all wire segments that are pin stubs.

    A wire segment is classified as a *stub* when its Euclidean length is
    ≤ ``stub_len_mm + tol``.  The default ``stub_len_mm = 5.08`` corresponds
    to 200 mil — the standard KiCad pin-stub extension length.

    A lower stub ratio indicates better direct-wire routing.  A schematic
    where every net is represented by label stubs will have a ratio close to
    1.0; a fully wired schematic will have a ratio close to 0.0 (only the
    mandatory pin-end stubs remain).

    Parameters
    ----------
    doc:
        The schematic document to analyse.
    stub_len_mm:
        Maximum length (mm) for a wire to be classified as a stub.
        Defaults to 5.08 mm (200 mil).
    tol:
        Tolerance added to *stub_len_mm* to absorb floating-point imprecision.
        Defaults to 0.2 mm.

    Returns
    -------
    float
        Ratio of stub wires to total wires, in ``[0.0, 1.0]``.
        Returns ``0.0`` if there are no wire segments.
    """
    segs = _collect_wire_segments(doc.root.items)
    if not segs:
        return 0.0
    threshold = stub_len_mm + tol
    stubs = sum(1 for x1, y1, x2, y2 in segs if math.hypot(x2 - x1, y2 - y1) <= threshold)
    return stubs / len(segs)

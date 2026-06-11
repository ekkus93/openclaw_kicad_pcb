"""Shared position helpers and column-based metrics for schematic analysis."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, TypeVar

from kicad_pcb.sexpr.nodes import ListNode, StringNode
from kicad_pcb.sexpr.utils import walk

if TYPE_CHECKING:
    from kicad_pcb.sch_doc import SchematicDoc

T = TypeVar("T")


def _symbol_ref_positions(
    doc: SchematicDoc,
    *,
    exclude_power_symbols: bool = False,
) -> dict[str, tuple[float, float]]:
    """Return a mapping of symbol ref to (x, y) position.

    Power symbols can be excluded because they often share crowded support
    lanes and would distort readability metrics aimed at placed components.
    """
    positions: dict[str, tuple[float, float]] = {}
    for sym in doc.list_symbols():
        ref_raw = sym.get("ref")
        x_raw = sym.get("x")
        y_raw = sym.get("y")
        if not isinstance(ref_raw, str):
            continue
        if exclude_power_symbols and ref_raw.startswith("#PWR"):
            continue
        if not isinstance(x_raw, (float, int)) or not isinstance(y_raw, (float, int)):
            continue
        positions[ref_raw] = (float(x_raw), float(y_raw))
    return positions


def _resolve_placed_refs(
    mapping: Mapping[str, T],
    ref: str,
) -> list[tuple[str, T]]:
    """Resolve a base device ref to matching placed-unit refs when needed."""

    if ref in mapping:
        return [(ref, mapping[ref])]

    unit_suffix = re.compile(rf"^{re.escape(ref)}[A-Z]+$")
    return sorted(
        (
            (candidate_ref, value)
            for candidate_ref, value in mapping.items()
            if unit_suffix.match(candidate_ref)
        ),
        key=lambda item: item[0],
    )


def _anchor_x_coordinate(
    mapping: dict[str, tuple[float, float]],
    anchor_ref: str,
) -> tuple[float, set[str]]:
    """Return a stable x anchor for exact refs or placed-unit siblings."""

    matches: list[tuple[str, tuple[float, float]]] = _resolve_placed_refs(mapping, anchor_ref)
    if not matches:
        msg = f"Anchor ref not found in schematic: {anchor_ref}"
        raise ValueError(msg)

    xs = sorted(value[0] for _ref, value in matches)
    anchor_x = xs[len(xs) // 2]
    return anchor_x, {ref for ref, _value in matches}


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


def count_refs_in_same_x_column_as(
    doc: SchematicDoc,
    anchor_ref: str,
    *,
    tolerance_mm: float = 0.5,
    refs: set[str] | None = None,
    include_anchor: bool = False,
) -> int:
    """Count refs whose x-position falls in the same visual column as anchor_ref.

    This is useful for detecting layout collapse around an anchor component,
    such as too many passives sharing the op-amp column.
    """
    positions = _symbol_ref_positions(doc)
    anchor_x, anchor_refs = _anchor_x_coordinate(positions, anchor_ref)
    count = 0
    for ref, (x, _y) in positions.items():
        if refs is not None and ref not in refs:
            continue
        if ref in anchor_refs and not include_anchor:
            continue
        if abs(x - anchor_x) <= tolerance_mm:
            count += 1
    return count


def count_non_power_symbols_in_same_x_column_as(
    doc: SchematicDoc,
    anchor_ref: str,
    *,
    tolerance_mm: float = 0.5,
    include_anchor: bool = False,
) -> int:
    """Count non-power symbols sharing the anchor component's x column."""
    positions = _symbol_ref_positions(doc, exclude_power_symbols=True)
    anchor_x, anchor_refs = _anchor_x_coordinate(positions, anchor_ref)
    return sum(
        1
        for ref, (x, _y) in positions.items()
        if (include_anchor or ref not in anchor_refs) and abs(x - anchor_x) <= tolerance_mm
    )


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

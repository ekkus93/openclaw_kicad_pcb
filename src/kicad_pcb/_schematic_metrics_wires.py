"""Wire, power-symbol, and spacing metrics for schematic analysis."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from kicad_pcb.lint.helpers import _collect_wire_segments
from kicad_pcb.lint.sch import LintIssue, lint_schematic_layout
from kicad_pcb.sexpr.nodes import ListNode, StringNode
from kicad_pcb.sexpr.utils import walk

if TYPE_CHECKING:
    from kicad_pcb.sch_doc import SchematicDoc


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


def count_power_symbols(
    doc: SchematicDoc,
    *,
    power_pin_name: str = "GND",
) -> int:
    """Return the number of power symbol instances in the schematic.

    Power symbols are special schematic symbols with ``(power yes)`` property
    that connect to power nets (GND, VCC, +5V, etc.).  A lower count indicates
    cleaner power routing with fewer visual ground/power flag repetitions.

    Parameters
    ----------
    doc:
        The schematic document to analyse.
    power_pin_name:
        The power net name to match (case-sensitive, default ``"GND"``).
        Note: This implementation counts power symbols by looking for the
        ``(power yes)`` marker, regardless of pin name.

    Returns
    -------
    int
        Count of power symbol instances in the schematic.
    """
    del power_pin_name
    count = 0
    for node in walk(doc.root):
        if not (isinstance(node, ListNode) and node.key == "symbol"):
            continue

        ref = ""
        symbol_id = ""
        in_bom = None
        on_board = None
        has_power_marker = False
        for child in node.items:
            if not isinstance(child, ListNode):
                continue
            if (
                child.key == "lib_id"
                and len(child.items) >= 2
                and isinstance(child.items[1], StringNode)
            ):
                symbol_id = child.items[1].value
            elif child.key == "property" and len(child.items) >= 3:
                name_node = child.items[1]
                value_node = child.items[2]
                if (
                    isinstance(name_node, StringNode)
                    and isinstance(value_node, StringNode)
                    and name_node.value == "Reference"
                ):
                    ref = value_node.value
            elif child.key == "in_bom" and len(child.items) >= 2:
                in_bom = getattr(child.items[1], "value", None)
            elif child.key == "on_board" and len(child.items) >= 2:
                on_board = getattr(child.items[1], "value", None)
            elif (
                child.key == "power"
                and len(child.items) >= 2
                and isinstance(child.items[1], StringNode)
                and child.items[1].value == "yes"
            ):
                has_power_marker = True

        if (
            ref.startswith("#PWR")
            or symbol_id.lower().startswith("power:")
            or (in_bom == "no" and on_board == "no")
            or has_power_marker
        ):
            count += 1
    return count


def count_short_wire_segments(
    doc: SchematicDoc,
    *,
    threshold_mm: float = 10.0,
) -> int:
    """Return the number of short wire segments (jogs) in the schematic.

    Short wire segments contribute to a "jaggy" or over-routed appearance.
    A lower count indicates cleaner, more direct routing with fewer
    unnecessary jogs.

    Parameters
    ----------
    doc:
        The schematic document to analyse.
    threshold_mm:
        Maximum length (mm) for a wire to be classified as short.
        Defaults to 10.0 mm (approximately 400 mil).

    Returns
    -------
    int
        Count of wire segments shorter than *threshold_mm*.
    """
    segs = _collect_wire_segments(doc.root.items)
    return sum(1 for x1, y1, x2, y2 in segs if math.hypot(x2 - x1, y2 - y1) <= threshold_mm)


def average_symbol_spacing(
    doc: SchematicDoc,
) -> float:
    """Return the average nearest-neighbor distance between symbols.

    A higher average spacing indicates less crowding and better whitespace
    distribution.  This metric helps detect overly dense regions.

    Parameters
    ----------
    doc:
        The schematic document to analyse.

    Returns
    -------
    float
        Average nearest-neighbor distance in millimetres.
        Returns 0.0 if there are fewer than 2 symbols.
    """
    symbols = doc.list_symbols()
    if len(symbols) < 2:  # noqa: PLR2004
        return 0.0

    positions = [(float(sym["x"]), float(sym["y"])) for sym in symbols]  # type: ignore[arg-type]

    total_distance = 0.0
    for i, (x1, y1) in enumerate(positions):
        min_dist = float("inf")
        for j, (x2, y2) in enumerate(positions):
            if i != j:
                dist = math.hypot(x2 - x1, y2 - y1)
                min_dist = min(min_dist, dist)
        if min_dist != float("inf"):
            total_distance += min_dist

    return total_distance / len(positions)


def page_region_density(
    doc: SchematicDoc,
    *,
    regions: int = 4,
) -> dict[str, float]:
    """Return symbol density by page quadrant or region.

    Divides the schematic page into *regions* quadrants (default 4: top-left,
    top-right, bottom-left, bottom-right) and computes the density (symbols
    per unit area) in each region.  Helps identify unbalanced page composition
    and crowded areas.

    Parameters
    ----------
    doc:
        The schematic document to analyse.
    regions:
        Number of regions to divide the page into.  Currently only supports
        4 (quadrants).  Future versions may support finer-grained grids.

    Returns
    -------
    dict[str, float]
        Mapping from region name (e.g., ``"top_left"``) to symbol count in
        that region.  Density is computed as count / total_symbols.

    Raises
    ------
    ValueError:
        If *regions* is not 4 (only quadrants are currently supported).
    """
    if regions != 4:  # noqa: PLR2004
        msg = "Only 4-region (quadrant) division is currently supported"
        raise ValueError(msg)

    symbols = doc.list_symbols()
    if not symbols:
        return {"top_left": 0.0, "top_right": 0.0, "bottom_left": 0.0, "bottom_right": 0.0}

    positions = [(float(sym["x"]), float(sym["y"])) for sym in symbols]  # type: ignore[arg-type]
    xs = [x for x, _ in positions]
    ys = [y for _, y in positions]
    x_mid = (min(xs) + max(xs)) / 2
    y_mid = (min(ys) + max(ys)) / 2

    counts = {"top_left": 0, "top_right": 0, "bottom_left": 0, "bottom_right": 0}
    for x, y in positions:
        if x <= x_mid and y <= y_mid:
            counts["top_left"] += 1
        elif x > x_mid and y <= y_mid:
            counts["top_right"] += 1
        elif x <= x_mid and y > y_mid:
            counts["bottom_left"] += 1
        else:
            counts["bottom_right"] += 1

    total = len(symbols)
    return {region: count / total for region, count in counts.items()}

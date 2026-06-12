"""Phase 6: wire simplification, chain routing, label attachment, ladder routing."""

from __future__ import annotations

import math
from collections import defaultdict

from kicad_pcb.router import (
    JunctionPoint,
    NetRouting,
    WireSegment,
    _best_direct_route_with_protected_points,
    _infer_safe_t_junctions,
    _l_route_with_protected_points,
    _simplify_wires,
    _split_wires_at_points,
)


def _count_short_segments(wires: list[WireSegment], threshold_mm: float = 5.1) -> int:
    """Count short wire segments at or below *threshold_mm*."""
    return sum(1 for seg in wires if math.hypot(seg.x2 - seg.x1, seg.y2 - seg.y1) <= threshold_mm)


def _connected_power_symbol_sets(routing: NetRouting) -> list[set[str]]:
    parent: dict[tuple[float, float], tuple[float, float]] = {}

    def find(point: tuple[float, float]) -> tuple[float, float]:
        parent.setdefault(point, point)
        if parent[point] != point:
            parent[point] = find(parent[point])
        return parent[point]

    def union(left: tuple[float, float], right: tuple[float, float]) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    labels_by_root: dict[tuple[float, float], set[str]] = defaultdict(set)
    for wire in routing.wires:
        start = (round(wire.x1, 2), round(wire.y1, 2))
        end = (round(wire.x2, 2), round(wire.y2, 2))
        union(start, end)
    for power_symbol in routing.power_symbols:
        point = (round(power_symbol.x, 2), round(power_symbol.y, 2))
        labels_by_root[find(point)].add(f"PWR:{power_symbol.net_name}")
    return list(labels_by_root.values())


def test_simplify_merges_colinear_horizontal_segments() -> None:
    """Consecutive horizontal segments on the same line are merged."""
    # Two horizontal segments that share an endpoint
    wires = [
        WireSegment(0.0, 10.0, 5.0, 10.0),  # (0, 10) → (5, 10)
        WireSegment(5.0, 10.0, 10.0, 10.0),  # (5, 10) → (10, 10)
    ]

    result = _simplify_wires(wires)

    assert len(result) == 1, "Two colinear segments should merge into one"
    merged = result[0]
    assert merged.y1 == 10.0 and merged.y2 == 10.0, "Merged segment should be horizontal"
    assert {merged.x1, merged.x2} == {0.0, 10.0}, "Merged segment should span full length"


def test_simplify_merges_colinear_vertical_segments() -> None:
    """Consecutive vertical segments on the same line are merged."""
    wires = [
        WireSegment(20.0, 0.0, 20.0, 5.0),  # (20, 0) → (20, 5)
        WireSegment(20.0, 5.0, 20.0, 10.0),  # (20, 5) → (20, 10)
    ]

    result = _simplify_wires(wires)

    assert len(result) == 1, "Two colinear vertical segments should merge"
    merged = result[0]
    assert merged.x1 == 20.0 and merged.x2 == 20.0, "Merged segment should be vertical"
    assert {merged.y1, merged.y2} == {0.0, 10.0}, "Merged segment should span full length"


def test_simplify_preserves_non_colinear_segments() -> None:
    """L-shaped wires (horizontal + vertical) are not merged."""
    wires = [
        WireSegment(0.0, 0.0, 10.0, 0.0),  # horizontal
        WireSegment(10.0, 0.0, 10.0, 10.0),  # vertical (shares endpoint but different direction)
    ]

    result = _simplify_wires(wires)

    assert len(result) == 2, "Non-colinear segments should not merge"


def test_simplify_preserves_junction_points() -> None:
    """Segments meeting at a junction (degree ≥ 3) are not merged.

    A T-junction has one spine segment and two branch segments meeting
    at the junction point.  None of these should be merged because doing
    so would eliminate the junction.
    """
    # T-junction: horizontal spine with vertical branch
    #
    #       |
    #   ----+----
    #
    wires = [
        WireSegment(0.0, 10.0, 10.0, 10.0),  # left spine
        WireSegment(10.0, 10.0, 20.0, 10.0),  # right spine
        WireSegment(10.0, 0.0, 10.0, 10.0),  # vertical branch
    ]

    result = _simplify_wires(wires)

    # All three segments must be preserved (junction point has degree 3)
    assert len(result) == 3, "Junction segments should not be merged"
    # Verify junction point (10, 10) is still an endpoint of all affected segments
    endpoints = {(seg.x1, seg.y1) for seg in result} | {(seg.x2, seg.y2) for seg in result}
    assert (10.0, 10.0) in endpoints, "Junction point must remain as a wire endpoint"


def test_simplify_preserves_explicit_protected_midsegment_junction() -> None:
    wires = [
        WireSegment(10.0, 0.0, 10.0, 10.0),
        WireSegment(10.0, 10.0, 10.0, 20.0),
        WireSegment(5.0, 10.0, 15.0, 10.0),
    ]

    result = _simplify_wires(wires, protected_points={(10.0, 10.0)})

    assert len(result) == 3
    assert (
        sum(
            1
            for seg in result
            if math.isclose(seg.x1, 10.0, abs_tol=0.01) and math.isclose(seg.x2, 10.0, abs_tol=0.01)
        )
        == 2
    )


def test_split_wires_at_points_breaks_segments_at_explicit_junctions() -> None:
    result = _split_wires_at_points(
        [
            WireSegment(30.48, 99.06, 48.26, 99.06),
            WireSegment(39.37, 96.52, 39.37, 102.87),
        ],
        {(39.37, 99.06)},
    )

    assert len(result) == 4
    assert WireSegment(30.48, 99.06, 39.37, 99.06) in result
    assert WireSegment(39.37, 99.06, 48.26, 99.06) in result
    assert WireSegment(39.37, 96.52, 39.37, 99.06) in result
    assert WireSegment(39.37, 99.06, 39.37, 102.87) in result


def test_infer_safe_t_junctions_adds_orthogonal_tee_outside_component_bodies() -> None:
    """A wire endpoint teeing into a trunk away from symbols gets a junction."""
    wires = [
        WireSegment(175.26, 118.11, 175.26, 152.40),
        WireSegment(168.91, 130.18, 175.26, 130.18),
    ]

    result = _infer_safe_t_junctions(
        wires,
        protected_points=set(),
        positions={
            "C64": (175.26, 109.22, 0.0),
            "U2": (175.26, 116.84, 0.0),
        },
    )

    assert result == [JunctionPoint(175.26, 130.18)]


def test_infer_safe_t_junctions_skips_points_inside_component_bodies() -> None:
    """Endpoints near a symbol body should not become inferred junctions."""
    wires = [
        WireSegment(57.15, 168.91, 57.15, 114.30),
        WireSegment(52.07, 168.91, 57.15, 168.91),
    ]

    result = _infer_safe_t_junctions(
        wires,
        protected_points=set(),
        positions={"C72": (55.88, 168.91, 90.0)},
    )

    assert result == []


def test_simplify_preserves_protected_points() -> None:
    """Pin endpoints (protected points) are preserved as wire start/end points."""
    # Pin at (5, 10) with stub to (5, 5), then routing to (10, 5)
    wires = [
        WireSegment(5.0, 10.0, 5.0, 5.0),  # stub from pin (vertical)
        WireSegment(5.0, 5.0, 10.0, 5.0),  # routing (horizontal)
    ]
    protected = {(5.0, 10.0)}  # pin position

    result = _simplify_wires(wires, protected_points=protected)

    # Should not merge because shared endpoint (5, 5) is where stub meets routing.
    # But actually these are non-colinear so they wouldn't merge anyway.
    # Better test: same direction but one endpoint is protected.
    wires = [
        WireSegment(5.0, 10.0, 5.0, 15.0),  # stub from pin (vertical)
        WireSegment(5.0, 15.0, 5.0, 20.0),  # routing extension (vertical, same line)
    ]
    protected = {(5.0, 10.0)}  # pin position

    result = _simplify_wires(wires, protected_points=protected)

    # These are colinear and share endpoint (5, 15), which is NOT protected.
    # They should merge.
    assert len(result) == 1, "Colinear segments should merge when shared point not protected"
    merged = result[0]
    assert (5.0, 10.0) in {(merged.x1, merged.y1), (merged.x2, merged.y2)}, (
        "Protected pin endpoint must remain as wire start/end"
    )


def test_simplify_preserves_label_attachment_boundaries() -> None:
    """Protected label attachment points keep adjacent vertical runs electrically separate."""

    wires = [
        WireSegment(0.0, 0.0, 0.0, 10.0),
        WireSegment(0.0, 10.0, 0.0, 20.0),
        WireSegment(0.0, 20.0, 0.0, 30.0),
    ]

    result = _simplify_wires(wires, protected_points={(0.0, 10.0), (0.0, 20.0)})

    assert len(result) == 3


def test_l_route_prefers_vertical_first_when_horizontal_elbow_crosses_other_stub_points() -> None:
    route = _l_route_with_protected_points(
        10.0,
        10.0,
        30.0,
        30.0,
        protected_points={(30.0, 15.0), (30.0, 20.0), (30.0, 25.0)},
    )

    assert route == [
        WireSegment(10.0, 10.0, 10.0, 30.0),
        WireSegment(10.0, 30.0, 30.0, 30.0),
    ]


def test_best_direct_route_uses_detour_when_both_elbows_hit_protected_stub_points() -> None:
    route = _best_direct_route_with_protected_points(
        20.0,
        10.0,
        40.0,
        30.0,
        protected_points={(20.0, 25.0), (25.0, 30.0), (40.0, 15.0)},
    )

    assert route == [
        WireSegment(20.0, 10.0, 45.08, 10.0),
        WireSegment(45.08, 10.0, 45.08, 30.0),
        WireSegment(45.08, 30.0, 40.0, 30.0),
    ]

"""Chain routing, cost metrics, and local analog routing heuristics."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .block_detection import BlockLayout
    from .circuit_ir import PinRefIR

from ._router_classify import _is_local_ladder_net
from ._router_geometry import (
    _best_direct_route_with_protected_points,
    _l_route,
    _manhattan,
    _route_candidate_key,
    _snap_grid,
)
from ._router_strats_basic import _shared_lane_route, _spine_route
from ._router_types import (
    WIRE_EXTEND_MM,
    JunctionPoint,
    SharedLanePlan,
    WireSegment,
)
from ._router_write import _simplify_wires
from .block_detection import BlockLayout, BlockRole

# ---------------------------------------------------------------------------
# Chain routing
# ---------------------------------------------------------------------------


def _chain_route(
    endpoints: list[tuple[float, float]],
    *,
    protected_points: set[tuple[float, float]] | None = None,
) -> tuple[list[WireSegment], list[JunctionPoint]]:
    """Route a compact 3-pin net as a simple ordered chain."""
    if len(endpoints) < 2:
        return [], []

    xs = [e[0] for e in endpoints]
    ys = [e[1] for e in endpoints]
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)

    if x_span >= y_span:
        ordered = sorted(endpoints, key=lambda point: (point[0], point[1]))
    else:
        ordered = sorted(endpoints, key=lambda point: (point[1], point[0]))

    segs: list[WireSegment] = []
    for index in range(len(ordered) - 1):
        x1, y1 = ordered[index]
        x2, y2 = ordered[index + 1]
        segs.extend(
            _best_direct_route_with_protected_points(
                x1,
                y1,
                x2,
                y2,
                protected_points=protected_points,
            )
        )

    protected = {(round(x, 2), round(y, 2)) for x, y in ordered}
    return _simplify_wires(segs, protected_points=protected), []


def _compact_aligned_chain_route(
    endpoints: list[tuple[float, float]],
    *,
    protected_points: set[tuple[float, float]] | None = None,
) -> tuple[list[WireSegment], list[JunctionPoint]]:
    if len(endpoints) < 2:
        return [], []
    ordered = sorted(endpoints, key=lambda point: (point[0], point[1]))
    if all(math.isclose(point[1], ordered[0][1], abs_tol=0.01) for point in ordered):
        lane_y = round(ordered[0][1] - WIRE_EXTEND_MM, 2)
        segs = []
        for x, y in ordered:
            if not math.isclose(y, lane_y, abs_tol=0.01):
                segs.append(WireSegment(x, y, x, lane_y))
        for (x1, _y1), (x2, _y2) in zip(ordered, ordered[1:], strict=False):
            segs.append(WireSegment(x1, lane_y, x2, lane_y))
        return segs, []
    ordered = sorted(endpoints, key=lambda point: (point[1], point[0]))
    if all(math.isclose(point[0], ordered[0][0], abs_tol=0.01) for point in ordered):
        lane_x = round(ordered[0][0] - WIRE_EXTEND_MM, 2)
        segs = []
        for x, y in ordered:
            if not math.isclose(x, lane_x, abs_tol=0.01):
                segs.append(WireSegment(x, y, lane_x, y))
        for (_x1, y1), (_x2, y2) in zip(ordered, ordered[1:], strict=False):
            segs.append(WireSegment(lane_x, y1, lane_x, y2))
        return segs, []
    return _chain_route(endpoints, protected_points=protected_points)


def _protected_shared_lane_route(
    endpoints: list[tuple[float, float]],
    *,
    protected_points: set[tuple[float, float]] | None = None,
) -> tuple[list[WireSegment], list[JunctionPoint]] | None:
    """Return a lower-collision shared lane route for crowded multi-pin nets when available."""
    if len(endpoints) < 3 or not protected_points:
        return None

    candidate_routes: list[tuple[list[WireSegment], list[JunctionPoint]]] = []
    for coordinate in sorted({round(point[0], 2) for point in endpoints}):
        candidate_routes.append(
            _shared_lane_route(endpoints, axis="vertical", coordinate=coordinate)
        )
    for coordinate in sorted({round(point[1], 2) for point in endpoints}):
        candidate_routes.append(
            _shared_lane_route(endpoints, axis="horizontal", coordinate=coordinate)
        )

    best_route, best_junctions = min(
        candidate_routes,
        key=lambda candidate: _route_candidate_key(
            candidate[0],
            protected_points=protected_points,
            endpoints=endpoints,
        ),
    )
    return best_route, best_junctions


def _buffer_follower_feedback_route(
    known: list[tuple[PinRefIR, tuple[float, float, float]]],
    stub_ends: list[tuple[float, float]],
    *,
    block_layout: BlockLayout | None,
) -> tuple[list[WireSegment], list[JunctionPoint]] | None:
    """Route a 3-pin buffer follower net as a short local loop plus branch."""
    if block_layout is None or len(known) != 3 or len(stub_ends) != 3:
        return None

    indices_by_ref: dict[str, list[int]] = {}
    for index, (pin_ref, _endpoint) in enumerate(known):
        indices_by_ref.setdefault(pin_ref.ref, []).append(index)

    repeated_refs = [(ref, indices) for ref, indices in indices_by_ref.items() if len(indices) == 2]
    if len(repeated_refs) != 1 or len(indices_by_ref) != 2:
        return None

    buffer_ref, shared_indices = repeated_refs[0]
    if block_layout.get_role(buffer_ref) != BlockRole.BUFFER_STAGE:
        return None

    downstream_index = next(index for index in range(len(known)) if index not in shared_indices)
    downstream_ref = known[downstream_index][0].ref
    if block_layout.get_role(downstream_ref) not in {
        BlockRole.OUTPUT_CONDITIONING,
        BlockRole.OUTPUT,
    }:
        return None

    first_index, second_index = shared_indices
    first_point = stub_ends[first_index]
    second_point = stub_ends[second_index]
    downstream_point = stub_ends[downstream_index]

    if abs(first_point[0] - second_point[0]) <= WIRE_EXTEND_MM:
        return None

    branch_index = (
        first_index
        if _manhattan(*first_point, *downstream_point)
        <= _manhattan(*second_point, *downstream_point)
        else second_index
    )
    feedback_index = second_index if branch_index == first_index else first_index

    branch_x, branch_y = stub_ends[branch_index]
    feedback_x, feedback_y = stub_ends[feedback_index]
    downstream_x, downstream_y = downstream_point

    loop_y = _snap_grid(min(branch_y, feedback_y) - WIRE_EXTEND_MM)
    if loop_y >= min(branch_y, feedback_y) - 0.05:
        loop_y = _snap_grid(loop_y - WIRE_EXTEND_MM)

    segs: list[WireSegment] = []
    if not math.isclose(feedback_y, loop_y, abs_tol=0.01):
        segs.append(WireSegment(feedback_x, feedback_y, feedback_x, loop_y))
    segs.append(WireSegment(feedback_x, loop_y, branch_x, loop_y))
    if not math.isclose(branch_y, loop_y, abs_tol=0.01):
        segs.append(WireSegment(branch_x, loop_y, branch_x, branch_y))
    segs.extend(_l_route(branch_x, branch_y, downstream_x, downstream_y))

    protected = {(round(x, 2), round(y, 2)) for x, y in stub_ends}
    return _simplify_wires(segs, protected_points=protected), []


# ---------------------------------------------------------------------------
# Route cost metrics
# ---------------------------------------------------------------------------


def _route_length(segments: list[WireSegment]) -> float:
    """Return total Manhattan wire length for *segments*."""
    return sum(_manhattan(seg.x1, seg.y1, seg.x2, seg.y2) for seg in segments)


def _route_visual_cost(
    segments: list[WireSegment],
    junctions: list[JunctionPoint],
) -> float:
    """Return a small readability-oriented cost for local routing alternatives."""
    local_junction_penalty = WIRE_EXTEND_MM / 2
    local_bend_penalty = (3 * WIRE_EXTEND_MM) / 4
    short_segment_threshold = WIRE_EXTEND_MM + 0.05
    short_segment_count = sum(
        1
        for seg in segments
        if _manhattan(seg.x1, seg.y1, seg.x2, seg.y2) <= short_segment_threshold
    )
    bend_count = max(len(segments) - 1, 0)
    return (
        _route_length(segments)
        + (len(junctions) * local_junction_penalty)
        + (bend_count * local_bend_penalty)
        + (short_segment_count * (WIRE_EXTEND_MM / 4))
    )


# ---------------------------------------------------------------------------
# Local analog routing heuristics
# ---------------------------------------------------------------------------


def _is_small_analog_chain_candidate(
    endpoints: list[tuple[float, float]],
    *,
    refs: tuple[str, ...] = (),
) -> bool:
    """Return True when compact 3-pin analog heuristics should evaluate *endpoints*."""
    if len(endpoints) != 3 or not _is_local_ladder_net(endpoints):
        return False
    return not (refs and all(ref.upper().startswith("R") for ref in refs))


def _prefer_chain_route(endpoints: list[tuple[float, float]]) -> bool:
    """Return True when a local 3-pin net reads better as a chain than a spine."""
    if len(endpoints) != 3:
        return False
    if all(math.isclose(point[1], endpoints[0][1], abs_tol=0.01) for point in endpoints):
        return True
    if all(math.isclose(point[0], endpoints[0][0], abs_tol=0.01) for point in endpoints):
        return True

    chain_segs, _ = _chain_route(endpoints)
    spine_segs, _ = _spine_route(endpoints)
    chain_length = _route_length(chain_segs)
    spine_length = _route_length(spine_segs)

    if chain_length < spine_length - 0.01:
        return True

    return math.isclose(chain_length, spine_length, abs_tol=0.01) and len(chain_segs) <= len(
        spine_segs
    )


def _prefer_small_analog_chain_route(
    endpoints: list[tuple[float, float]],
    *,
    inferred_plan: SharedLanePlan | None = None,
    refs: tuple[str, ...] = (),
) -> bool:
    """Return True when a compact local analog net reads better as a chain."""
    if not _is_small_analog_chain_candidate(endpoints, refs=refs):
        return False

    chain_segs, chain_junctions = _chain_route(endpoints)
    if inferred_plan is None:
        candidate_segs, candidate_junctions = _spine_route(endpoints)
    else:
        candidate_segs, candidate_junctions = _shared_lane_route(
            endpoints,
            axis=inferred_plan.axis,
            coordinate=inferred_plan.coordinate,
            min_bound=inferred_plan.min_orthogonal,
            max_bound=inferred_plan.max_orthogonal,
        )

    return (
        _route_visual_cost(chain_segs, chain_junctions)
        <= _route_visual_cost(
            candidate_segs,
            candidate_junctions,
        )
        + 0.01
    )

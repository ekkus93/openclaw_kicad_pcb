"""Wire simplification, power-symbol clustering, junction inference, and write_routing."""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from ._router_types import (
    SYMBOL_HALF_SIZE_MM,
    WIRE_EXTEND_MM,
    JunctionPoint,
    NetRouting,
    PowerSymbolPlacement,
    WireSegment,
)
from .errors import ErrorCode, UserError
from .layout import ORIGIN_X, ORIGIN_Y

if TYPE_CHECKING:
    from .circuit_ir import PinRefIR
    from .sch_doc import SchematicDoc


# ---------------------------------------------------------------------------
# Wire simplification
# ---------------------------------------------------------------------------
def _simplify_wires(  # noqa: PLR0912, PLR0915
    wires: list[WireSegment],
    *,
    protected_points: set[tuple[float, float]] | None = None,
) -> list[WireSegment]:
    """Simplify wire routing by merging consecutive colinear segments."""
    if not wires:
        return []

    def _segment_key(seg: WireSegment) -> tuple[tuple[float, float], tuple[float, float]]:
        p1 = (round(seg.x1, 2), round(seg.y1, 2))
        p2 = (round(seg.x2, 2), round(seg.y2, 2))
        return (p1, p2) if p1 <= p2 else (p2, p1)

    def _is_zero_length(seg: WireSegment) -> bool:
        return math.isclose(seg.x1, seg.x2, abs_tol=0.01) and math.isclose(
            seg.y1, seg.y2, abs_tol=0.01
        )

    normalized: list[WireSegment] = []
    seen_segments: set[tuple[tuple[float, float], tuple[float, float]]] = set()
    for seg in wires:
        if _is_zero_length(seg):
            continue
        key = _segment_key(seg)
        if key in seen_segments:
            continue
        seen_segments.add(key)
        normalized.append(seg)

    if protected_points is None:
        protected_points = set()

    segments = normalized
    changed = True

    while changed:
        changed = False
        new_segments = []
        used = set()

        degree: dict[tuple[float, float], int] = {}
        for seg in segments:
            p1 = (round(seg.x1, 2), round(seg.y1, 2))
            p2 = (round(seg.x2, 2), round(seg.y2, 2))
            degree[p1] = degree.get(p1, 0) + 1
            degree[p2] = degree.get(p2, 0) + 1

        for i, seg1 in enumerate(segments):
            if i in used:
                continue

            merged = False
            for j in range(i + 1, len(segments)):
                if j in used:
                    continue

                seg2 = segments[j]

                seg1_endpoints = {
                    (round(seg1.x1, 2), round(seg1.y1, 2)),
                    (round(seg1.x2, 2), round(seg1.y2, 2)),
                }
                seg2_endpoints = {
                    (round(seg2.x1, 2), round(seg2.y1, 2)),
                    (round(seg2.x2, 2), round(seg2.y2, 2)),
                }
                shared = seg1_endpoints & seg2_endpoints

                if not shared:
                    continue

                if any(pt in protected_points for pt in shared):
                    continue

                if any(degree.get(pt, 0) >= 3 for pt in shared):
                    continue

                # Horizontal segments (both have y1 == y2)
                if seg1.y1 == seg1.y2 and seg2.y1 == seg2.y2 and seg1.y1 == seg2.y1:
                    xs = sorted([seg1.x1, seg1.x2, seg2.x1, seg2.x2])
                    new_segments.append(WireSegment(xs[0], seg1.y1, xs[-1], seg1.y1))
                    used.add(i)
                    used.add(j)
                    merged = True
                    changed = True
                    break

                # Vertical segments (both have x1 == x2)
                elif seg1.x1 == seg1.x2 and seg2.x1 == seg2.x2 and seg1.x1 == seg2.x1:
                    ys = sorted([seg1.y1, seg1.y2, seg2.y1, seg2.y2])
                    new_segments.append(WireSegment(seg1.x1, ys[0], seg1.x1, ys[-1]))
                    used.add(i)
                    used.add(j)
                    merged = True
                    changed = True
                    break

            if not merged:
                new_segments.append(seg1)
                used.add(i)

        segments = new_segments

    return segments


def _split_wires_at_points(
    wires: list[WireSegment],
    split_points: set[tuple[float, float]],
) -> list[WireSegment]:
    """Split horizontal/vertical wires so explicit junctions become segment endpoints."""
    if not split_points:
        return list(wires)

    split_segments: list[WireSegment] = []
    for wire in wires:
        x1 = round(wire.x1, 2)
        y1 = round(wire.y1, 2)
        x2 = round(wire.x2, 2)
        y2 = round(wire.y2, 2)

        if math.isclose(x1, x2, abs_tol=0.01):
            candidate_points = sorted(
                point
                for point in split_points
                if math.isclose(point[0], x1, abs_tol=0.01) and min(y1, y2) < point[1] < max(y1, y2)
            )
            if y2 < y1:
                candidate_points.reverse()
        elif math.isclose(y1, y2, abs_tol=0.01):
            candidate_points = sorted(
                point
                for point in split_points
                if math.isclose(point[1], y1, abs_tol=0.01) and min(x1, x2) < point[0] < max(x1, x2)
            )
            if x2 < x1:
                candidate_points.reverse()
        else:
            candidate_points = []

        if not candidate_points:
            split_segments.append(wire)
            continue

        last_x, last_y = wire.x1, wire.y1
        for split_x, split_y in candidate_points:
            split_segments.append(WireSegment(last_x, last_y, split_x, split_y))
            last_x, last_y = split_x, split_y
        split_segments.append(WireSegment(last_x, last_y, wire.x2, wire.y2))

    return split_segments


# ---------------------------------------------------------------------------
# Power symbol clustering
# ---------------------------------------------------------------------------
def _clamp_emitted_power_symbols(
    power_symbols: list[PowerSymbolPlacement],
    *,
    emitted_wires: list[WireSegment],
) -> list[PowerSymbolPlacement]:
    """Clamp emitted power symbols onto the page and extend wires to match."""
    from ._router_geometry import _snap_grid  # noqa: PLC0415

    emitted_power_symbols: list[PowerSymbolPlacement] = []
    for power_symbol in power_symbols:
        clamped_x = max(power_symbol.x, ORIGIN_X)
        clamped_y = max(power_symbol.y, ORIGIN_Y)
        if not math.isclose(power_symbol.x, clamped_x, abs_tol=0.01):
            clamped_y = _snap_grid(clamped_y + (2 * WIRE_EXTEND_MM))
        if not math.isclose(power_symbol.x, clamped_x, abs_tol=0.01):
            emitted_wires.append(
                WireSegment(
                    power_symbol.x,
                    power_symbol.y,
                    clamped_x,
                    power_symbol.y,
                )
            )
        if not math.isclose(power_symbol.y, clamped_y, abs_tol=0.01):
            emitted_wires.append(
                WireSegment(
                    clamped_x,
                    power_symbol.y,
                    clamped_x,
                    clamped_y,
                )
            )
        emitted_power_symbols.append(
            PowerSymbolPlacement(
                power_symbol.net_name,
                clamped_x,
                clamped_y,
                power_symbol.angle,
            )
        )
    return emitted_power_symbols


def _should_cluster_emitted_power_symbols(
    seed: PowerSymbolPlacement,
    candidate: PowerSymbolPlacement,
) -> bool:
    """Return whether two emitted power symbols should collapse to one anchor."""
    if candidate.net_name != seed.net_name:
        return False
    if math.hypot(candidate.x - seed.x, candidate.y - seed.y) <= 16.0:
        return True
    if abs(candidate.x - seed.x) <= 8.0 and abs(candidate.y - seed.y) <= 32.0:
        return True
    return (
        abs(candidate.x - seed.x) <= 32.0
        and abs(candidate.y - seed.y) <= 24.0
        and min(candidate.x, seed.x) <= ORIGIN_X + 0.01
    )


def _cluster_emitted_power_symbols(
    power_symbols: list[PowerSymbolPlacement],
    *,
    emitted_wires: list[WireSegment],
) -> list[PowerSymbolPlacement]:
    """Merge nearby emitted power symbols and reconnect removed anchors by wire."""
    from ._router_geometry import _l_route  # noqa: PLC0415

    clustered_power_symbols: list[PowerSymbolPlacement] = []
    net_counts = Counter(symbol.net_name for symbol in power_symbols)
    pending_power_symbols = list(power_symbols)
    while pending_power_symbols:
        seed = pending_power_symbols.pop(0)
        if net_counts[seed.net_name] < 3:
            clustered_power_symbols.append(seed)
            continue
        cluster = [seed]
        remaining: list[PowerSymbolPlacement] = []
        for candidate in pending_power_symbols:
            if _should_cluster_emitted_power_symbols(seed, candidate):
                cluster.append(candidate)
            else:
                remaining.append(candidate)
        pending_power_symbols = remaining
        if len(cluster) == 1:
            clustered_power_symbols.append(seed)
            continue
        centroid_x = sum(symbol.x for symbol in cluster) / len(cluster)
        centroid_y = sum(symbol.y for symbol in cluster) / len(cluster)
        representative = min(
            cluster,
            key=lambda symbol: math.hypot(symbol.x - centroid_x, symbol.y - centroid_y),
        )
        clustered_power_symbols.append(representative)
        for symbol in cluster:
            if symbol == representative:
                continue
            emitted_wires.extend(_l_route(symbol.x, symbol.y, representative.x, representative.y))
    return clustered_power_symbols


# ---------------------------------------------------------------------------
# Junction inference
# ---------------------------------------------------------------------------
def _infer_safe_t_junctions(
    wires: list[WireSegment],
    *,
    protected_points: set[tuple[float, float]],
    positions: Mapping[str, tuple[float, float, float | None]] | None,
) -> list[JunctionPoint]:
    """Infer junctions for orthogonal tees that land outside component bodies."""

    def _axis(seg: WireSegment) -> str | None:
        if math.isclose(seg.x1, seg.x2, abs_tol=0.01):
            return "vertical"
        if math.isclose(seg.y1, seg.y2, abs_tol=0.01):
            return "horizontal"
        return None

    def _point_on_interior(px: float, py: float, seg: WireSegment) -> bool:
        if math.isclose(seg.x1, seg.x2, abs_tol=0.01):
            min_y = min(seg.y1, seg.y2)
            max_y = max(seg.y1, seg.y2)
            return (
                math.isclose(px, seg.x1, abs_tol=0.01)
                and not math.isclose(py, min_y, abs_tol=0.01)
                and not math.isclose(py, max_y, abs_tol=0.01)
                and min_y < py < max_y
            )
        if math.isclose(seg.y1, seg.y2, abs_tol=0.01):
            min_x = min(seg.x1, seg.x2)
            max_x = max(seg.x1, seg.x2)
            return (
                math.isclose(py, seg.y1, abs_tol=0.01)
                and not math.isclose(px, min_x, abs_tol=0.01)
                and not math.isclose(px, max_x, abs_tol=0.01)
                and min_x < px < max_x
            )
        return False

    def _inside_component_body(px: float, py: float) -> bool:
        if positions is None:
            return False
        for cx, cy, _angle in positions.values():
            if (cx - SYMBOL_HALF_SIZE_MM) <= px <= (cx + SYMBOL_HALF_SIZE_MM) and (
                cy - SYMBOL_HALF_SIZE_MM
            ) <= py <= (cy + SYMBOL_HALF_SIZE_MM):
                return True
        return False

    inferred: set[tuple[float, float]] = set()
    for index, seg in enumerate(wires):
        seg_axis = _axis(seg)
        if seg_axis is None:
            continue
        endpoints = ((seg.x1, seg.y1), (seg.x2, seg.y2))
        for px, py in endpoints:
            rounded = (round(px, 2), round(py, 2))
            if rounded in protected_points or _inside_component_body(px, py):
                continue
            for other_index, other in enumerate(wires):
                if other_index == index:
                    continue
                other_axis = _axis(other)
                if other_axis is None or other_axis == seg_axis:
                    continue
                if _point_on_interior(px, py, other):
                    inferred.add(rounded)
                    break
    return [JunctionPoint(x, y) for x, y in sorted(inferred)]


def _aligned_power_cluster_route_points(
    cluster: list[tuple[PinRefIR, tuple[float, float, float]]],
) -> tuple[list[WireSegment], list[tuple[float, float]], str | None, float | None]:
    """Return stub wires, stub-end points, and any fully aligned stub axis."""
    from ._router_geometry import _stub_end  # noqa: PLC0415

    stub_points = [_stub_end(x, y, angle) for _pin_ref, (x, y, angle) in cluster]
    shared_x = round(stub_points[0][0], 2) if stub_points else None
    shared_y = round(stub_points[0][1], 2) if stub_points else None
    aligned_x = (
        shared_x
        if shared_x is not None
        and all(math.isclose(point[0], shared_x, abs_tol=0.01) for point in stub_points)
        else None
    )
    aligned_y = (
        shared_y
        if shared_y is not None
        and all(math.isclose(point[1], shared_y, abs_tol=0.01) for point in stub_points)
        else None
    )

    stub_wires: list[WireSegment] = []
    route_points: list[tuple[float, float]] = []
    for _pin_ref, (wx, wy, wa) in cluster:
        ex, ey = _stub_end(wx, wy, wa)
        stub_wires.append(WireSegment(wx, wy, ex, ey))
        route_points.append((ex, ey))
    if aligned_x is not None:
        return stub_wires, route_points, "vertical", aligned_x
    if aligned_y is not None:
        return stub_wires, route_points, "horizontal", aligned_y
    return stub_wires, route_points, None, None


# ---------------------------------------------------------------------------
# Public write_routing
# ---------------------------------------------------------------------------
def write_routing(  # noqa: PLR0913
    *,
    doc: SchematicDoc,
    routing: NetRouting,
    new_uuid: Callable[[], str],
    stats: dict[str, int],
    symbols_dir: Path | None = None,
    project_name: str = "project",
    strict: bool = False,
) -> None:
    """Emit *routing* decisions into the schematic document *doc*."""
    from ._router_geometry import (  # noqa: PLC0415
        _fallback_power_label_position,
        _label_attachment_plan,
        _occupied_label_points,
        _occupied_wire_points,
        _segment_label_angle,
    )

    emitted_wires = list(routing.wires)
    emitted_power_symbols = _clamp_emitted_power_symbols(
        routing.power_symbols,
        emitted_wires=emitted_wires,
    )
    emitted_power_symbols = _cluster_emitted_power_symbols(
        emitted_power_symbols,
        emitted_wires=emitted_wires,
    )

    if routing.junctions:
        emitted_wires = _split_wires_at_points(
            emitted_wires,
            {(round(junction.x, 2), round(junction.y, 2)) for junction in routing.junctions},
        )
    emitted_protected_points: set[tuple[float, float]] = set()
    emitted_protected_points.update(
        (round(label.x, 2), round(label.y, 2)) for label in routing.labels
    )
    emitted_protected_points.update(
        (round(label.x, 2), round(label.y, 2)) for label in routing.global_labels
    )
    emitted_protected_points.update(
        (round(symbol.x, 2), round(symbol.y, 2)) for symbol in emitted_power_symbols
    )
    emitted_protected_points.update(
        (round(junction.x, 2), round(junction.y, 2)) for junction in routing.junctions
    )
    emitted_wires = _simplify_wires(emitted_wires, protected_points=emitted_protected_points)

    for seg in emitted_wires:
        doc.add_wire(seg.x1, seg.y1, seg.x2, seg.y2, new_uuid())
        stats["wires"] += 1

    for lbl in routing.labels:
        doc.add_label(lbl.name, lbl.x, lbl.y, new_uuid(), angle=lbl.angle)
        stats["labels"] += 1

    for glbl in routing.global_labels:
        doc.add_global_label(
            glbl.name, glbl.x, glbl.y, new_uuid(), angle=glbl.angle, shape="passive"
        )
        stats["global_labels"] = stats.get("global_labels", 0) + 1

    fallback_occupied_points = _occupied_wire_points(emitted_wires) | _occupied_label_points(
        routing
    )
    from ._router_types import _ProtectedPointContext  # noqa: PLC0415

    for idx, ps in enumerate(emitted_power_symbols):
        sym_uuid = new_uuid()
        pin_uuid = new_uuid()
        ref = f"#PWR{idx + 1:02d}"
        success = doc.add_power_symbol(
            ps.net_name,
            ps.x,
            ps.y,
            sym_uuid,
            pin_uuid,
            ref,
            project_name,
            angle=ps.angle,
            symbols_dir=symbols_dir,
        )
        if not success:
            if strict:
                raise UserError(
                    f"Power symbol not found in library: power:{ps.net_name}",
                    code=ErrorCode.SYMBOL_NOT_FOUND,
                    details={
                        "symbol": f"power:{ps.net_name}",
                        "net_name": ps.net_name,
                    },
                )
            # Fallback: global_label when power symbol is not in the library.
            power_point = (round(ps.x, 2), round(ps.y, 2))
            fallback_route, fallback_x, fallback_y = _label_attachment_plan(
                pin_point=(ps.x, ps.y),
                pin_angle=ps.angle,
                occupied_points=fallback_occupied_points - {power_point},
                protected=_ProtectedPointContext(
                    fallback_occupied_points - {power_point},
                ),
                prefer_perpendicular=True,
            )
            fallback_angle = (
                _segment_label_angle(fallback_route[-1])
                if fallback_route
                else _fallback_power_label_position(ps.x, ps.y, ps.angle)[2]
            )
            for segment in fallback_route:
                doc.add_wire(segment.x1, segment.y1, segment.x2, segment.y2, new_uuid())
                stats["wires"] += 1
            doc.add_global_label(
                ps.net_name,
                fallback_x,
                fallback_y,
                new_uuid(),
                angle=fallback_angle,
                shape="passive",
            )
            stats["global_labels"] = stats.get("global_labels", 0) + 1
            fallback_occupied_points.add((round(fallback_x, 2), round(fallback_y, 2)))
            for segment in fallback_route:
                fallback_occupied_points.add((round(segment.x1, 2), round(segment.y1, 2)))
                fallback_occupied_points.add((round(segment.x2, 2), round(segment.y2, 2)))
        else:
            stats["power_symbols"] = stats.get("power_symbols", 0) + 1

    for jpt in routing.junctions:
        doc.add_junction(jpt.x, jpt.y, new_uuid())
        stats["junctions"] = stats.get("junctions", 0) + 1

    for idx, bm in enumerate(routing.bind_markers):
        binding_text = "kicad-pcb:bind=" + json.dumps(
            {"ref": bm.ref, "pin": bm.pin, "net_name": bm.net_name},
            separators=(",", ":"),
            sort_keys=True,
        )
        doc.add_text(binding_text, -1200.0, -1500.0 - 10.0 * idx, hidden=True)
        stats["binding_markers"] += 1

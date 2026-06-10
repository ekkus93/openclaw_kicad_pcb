"""Wire quality and local direct wiring lint rules (LAY009–LAY011)."""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING

from ..component_types import is_power_net as _is_power_net
from ..component_types import power_rail_polarity
from ..sexpr.nodes import ListNode, StringNode
from ..sexpr.utils import find_first, walk
from ._sch_composition import _extract_symbol_positions
from .defs import _WARN, LintIssue
from .helpers import _collect_wire_segments, _float_from_atom

if TYPE_CHECKING:
    from ..circuit_ir import CircuitIR
    from ..sch_doc import SchematicDoc

# LAY009: excessive short wire jogs threshold. If a net has this fraction
# or more of its wire segments shorter than _LAY_SHORT_WIRE_THRESHOLD_MM,
# flag it as having too many short jogs (Phase 6.2).
_LAY_SHORT_WIRE_FRACTION_THRESHOLD: float = 0.50  # 50% of segments short
_LAY_SHORT_WIRE_THRESHOLD_MM: float = 5.0  # 5mm = short wire threshold

# LAY010: over-routed local connection thresholds. A 2-pin net with more
# than this many segments is considered over-routed (Phase 6.2).
_LAY_MAX_SEGMENTS_TWO_PIN: int = 4  # 2 stubs + 2 routing segments is reasonable

# LAY011: local direct wiring preference. A 2-pin net where both pins are
# within this Manhattan distance should prefer direct routing instead of
# multi-segment routing through long trunks (Phase 6.3).
_LAY_LOCAL_DIRECT_DIST_MM: float = 150.0  # ~5.9 inches (nearby = roughly same block)
_LAY_LOCAL_DIRECT_MIN_SEGMENTS: int = 3  # 3+ segments suggests over-routing


def _is_power_net_name(net_name: str) -> bool:
    return _is_power_net(net_name) or power_rail_polarity(net_name) is not None


def _collect_bind_markers(doc: SchematicDoc) -> dict[str, list[tuple[float, float]]]:
    bind_markers: dict[str, list[tuple[float, float]]] = {}
    for node in walk(doc.root):
        if not (isinstance(node, ListNode) and node.key == "text"):
            continue
        if len(node.items) < 2 or not isinstance(node.items[1], StringNode):
            continue

        text = node.items[1].value
        if not (text.startswith("OpenClaw:bind=") or text.startswith("kicad-pcb:bind=")):
            continue

        try:
            bind_data = json.loads(text.split("=", 1)[1] if "=" in text else "{}")
        except (json.JSONDecodeError, ValueError, TypeError):
            continue

        net_name = bind_data.get("net_name")
        if not isinstance(net_name, str) or not net_name:
            continue

        at_node = find_first(node, "at")
        if at_node is None or len(at_node.items) < 3:
            continue

        x = _float_from_atom(at_node.items[1])
        y = _float_from_atom(at_node.items[2])
        if x is None or y is None:
            continue

        bind_markers.setdefault(net_name, []).append((round(x, 2), round(y, 2)))

    return bind_markers


def _connected_segments_from_positions(
    segs: list[tuple[float, float, float, float]],
    bind_positions: list[tuple[float, float]],
) -> list[tuple[float, float, float, float]]:
    visited: set[tuple[float, float, float, float]] = set()
    queue: list[tuple[float, float]] = list(bind_positions)
    connected_segs: list[tuple[float, float, float, float]] = []

    while queue:
        current_x, current_y = queue.pop(0)
        current_pos = (round(current_x, 2), round(current_y, 2))

        for x1, y1, x2, y2 in segs:
            seg_tuple = (x1, y1, x2, y2)
            if seg_tuple in visited:
                continue

            p1 = (round(x1, 2), round(y1, 2))
            p2 = (round(x2, 2), round(y2, 2))
            endpoint: tuple[float, float] | None = None

            if abs(p1[0] - current_pos[0]) < 0.1 and abs(p1[1] - current_pos[1]) < 0.1:
                endpoint = p2
            elif abs(p2[0] - current_pos[0]) < 0.1 and abs(p2[1] - current_pos[1]) < 0.1:
                endpoint = p1

            if endpoint is None:
                continue

            connected_segs.append(seg_tuple)
            visited.add(seg_tuple)
            if endpoint not in queue and endpoint not in bind_positions:
                queue.append(endpoint)

    return connected_segs


def _build_net_segments(
    segs: list[tuple[float, float, float, float]],
    bind_markers: dict[str, list[tuple[float, float]]],
) -> dict[str, list[tuple[float, float, float, float]]]:
    net_segments: dict[str, list[tuple[float, float, float, float]]] = {}
    for net_name, bind_positions in bind_markers.items():
        if not bind_positions:
            continue
        connected = _connected_segments_from_positions(segs, bind_positions)
        if connected:
            net_segments[net_name] = connected
    return net_segments


def lint_wire_quality(
    doc: SchematicDoc,
    ir: CircuitIR,
) -> list[LintIssue]:
    """LAY009 & LAY010: warn about excessive short wire jogs and over-routed connections.

    Detects when nets have too many short wire segments (excessive jogs)
    or when simple 2-pin connections use unnecessarily complex routing.

    Parameters
    ----------
    doc:
        Schematic document :class:`~kicad_pcb.kicad_sch.SchematicDoc`.
    ir:
        Parsed :class:`~kicad_pcb.circuit_ir.CircuitIR`; used to identify
        nets and their pin counts.

    Returns
    -------
    list[LintIssue]
        LAY009 and/or LAY010 WARNINGs for nets with excessive short segments
        or over-routed local connections, otherwise an empty list.
    """
    issues: list[LintIssue] = []

    # Collect all wire segments from the schematic
    segs = _collect_wire_segments(doc.root.items)

    # Build a mapping of wire segments to nets using bind markers
    # Bind markers are in format: "OpenClaw:bind={json}"
    bind_markers: dict[str, list[tuple[float, float]]] = {}  # {net_name: [(x, y), ...]}
    for node in walk(doc.root):
        if not (isinstance(node, ListNode) and node.key == "text"):
            continue
        if len(node.items) >= 2 and isinstance(node.items[1], StringNode):
            text = node.items[1].value
            # Parse bind marker format: "OpenClaw:bind={...}"
            if text.startswith("OpenClaw:bind=") or text.startswith("kicad-pcb:bind="):
                try:
                    bind_json = text.split("=", 1)[1] if "=" in text else ""
                    bind_data = json.loads(bind_json)
                    net_name = bind_data.get("net_name", "")
                    # Get position from the bind marker (it's placed at the pin location)
                    at_node = find_first(node, "at")
                    if at_node and len(at_node.items) >= 3:
                        x = _float_from_atom(at_node.items[1])
                        y = _float_from_atom(at_node.items[2])
                        if x is not None and y is not None:
                            if net_name not in bind_markers:
                                bind_markers[net_name] = []
                            # Store rounded coordinates for matching
                            bind_markers[net_name].append((round(x, 2), round(y, 2)))
                except (json.JSONDecodeError, ValueError, KeyError):
                    pass

    # For each net, collect all wire segments where either endpoint matches a bind marker position
    # This is a heuristic since we don't have explicit wire-to-net mapping in the schematic
    net_segments: dict[str, list[tuple[float, float, float, float]]] = {}

    for net_name, positions in bind_markers.items():
        # For each wire segment, check if either endpoint is close to any bind marker for this net
        for x1, y1, x2, y2 in segs:
            x1r, y1r, x2r, y2r = round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)
            # Check if either endpoint matches a bind marker position (within tolerance)
            matches = False
            for bx, by in positions:
                if (abs(x1r - bx) < 5.0 and abs(y1r - by) < 5.0) or (
                    abs(x2r - bx) < 5.0 and abs(y2r - by) < 5.0
                ):
                    matches = True
                    break

            if matches:
                if net_name not in net_segments:
                    net_segments[net_name] = []
                # Avoid duplicates
                if (x1, y1, x2, y2) not in net_segments[net_name]:
                    net_segments[net_name].append((x1, y1, x2, y2))

    # -------------------------------------------------------------------------
    # LAY009 — excessive short wire jogs (per net)
    # -------------------------------------------------------------------------
    for net_name, segments in net_segments.items():
        if not segments or len(segments) < 3:
            continue

        # Count short segments in this net
        short_count = 0
        for x1, y1, x2, y2 in segments:
            length = math.hypot(x2 - x1, y2 - y1)
            if length <= _LAY_SHORT_WIRE_THRESHOLD_MM:
                short_count += 1

        # Check if too many segments are short
        short_fraction = short_count / len(segments)
        if short_fraction >= _LAY_SHORT_WIRE_FRACTION_THRESHOLD:
            issues.append(
                LintIssue(
                    _WARN,
                    "LAY009",
                    f"Net '{net_name}' has {short_count}/{len(segments)} short wire segments "
                    f"({short_fraction:.0%}) below {_LAY_SHORT_WIRE_THRESHOLD_MM}mm; "
                    "consider simplifying routing or using more direct connections.",
                    path="kicad_sch/wire",
                )
            )

    # -------------------------------------------------------------------------
    # LAY010 — over-routed local connection (2-pin nets with too many segments)
    # -------------------------------------------------------------------------
    for net in ir.nets:
        # Skip power nets (they typically use hub/spine routing)
        if _is_power_net_name(net.name):
            continue

        # Only check 2-pin nets (local connections)
        if len(net.pins) == 2:
            segments = net_segments.get(net.name, [])
            if len(segments) > _LAY_MAX_SEGMENTS_TWO_PIN:
                # Calculate total wire length
                total_length = sum(math.hypot(x2 - x1, y2 - y1) for x1, y1, x2, y2 in segments)

                issues.append(
                    LintIssue(
                        _WARN,
                        "LAY010",
                        f"Net '{net.name}' is a 2-pin connection but uses "
                        f"{len(segments)} wire segments "
                        f"(threshold {_LAY_MAX_SEGMENTS_TWO_PIN}, "
                        f"total length {total_length:.1f}mm); "
                        "consider using more direct routing for local connections.",
                        path="kicad_sch/wire",
                    )
                )

    return issues


def lint_local_direct_wiring(doc: SchematicDoc, ir: CircuitIR) -> list[LintIssue]:
    """
    LAY011: Detect nearby 2-pin nets that could/should use direct routing.

    Phase 6.3 concern: if two pins are within `_LAY_LOCAL_DIRECT_DIST_MM` Manhattan
    distance (roughly same block), they should prefer direct L-routing instead of
    multi-segment routing through long trunks or hubs.

    Returns list of LintIssue for 2-pin connections that are routed with too many
    segments for their physical proximity.
    """
    issues: list[LintIssue] = []
    segs = _collect_wire_segments(doc.root.items)
    if not segs:
        return issues

    try:
        comp_positions = _extract_symbol_positions(doc)
    except (AttributeError, KeyError, TypeError, ValueError):
        return issues

    bind_markers = _collect_bind_markers(doc)
    net_segments = _build_net_segments(segs, bind_markers)

    for net in ir.nets:
        if _is_power_net_name(net.name):
            continue
        if len(net.pins) != 2:
            continue

        segments = net_segments.get(net.name, [])
        if len(segments) < _LAY_LOCAL_DIRECT_MIN_SEGMENTS:
            continue

        try:
            pin1 = net.pins[0]
            pin2 = net.pins[1]
            if pin1.ref not in comp_positions or pin2.ref not in comp_positions:
                continue

            x1, y1 = comp_positions[pin1.ref]
            x2, y2 = comp_positions[pin2.ref]
            manhattan_dist = abs(x2 - x1) + abs(y2 - y1)
            if manhattan_dist > _LAY_LOCAL_DIRECT_DIST_MM:
                continue

            total_length = sum(math.hypot(sx2 - sx1, sy2 - sy1) for sx1, sy1, sx2, sy2 in segments)
            issues.append(
                LintIssue(
                    _WARN,
                    "LAY011",
                    f"Net '{net.name}' connects nearby pins "
                    f"({manhattan_dist:.0f}mm apart) but uses "
                    f"{len(segments)} wire segments "
                    f"(length {total_length:.1f}mm); "
                    "prefer simple L-routing for local connections to avoid trunk congestion.",
                    path="kicad_sch/wire",
                )
            )
        except (AttributeError, TypeError, KeyError):
            continue

    return issues

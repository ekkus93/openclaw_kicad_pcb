"""Orthogonal net routing for kicad_pcb managed schematics.

Routes 2-pin nets directly with L-shaped wire segments when both pins
are known and within :data:`MAX_DIRECT_DIST_MM` (Manhattan distance,
stub-end to stub-end).  For nets with 3–:data:`_HUB_MAX_DEGREE` pins the
default strategy is spine routing (bus-style wire with T-junction taps),
which produces cleaner schematics than the per-pin stub+label fallback.
All other nets fall back to the classic wire-stub + net-label-per-pin
approach.

Public API
----------
:func:`route_nets`             — compute routing decisions for all nets in a CircuitIR.
:func:`write_routing`          — emit computed decisions into a SchematicDoc.
:class:`LabelPolicy`           — policy dataclass controlling label deduplication.
:data:`DEFAULT_LABEL_POLICY`   — default policy (max 2 local labels, 4 global per net).
:class:`PowerSymbolPlacement`  — placed power symbol (GND/VCC etc.), emitted for power nets.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from .block_detection import BlockLayout, BlockRole, is_input_like_role, is_output_like_role
from .component_types import (
    component_type as _component_type,
)
from .component_types import (
    is_ground_like_name,
    power_rail_polarity,
)
from .component_types import (
    is_power_net as _base_is_power_net_name,
)
from .errors import ErrorCode, UserError
from .layout import ORIGIN_X, ORIGIN_Y

if TYPE_CHECKING:
    from .circuit_ir import CircuitIR, PinRefIR
    from .sch_doc import SchematicDoc

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
WIRE_EXTEND_MM: float = 5.08  # pin stub length — 200 mil (one KiCad grid unit)
# Raised from 120.0 → 200.0 to accommodate the wider layout scale
# (ranksep=2.5 × SCALE_MM_PER_GV=24.0 puts simple 2-component circuits up to
# ~150 mm apart when routed without tier information).
MAX_DIRECT_DIST_MM: float = 200.0  # Manhattan distance cap for direct routing
# When tiers are provided, a wire longer than this triggers net-label routing
# even between adjacent-tier components.  Separate from MAX_DIRECT_DIST_MM so
# the Manhattan fallback (no tiers) is not affected.  (Rule §4)
# Raised from 30.0 → 70.0 to accommodate the wider layout scale
# (ranksep=2.5 × SCALE_MM_PER_GV=24.0 = 60 mm between adjacent tiers).
MAX_DIRECT_WIRE_MM: float = 70.0
# Approximate half-edge of a KiCad symbol bounding box (200 mil = 5.08 mm).
# Used by detect_body_crossings to detect component-body wire crossings.
SYMBOL_HALF_SIZE_MM: float = 5.08

# Nets with degree > this threshold fall back to global-label style (avoids
# spaghetti wiring for busses and power rails).
_HUB_MAX_DEGREE: int = 6

# Maximum Euclidean distance (mm) for grouping power pins into a shared
# power symbol cluster.  Pins within this radius share one power symbol,
# reducing visual ground/power clutter (Phase 5.1).
_POWER_CLUSTER_RADIUS_MM: float = 40.0
_POWER_LABEL_CLEARANCE_MM: float = 6.35

LabelModeName = Literal["minimal", "debug", "always-show-important-labels"]


@dataclass(frozen=True)
class LabelPolicy:
    """Policy controlling label deduplication in :func:`route_nets`.

    Capping labels per net reduces schematic clutter and keeps the LAY001
    lint rule (warn when a label appears > 3 times) from triggering on
    high-fanout signal nets.

    Attributes
    ----------
    max_labels_per_net:
        Maximum number of local :class:`NetLabel` nodes emitted for any
        single non-power net falling through to the label-route fallback.
        Pins beyond this limit still receive a stub wire and bind marker;
        only the visible label is suppressed.  Default: ``2`` (one label
        per connection endpoint, the minimum KiCad requires for
        schematic connectivity).
    max_global_labels_per_net:
        Maximum number of :class:`GlobalLabelPlacement` nodes emitted for
        a single *non-power* high-degree net (see :data:`_HUB_MAX_DEGREE`).
        Power nets use :class:`PowerSymbolPlacement` instead and are not
        subject to this cap.  Default: ``4``.
    mode_name:
        Human-readable label-mode identifier used by CLI/result/debug output.
    force_important_labels:
        When ``True``, preserve one visible label for structurally important
        signal nets even when routing would otherwise be label-free.
    force_all_signal_labels:
        When ``True``, preserve one visible label for every non-power signal
        net and relax label caps for debugging.
    """

    max_labels_per_net: int = 2
    max_global_labels_per_net: int = 4
    mode_name: LabelModeName = "minimal"
    force_important_labels: bool = False
    force_all_signal_labels: bool = False


MINIMAL_LABEL_POLICY = LabelPolicy(mode_name="minimal")
DEBUG_LABEL_POLICY = LabelPolicy(
    max_labels_per_net=999,
    max_global_labels_per_net=999,
    mode_name="debug",
    force_important_labels=True,
    force_all_signal_labels=True,
)
IMPORTANT_LABEL_POLICY = LabelPolicy(
    mode_name="always-show-important-labels",
    force_important_labels=True,
)
LABEL_MODE_POLICIES: dict[LabelModeName, LabelPolicy] = {
    "minimal": MINIMAL_LABEL_POLICY,
    "debug": DEBUG_LABEL_POLICY,
    "always-show-important-labels": IMPORTANT_LABEL_POLICY,
}
DEFAULT_LABEL_POLICY: LabelPolicy = MINIMAL_LABEL_POLICY


@dataclass(frozen=True)
class RoutingHeuristicPolicy:
    """Policy seam for analog-specific routing heuristics.

    The generic router still owns direct, hub, spine, lane, and label routing.
    This policy only governs the analog-audio special cases layered on top of
    those generic strategies so later profile work can swap or disable them
    without rewriting :func:`route_nets`.
    """

    enable_compact_output_tails: bool = True
    enable_compact_local_ground_clusters: bool = True
    enable_small_analog_local_routing: bool = False

    def should_skip_shared_lane_plan(
        self,
        endpoints: list[tuple[float, float]],
        inferred_plan: SharedLanePlan | None,
    ) -> bool:
        """Return True when the analog compact-tail rule should override a lane."""
        if not self.enable_compact_output_tails or inferred_plan is None:
            return False
        return _should_skip_inferred_lane_plan(endpoints, inferred_plan)

    def should_prefer_small_analog_chain(
        self,
        endpoints: list[tuple[float, float]],
        *,
        inferred_plan: SharedLanePlan | None = None,
        refs: tuple[str, ...] = (),
    ) -> bool:
        """Return True when compact local analog nets should avoid a trunk route."""
        if not self.enable_small_analog_local_routing:
            return False
        return _prefer_small_analog_chain_route(
            endpoints,
            inferred_plan=inferred_plan,
            refs=refs,
        )

    def route_compact_signal_tail(
        self,
        endpoints: list[tuple[float, float]],
        *,
        inferred_plan: SharedLanePlan | None,
        protected_points: set[tuple[float, float]] | None = None,
        positions: Mapping[str, tuple[float, float, float | None]] | None = None,
    ) -> tuple[list[WireSegment], list[JunctionPoint]] | None:
        """Return the analog compact-tail route when that policy applies."""
        if not self.enable_compact_output_tails or inferred_plan is None:
            return None
        if _is_compact_horizontal_stage_tail(endpoints, axis=inferred_plan.axis):
            return _compact_horizontal_stage_tail_route(endpoints)
        if not self.should_skip_shared_lane_plan(endpoints, inferred_plan):
            return None
        return _best_compact_vertical_tail_route(
            endpoints,
            preferred_coordinate=inferred_plan.coordinate,
            protected_points=protected_points,
            positions=positions,
        )

    def route_compact_power_cluster(
        self,
        *,
        net_name: str,
        cluster: list[tuple[PinRefIR, tuple[float, float, float]]],
        positions: Mapping[str, tuple[float, float, float | None]] | None = None,
    ) -> tuple[list[WireSegment], list[JunctionPoint], tuple[float, float]] | None:
        """Return the analog compact power-cluster route when that policy applies."""
        if not self.enable_compact_local_ground_clusters:
            return None
        if net_name.upper() == "GND":
            decoupling_ground_cluster = _compact_local_decoupling_ground_cluster_route(
                cluster,
                positions=positions,
            )
            if decoupling_ground_cluster is not None:
                return decoupling_ground_cluster
            return _compact_local_ground_cluster_route(cluster, positions=positions)
        if power_rail_polarity(net_name) is None:
            return None
        return _compact_local_decoupling_power_cluster_route(
            net_name,
            cluster,
            positions=positions,
        )


DEFAULT_ROUTING_HEURISTIC_POLICY: RoutingHeuristicPolicy = RoutingHeuristicPolicy()


_SIGNAL_LABEL_ROLE_PRIORITY: dict[BlockRole, int] = {
    BlockRole.INPUT: 0,
    BlockRole.INTERSTAGE: 0,
    BlockRole.OUTPUT: 0,
    BlockRole.PRECONDITIONING: 1,
    BlockRole.OUTPUT_CONDITIONING: 1,
    BlockRole.BUFFER_STAGE: 2,
    BlockRole.OPAMP_CORE: 3,
    BlockRole.FEEDBACK: 4,
    BlockRole.POWER_ENTRY: 5,
    BlockRole.DECOUPLING: 5,
}

_CONNECTOR_LABEL_ROLE_PRIORITY: dict[BlockRole, int] = {
    BlockRole.INPUT: 0,
    BlockRole.OUTPUT: 0,
    BlockRole.PRECONDITIONING: 1,
    BlockRole.OUTPUT_CONDITIONING: 1,
    BlockRole.INTERSTAGE: 2,
    BlockRole.BUFFER_STAGE: 2,
    BlockRole.OPAMP_CORE: 3,
    BlockRole.FEEDBACK: 4,
    BlockRole.POWER_ENTRY: 5,
    BlockRole.DECOUPLING: 5,
}

_FEEDBACK_LABEL_ROLE_PRIORITY: dict[BlockRole, int] = {
    BlockRole.FEEDBACK: 0,
    BlockRole.OPAMP_CORE: 1,
    BlockRole.BUFFER_STAGE: 2,
    BlockRole.INTERSTAGE: 3,
    BlockRole.PRECONDITIONING: 4,
    BlockRole.OUTPUT_CONDITIONING: 4,
    BlockRole.INPUT: 5,
    BlockRole.OUTPUT: 5,
    BlockRole.POWER_ENTRY: 6,
    BlockRole.DECOUPLING: 6,
}


def _is_power_net_name(net_name: str) -> bool:
    """Return True for routed power rails, including VPLUS/VMINUS aliases."""
    return _base_is_power_net_name(net_name) or power_rail_polarity(net_name) is not None


def _tier_distance(ref_a: str, ref_b: str, tiers: dict[str, int]) -> int:
    """Return the absolute tier-index difference between two component refs."""
    return abs(tiers.get(ref_a, 0) - tiers.get(ref_b, 0))


def _wire_crosses_box(  # noqa: PLR0913
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    bx: float,
    by: float,
    half: float,
) -> bool:
    """Return True when segment (x1,y1)→(x2,y2) intersects the axis-aligned
    bounding box centred at *(bx, by)* with half-edge *half*.

    Only orthogonal (horizontal and vertical) segments are considered;
    diagonal segments always return False.
    """
    lx, rx = bx - half, bx + half
    ty, bot = by - half, by + half  # top (smaller y in KiCad) / bottom
    if math.isclose(y1, y2, abs_tol=0.01):  # horizontal segment
        seg_lx, seg_rx = min(x1, x2), max(x1, x2)
        return ty <= y1 <= bot and seg_lx < rx and seg_rx > lx
    if math.isclose(x1, x2, abs_tol=0.01):  # vertical segment
        seg_ty, seg_bot = min(y1, y2), max(y1, y2)
        return lx <= x1 <= rx and seg_ty < bot and seg_bot > ty
    return False


def _point_in_or_on_box(x: float, y: float, bx: float, by: float, half: float) -> bool:
    """Return True when point *(x, y)* lies inside or on the box boundary."""
    return (bx - half) <= x <= (bx + half) and (by - half) <= y <= (by + half)


def _compact_cluster_detour_x(
    ref: str,
    center_x: float,
    *,
    crossing_own_member: bool = False,
) -> float:
    """Return a conservative compact-cluster detour X coordinate.

    The generic 10.16 mm sidestep is enough for broad obstacle avoidance, but
    compact decoupling clusters can still place a lane directly on a symbol edge.
    KiCad's net export has proven sensitive to that geometry for local power
    rails, so own-member spans and IC-adjacent spans get one extra 5.08 mm step.
    """
    multiplier = 3 if crossing_own_member or _component_type(ref) == "ic" else 2
    return center_x - (multiplier * SYMBOL_HALF_SIZE_MM)


def _detour_segment(
    seg: WireSegment,
    bx: float,
    by: float,
    half: float,
) -> list[WireSegment]:
    """Replace *seg* with a detour path that bypasses the AABB at *(bx, by)*.

    For a horizontal wire the detour routes **above** the box
    (``y_new = by - half - half``); for a vertical wire the detour routes
    **to the left** (``x_new = bx - half - half``).

    The returned list contains up to five orthogonal segments forming a
    rectangular jog around the obstacle.
    """
    if math.isclose(seg.y1, seg.y2, abs_tol=0.01):  # horizontal
        detour_y = by - half - half  # one symbol-height above box top
        left_edge = bx - half
        right_edge = bx + half
        moving_right = seg.x2 >= seg.x1
        enter_x = left_edge if moving_right else right_edge
        exit_x = right_edge if moving_right else left_edge
        return [
            WireSegment(seg.x1, seg.y1, enter_x, seg.y1),
            WireSegment(enter_x, seg.y1, enter_x, detour_y),
            WireSegment(enter_x, detour_y, exit_x, detour_y),
            WireSegment(exit_x, detour_y, exit_x, seg.y2),
            WireSegment(exit_x, seg.y2, seg.x2, seg.y2),
        ]
    if math.isclose(seg.x1, seg.x2, abs_tol=0.01):  # vertical
        detour_x = bx - half - half  # one symbol-width to the left of box
        top_edge = by - half
        bottom_edge = by + half
        moving_down = seg.y2 >= seg.y1
        enter_y = top_edge if moving_down else bottom_edge
        exit_y = bottom_edge if moving_down else top_edge
        return [
            WireSegment(seg.x1, seg.y1, seg.x1, enter_y),
            WireSegment(seg.x1, enter_y, detour_x, enter_y),
            WireSegment(detour_x, enter_y, detour_x, exit_y),
            WireSegment(detour_x, exit_y, seg.x1, exit_y),
            WireSegment(seg.x1, exit_y, seg.x2, seg.y2),
        ]
    return [seg]  # diagonal — no detour (uncommon in schematic routing)


def detect_body_crossings(
    wires: list[WireSegment],
    positions: Mapping[str, tuple[float, float, float | None]],
) -> list[WireSegment]:
    """Reroute wire segments that pass through a component bounding box.

    Each component in *positions* is approximated as a
    ``SYMBOL_HALF_SIZE_MM``-square bounding box centred on its KiCad
    coordinates.  Any wire segment that intersects such a box is replaced with
    a rectangular detour path (see :func:`_detour_segment`); all other
    segments pass through unchanged.

    Parameters
    ----------
    wires:
        Current list of :class:`WireSegment` objects from :func:`route_nets`.
    positions:
        ``{ref: (x, y, rotation)}`` position map from the layout engine.

    Returns a new wire list with all body crossings rerouted.
    """
    bboxes = [(pos[0], pos[1]) for pos in positions.values()]
    result: list[WireSegment] = []
    for seg in wires:
        replaced = False
        for bx, by in bboxes:
            if _point_in_or_on_box(
                seg.x1, seg.y1, bx, by, SYMBOL_HALF_SIZE_MM
            ) or _point_in_or_on_box(seg.x2, seg.y2, bx, by, SYMBOL_HALF_SIZE_MM):
                continue
            if _wire_crosses_box(seg.x1, seg.y1, seg.x2, seg.y2, bx, by, SYMBOL_HALF_SIZE_MM):
                result.extend(_detour_segment(seg, bx, by, SYMBOL_HALF_SIZE_MM))
                replaced = True
                break  # one detour per segment; further crossings resolved on next call
        if not replaced:
            result.append(seg)
    return result


def _cluster_power_pins(
    pins: list[tuple[PinRefIR, tuple[float, float, float]]],
    radius: float = _POWER_CLUSTER_RADIUS_MM,
) -> list[list[tuple[PinRefIR, tuple[float, float, float]]]]:
    """Group power pins by proximity into clusters sharing a symbol.

    Uses simple greedy clustering: each unclustered pin either joins the
    nearest existing cluster (if within *radius*) or starts a new cluster.

    Parameters
    ----------
    pins:
        List of ``(pin_ref, (x, y, angle))`` tuples for pins on a power net.
    radius:
        Maximum Euclidean distance (mm) for pins to share a cluster.

    Returns
    -------
    list[list[tuple]]
        List of clusters, where each cluster is a list of pin tuples.

    Notes
    -----
    Phase 5.1: reduces visual ground/power clutter by placing one power
    symbol per cluster instead of one per pin.
    """
    if not pins:
        return []

    clusters: list[list[tuple[PinRefIR, tuple[float, float, float]]]] = []

    for pin, (px, py, pangle) in pins:
        # Find the nearest cluster centroid within radius
        best_cluster_idx: int | None = None
        best_dist = float("inf")

        for idx, cluster in enumerate(clusters):
            # Compute cluster centroid
            cx = _snap_grid(sum(cpx for _, (cpx, _, _) in cluster) / len(cluster))
            cy = _snap_grid(sum(cpy for _, (_, cpy, _) in cluster) / len(cluster))
            dist = math.hypot(px - cx, py - cy)

            if dist < radius and dist < best_dist:
                best_dist = dist
                best_cluster_idx = idx

        # Add to nearest cluster or create new one
        if best_cluster_idx is not None:
            clusters[best_cluster_idx].append((pin, (px, py, pangle)))
        else:
            clusters.append([(pin, (px, py, pangle))])

    return clusters


# ---------------------------------------------------------------------------
# Data classes — immutable routing decisions
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WireSegment:
    """A single wire segment from (x1,y1) to (x2,y2) in mm."""

    x1: float
    y1: float
    x2: float
    y2: float


@dataclass(frozen=True)
class NetLabel:
    """A net-name label placed at (x,y) with the given *angle* (degrees)."""

    name: str
    x: float
    y: float
    angle: int


@dataclass(frozen=True)
class BindMarker:
    """Hidden off-canvas text binding a (ref, pin) pair to a net name."""

    ref: str
    pin: str
    net_name: str


@dataclass(frozen=True)
class PinAnchor:
    """Unit-local anchor ownership plus schematic-space endpoint geometry."""

    ref: str
    pin: str
    x: float
    y: float
    angle: float
    unit: int | None = None


@dataclass(frozen=True)
class GlobalLabelPlacement:
    """A global-label node placed at *(x, y)*.  Used for high-degree
    non-power nets instead of per-pin local labels."""

    name: str
    x: float
    y: float
    angle: int


@dataclass(frozen=True)
class PowerSymbolPlacement:
    """A KiCad power symbol placed at *(x, y)* with a stub wire.

    Used in place of :class:`GlobalLabelPlacement` for power nets (GND,
    VCC, etc.) when a matching ``power:<net_name>`` library symbol is
    available.  The symbol's single connection pin is at *(x, y)* — a
    stub wire should end here.  When the library symbol is unavailable,
    :func:`write_routing` falls back to a ``global_label`` node.
    """

    net_name: str  # e.g. "GND"  — also the KiCad Value of the placed symbol
    x: float
    y: float
    angle: int = 0


@dataclass(frozen=True)
class _ProtectedPointContext:
    points: set[tuple[float, float]]
    shared_points: set[tuple[float, float]] | None = None


@dataclass(frozen=True)
class JunctionPoint:
    """A schematic junction marker at *(x, y)*."""

    x: float
    y: float


@dataclass(frozen=True)
class SharedLanePlan:
    """Planner output for a local shared lane.

    The optional orthogonal bounds let the planner keep a lane on the same
    X/Y coordinate while shortening the visible trunk to the portion that
    actually reads as the downstream continuation.
    """

    axis: str
    coordinate: float
    min_orthogonal: float | None = None
    max_orthogonal: float | None = None


@dataclass(frozen=True)
class LadderLanePlannerContext:
    """Grouped-ladder planning inputs shared across one local neighborhood."""

    candidate_degree: dict[str, int]
    shared_lane_by_net: dict[str, tuple[str, float]]
    endpoints_by_net: dict[str, list[tuple[float, float]]]
    refs_by_net: dict[str, tuple[str, ...]]
    connector_entry_x_by_net: dict[str, float]
    heuristic_policy: RoutingHeuristicPolicy = DEFAULT_ROUTING_HEURISTIC_POLICY


@dataclass
class NetRouting:
    """Aggregated routing decisions for a whole schematic."""

    wires: list[WireSegment] = field(default_factory=list)
    labels: list[NetLabel] = field(default_factory=list)
    global_labels: list[GlobalLabelPlacement] = field(default_factory=list)
    power_symbols: list[PowerSymbolPlacement] = field(default_factory=list)
    junctions: list[JunctionPoint] = field(default_factory=list)
    bind_markers: list[BindMarker] = field(default_factory=list)
    route_decisions: list[RouteDecision] = field(default_factory=list)


@dataclass(frozen=True)
class RouteDecision:
    """Debug summary of the final routing strategy selected for one net."""

    net_name: str
    classification: Literal[
        "power",
        "local_decoupling",
        "shunt_ground",
        "connector_only",
        "connector_attachment",
        "signal_chain",
        "feedback",
        "generic_signal",
    ]
    strategy: str
    pin_count: int
    known_pin_count: int
    unknown_pin_count: int
    use_bus: bool
    heuristic_override: str | None = None


def _net_member_roles(
    refs: tuple[str, ...],
    block_layout: BlockLayout | None,
) -> set[BlockRole]:
    """Return the set of block roles present on *refs* when available."""

    if block_layout is None:
        return set()
    return {
        assignment.role
        for ref in refs
        if (assignment := block_layout.assignments.get(ref)) is not None
    }


def _classify_routing_net_from_roles(
    refs: tuple[str, ...],
    *,
    block_layout: BlockLayout | None,
    has_connector: bool,
) -> (
    Literal[
        "connector_attachment",
        "signal_chain",
        "feedback",
        "generic_signal",
    ]
    | None
):
    """Return a structural routing class derived from block roles when possible."""

    roles = _net_member_roles(refs, block_layout)
    if not roles:
        return None

    if BlockRole.FEEDBACK in roles:
        return "feedback"

    if (
        has_connector
        and len(refs) <= 3
        and any(is_input_like_role(role) or is_output_like_role(role) for role in roles)
    ):
        return "connector_attachment"

    signal_roles = {
        BlockRole.INPUT,
        BlockRole.PRECONDITIONING,
        BlockRole.OPAMP_CORE,
        BlockRole.INTERSTAGE,
        BlockRole.BUFFER_STAGE,
        BlockRole.OUTPUT,
        BlockRole.OUTPUT_CONDITIONING,
    }
    if roles & signal_roles:
        return "signal_chain"

    return None


def _classify_routing_net(
    net_name: str,
    refs: tuple[str, ...],
    *,
    block_layout: BlockLayout | None = None,
) -> Literal[
    "power",
    "local_decoupling",
    "shunt_ground",
    "connector_only",
    "connector_attachment",
    "signal_chain",
    "feedback",
    "generic_signal",
]:
    """Return a first-class routing taxonomy for one net.

    The taxonomy is intentionally pragmatic: it formalizes the categories the
    router already treats differently in practice and provides a stable debug
    surface for later policy work.
    """
    component_kinds = tuple(_component_type(ref) for ref in refs)
    has_connector = any(kind == "connector" for kind in component_kinds)
    has_ic = any(kind == "ic" for kind in component_kinds)
    has_capacitor = any(ref.upper().startswith("C") for ref in refs)
    all_connectors = bool(refs) and all(kind == "connector" for kind in component_kinds)
    all_passive_or_connector = bool(refs) and all(
        kind in {"passive", "connector"} for kind in component_kinds
    )
    upper_name = net_name.upper()

    classification: Literal[
        "power",
        "local_decoupling",
        "shunt_ground",
        "connector_only",
        "connector_attachment",
        "signal_chain",
        "feedback",
        "generic_signal",
    ] = "generic_signal"

    if _is_power_net_name(net_name):
        if is_ground_like_name(net_name) and len(refs) <= 3 and all_passive_or_connector:
            classification = "shunt_ground"
        elif len(refs) <= 3 and has_capacitor and has_ic:
            classification = "local_decoupling"
        else:
            classification = "power"
    elif all_connectors:
        classification = "connector_only"
    else:
        structural_classification = _classify_routing_net_from_roles(
            refs,
            block_layout=block_layout,
            has_connector=has_connector,
        )
        if structural_classification is not None:
            return structural_classification

        if any(token in upper_name for token in ("INV", "FB", "FEEDBACK")):
            classification = "feedback"
        elif has_connector and len(refs) <= 3:
            classification = "connector_attachment"
        elif (
            has_ic
            or has_connector
            or any(token in upper_name for token in ("IN", "OUT", "BUF", "STAGE", "VOL", "HP"))
        ):
            classification = "signal_chain"

    return classification


def _classification_prefers_compact_tail(
    classification: Literal[
        "power",
        "local_decoupling",
        "shunt_ground",
        "connector_only",
        "connector_attachment",
        "signal_chain",
        "feedback",
        "generic_signal",
    ],
) -> bool:
    """Return True when a routing class should try the compact tail heuristic."""
    return classification in {"connector_attachment", "signal_chain"}


def _classification_prefers_local_chain(
    classification: Literal[
        "power",
        "local_decoupling",
        "shunt_ground",
        "connector_only",
        "connector_attachment",
        "signal_chain",
        "feedback",
        "generic_signal",
    ],
) -> bool:
    """Return True when a routing class should prefer a compact local chain."""
    return classification in {"connector_attachment", "signal_chain", "feedback"}


def _classification_prefers_short_local_direct_route(
    classification: Literal[
        "power",
        "local_decoupling",
        "shunt_ground",
        "connector_only",
        "connector_attachment",
        "signal_chain",
        "feedback",
        "generic_signal",
    ],
) -> bool:
    """Return True when a routing class should stay locally wired before using labels."""
    return classification in {"connector_attachment", "signal_chain", "feedback"}


def _label_role_priority(
    role: BlockRole | None,
    *,
    classification: Literal[
        "power",
        "local_decoupling",
        "shunt_ground",
        "connector_only",
        "connector_attachment",
        "signal_chain",
        "feedback",
        "generic_signal",
    ],
) -> int:
    """Return the sort priority for visible label placement on a routed net."""

    if role is None:
        return 99
    if classification == "feedback":
        return _FEEDBACK_LABEL_ROLE_PRIORITY.get(role, 99)
    if classification == "connector_attachment":
        return _CONNECTOR_LABEL_ROLE_PRIORITY.get(role, 99)
    if classification == "signal_chain":
        return _SIGNAL_LABEL_ROLE_PRIORITY.get(role, 99)
    return 99


def _name_suggests_important_signal(net_name: str) -> bool:
    """Return True when *net_name* reads like a user-meaningful stage handoff."""

    upper_name = net_name.upper()
    if any(token in upper_name for token in ("RAW", "INV", "FB", "FEEDBACK", "AFTER_")):
        return False
    return any(
        token in upper_name
        for token in (
            "LEFT_IN",
            "RIGHT_IN",
            "_IN",
            "IN_",
            "VOL",
            "STAGE",
            "BUF",
            "HP",
            "_OUT",
            "OUT_",
        )
    )


def _roles_mark_important_display_seam(
    roles: set[BlockRole],
    *,
    has_connector: bool,
) -> bool:
    """Return True when *roles* form a stage seam worth keeping visibly labeled."""

    if has_connector and any(
        is_input_like_role(role) or is_output_like_role(role) for role in roles
    ):
        return True
    if BlockRole.INTERSTAGE in roles:
        return True
    if BlockRole.OPAMP_CORE in roles and BlockRole.PRECONDITIONING in roles:
        return True
    return BlockRole.INPUT in roles and BlockRole.PRECONDITIONING in roles


def _net_is_important_for_display(
    net_name: str,
    refs: tuple[str, ...],
    *,
    block_layout: BlockLayout | None,
    classification: Literal[
        "power",
        "local_decoupling",
        "shunt_ground",
        "connector_only",
        "connector_attachment",
        "signal_chain",
        "feedback",
        "generic_signal",
    ],
) -> bool:
    """Return True when a signal net should stay visibly labeled in important mode."""

    roles = _net_member_roles(refs, block_layout)
    if roles:
        return _roles_mark_important_display_seam(
            roles,
            has_connector=any(_component_type(ref) == "connector" for ref in refs),
        )

    if classification not in {"connector_attachment", "signal_chain"}:
        return False

    return _name_suggests_important_signal(net_name)


def _should_promote_visible_label(
    net_name: str,
    refs: tuple[str, ...],
    *,
    block_layout: BlockLayout | None,
    classification: Literal[
        "power",
        "local_decoupling",
        "shunt_ground",
        "connector_only",
        "connector_attachment",
        "signal_chain",
        "feedback",
        "generic_signal",
    ],
    policy: LabelPolicy,
) -> bool:
    """Return True when the selected label mode should add a visible label."""

    if classification in {"power", "local_decoupling", "shunt_ground"}:
        return False
    if policy.force_all_signal_labels:
        return True
    if not policy.force_important_labels:
        return False
    return _net_is_important_for_display(
        net_name,
        refs,
        block_layout=block_layout,
        classification=classification,
    )


def _prioritize_label_candidates(
    known: list[tuple[PinRefIR, tuple[float, float, float]]],
    *,
    block_layout: BlockLayout | None,
    classification: Literal[
        "power",
        "local_decoupling",
        "shunt_ground",
        "connector_only",
        "connector_attachment",
        "signal_chain",
        "feedback",
        "generic_signal",
    ],
) -> list[tuple[PinRefIR, tuple[float, float, float]]]:
    """Return *known* reordered so capped labels favor structurally important seams."""

    if block_layout is None or classification not in {
        "connector_attachment",
        "signal_chain",
        "feedback",
    }:
        return known

    scored: list[tuple[int, int, tuple[PinRefIR, tuple[float, float, float]]]] = []
    for index, candidate in enumerate(known):
        pin_ref, _endpoint = candidate
        scored.append(
            (
                _label_role_priority(
                    block_layout.get_role(pin_ref.ref),
                    classification=classification,
                ),
                index,
                candidate,
            )
        )
    return [candidate for _priority, _index, candidate in sorted(scored)]


@dataclass(frozen=True)
class _VisibleLabelPromotion:
    net_name: str
    refs: tuple[str, ...]
    block_layout: BlockLayout | None
    classification: Literal[
        "power",
        "local_decoupling",
        "shunt_ground",
        "connector_only",
        "connector_attachment",
        "signal_chain",
        "feedback",
        "generic_signal",
    ]
    label_candidates: list[tuple[PinRefIR, tuple[float, float, float]]]


def _append_promoted_visible_label(
    routing: NetRouting,
    *,
    promotion: _VisibleLabelPromotion,
    policy: LabelPolicy,
    protected_points: set[tuple[float, float]] | None = None,
    shared_protected_points: set[tuple[float, float]] | None = None,
) -> None:
    """Add one visible label when the selected mode promotes this signal net."""

    if not promotion.label_candidates:
        return

    promoted_candidate = promotion.label_candidates[0]
    _pin_ref, (wx, wy, wa) = promoted_candidate
    if not _should_promote_visible_label(
        promotion.net_name,
        promotion.refs,
        block_layout=promotion.block_layout,
        classification=promotion.classification,
        policy=policy,
    ):
        return

    label_anchor = _safe_stub_label_anchor(
        pin_point=(wx, wy),
        pin_angle=wa,
        occupied_label_points=_occupied_label_points(routing),
        protected_points=protected_points,
        shared_protected_points=shared_protected_points,
    )
    if label_anchor is None:
        occupied_points = _occupied_wire_points(routing.wires) | _occupied_label_points(routing)
        label_route, ex, ey = _label_attachment_plan(
            pin_point=(wx, wy),
            pin_angle=wa,
            occupied_points=occupied_points,
            protected=(
                _ProtectedPointContext(protected_points, shared_protected_points)
                if protected_points is not None
                else None
            ),
            prefer_perpendicular=True,
        )
    else:
        label_route = []
        ex, ey = label_anchor
    routing.wires.extend(label_route)
    label_angle = int((wa + 180) % 360)
    if promotion.net_name.startswith("/"):
        global_label = GlobalLabelPlacement(promotion.net_name, ex, ey, label_angle)
        if global_label not in routing.global_labels:
            routing.global_labels.append(global_label)
        return

    local_label = NetLabel(promotion.net_name, ex, ey, label_angle)
    if local_label not in routing.labels:
        routing.labels.append(local_label)


def _safe_stub_label_anchor(
    *,
    pin_point: tuple[float, float],
    pin_angle: float,
    occupied_label_points: set[tuple[float, float]],
    protected_points: set[tuple[float, float]] | None = None,
    shared_protected_points: set[tuple[float, float]] | None = None,
) -> tuple[float, float] | None:
    stub_x, stub_y = _stub_end(pin_point[0], pin_point[1], pin_angle)
    stub = (round(stub_x, 2), round(stub_y, 2))
    if stub in occupied_label_points:
        return None
    if protected_points and stub in protected_points and (
        not shared_protected_points or stub not in shared_protected_points
    ):
        return None
    return stub_x, stub_y


def _safe_pin_label_anchor(
    *,
    pin_point: tuple[float, float],
    occupied_label_points: set[tuple[float, float]],
    protected_points: set[tuple[float, float]] | None = None,
) -> tuple[float, float] | None:
    pin = (round(pin_point[0], 2), round(pin_point[1], 2))
    if pin in occupied_label_points:
        return None
    if protected_points and pin in protected_points:
        return None
    return pin_point


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _stub_end(wx: float, wy: float, wa: float) -> tuple[float, float]:
    """Return the far end of the 5.08 mm stub extended outward from a pin.

    *wa* is the KiCad pin angle (direction **from endpoint toward the symbol
    body**; e.g. 0 = body is to the right of the endpoint, stub goes left).
    The stub extends in the *opposite* direction.
    """
    rad = math.radians(wa)
    return wx - math.cos(rad) * WIRE_EXTEND_MM, wy - math.sin(rad) * WIRE_EXTEND_MM


def _manhattan(x1: float, y1: float, x2: float, y2: float) -> float:
    return abs(x2 - x1) + abs(y2 - y1)


def _resolve_pin_anchors(
    pin_endpoints: Mapping[tuple[str, str], tuple[float, float, float]],
    pin_anchors: Mapping[tuple[str, str], PinAnchor] | None = None,
) -> dict[tuple[str, str], PinAnchor]:
    """Return the explicit pin-anchor map used by routing helpers."""

    resolved = dict(pin_anchors or {})
    for (ref, pin), (x, y, angle) in pin_endpoints.items():
        resolved.setdefault(
            (ref, pin),
            PinAnchor(ref=ref, pin=pin, x=x, y=y, angle=angle),
        )
    return resolved


def _coerce_pin_anchor_map(
    pin_map: Mapping[tuple[str, str], PinAnchor | tuple[float, float, float]] | None,
) -> dict[tuple[str, str], PinAnchor]:
    """Normalize legacy endpoint tuples into PinAnchor values."""

    if pin_map is None:
        return {}

    normalized: dict[tuple[str, str], PinAnchor] = {}
    for (ref, pin), anchor in pin_map.items():
        if isinstance(anchor, PinAnchor):
            normalized[(ref, pin)] = anchor
            continue
        x, y, angle = anchor
        normalized[(ref, pin)] = PinAnchor(ref=ref, pin=pin, x=x, y=y, angle=angle)
    return normalized


def _is_connector_passive_edge(ref_a: str, ref_b: str) -> bool:
    """Return True for simple connector-to-passive edge links.

    These 2-pin nets are typically the small input/output links at the circuit
    boundary. Keep them directly wired even if later tier inference stretches
    the passive further into the signal path.
    """
    pair = {_component_type(ref_a), _component_type(ref_b)}
    return pair == {"connector", "passive"}


def _l_route(ex1: float, ey1: float, ex2: float, ey2: float) -> list[WireSegment]:
    """Return up to two orthogonal segments that connect (ex1,ey1) to (ex2,ey2).

    Prefer the simpler horizontal-first elbow, but switch to the vertical-first
    variant when the default path would run through other protected stub-end
    points from nearby nets.
    """
    return _l_route_with_protected_points(ex1, ey1, ex2, ey2, protected_points=None)


def _l_route_with_protected_points(
    ex1: float,
    ey1: float,
    ex2: float,
    ey2: float,
    *,
    protected_points: set[tuple[float, float]] | None,
) -> list[WireSegment]:
    horizontal_first = _horizontal_first_l_route(ex1, ey1, ex2, ey2)
    if not protected_points:
        return horizontal_first

    vertical_first = _vertical_first_l_route(ex1, ey1, ex2, ey2)
    current_endpoints = {(round(ex1, 2), round(ey1, 2)), (round(ex2, 2), round(ey2, 2))}
    horizontal_score = _route_protected_point_score(
        horizontal_first,
        protected_points=protected_points,
        excluded_points=current_endpoints,
    )
    vertical_score = _route_protected_point_score(
        vertical_first,
        protected_points=protected_points,
        excluded_points=current_endpoints,
    )
    if vertical_score < horizontal_score:
        return vertical_first
    return horizontal_first


def _horizontal_first_l_route(ex1: float, ey1: float, ex2: float, ey2: float) -> list[WireSegment]:
    segs: list[WireSegment] = []
    if not math.isclose(ex1, ex2, abs_tol=0.01):
        segs.append(WireSegment(ex1, ey1, ex2, ey1))
    if not math.isclose(ey1, ey2, abs_tol=0.01):
        segs.append(WireSegment(ex2, ey1, ex2, ey2))
    return segs


def _vertical_first_l_route(ex1: float, ey1: float, ex2: float, ey2: float) -> list[WireSegment]:
    segs: list[WireSegment] = []
    if not math.isclose(ey1, ey2, abs_tol=0.01):
        segs.append(WireSegment(ex1, ey1, ex1, ey2))
    if not math.isclose(ex1, ex2, abs_tol=0.01):
        segs.append(WireSegment(ex1, ey2, ex2, ey2))
    return segs


def _three_segment_route_via_x(
    ex1: float,
    ey1: float,
    ex2: float,
    ey2: float,
    via_x: float,
) -> list[WireSegment]:
    segs: list[WireSegment] = []
    if not math.isclose(ex1, via_x, abs_tol=0.01):
        segs.append(WireSegment(ex1, ey1, via_x, ey1))
    if not math.isclose(ey1, ey2, abs_tol=0.01):
        segs.append(WireSegment(via_x, ey1, via_x, ey2))
    if not math.isclose(via_x, ex2, abs_tol=0.01):
        segs.append(WireSegment(via_x, ey2, ex2, ey2))
    return segs


def _three_segment_route_via_y(
    ex1: float,
    ey1: float,
    ex2: float,
    ey2: float,
    via_y: float,
) -> list[WireSegment]:
    segs: list[WireSegment] = []
    if not math.isclose(ey1, via_y, abs_tol=0.01):
        segs.append(WireSegment(ex1, ey1, ex1, via_y))
    if not math.isclose(ex1, ex2, abs_tol=0.01):
        segs.append(WireSegment(ex1, via_y, ex2, via_y))
    if not math.isclose(via_y, ey2, abs_tol=0.01):
        segs.append(WireSegment(ex2, via_y, ex2, ey2))
    return segs


def _wire_path_length(route: list[WireSegment]) -> float:
    return sum(abs(seg.x2 - seg.x1) + abs(seg.y2 - seg.y1) for seg in route)


def _segment_label_angle(segment: WireSegment) -> int:
    if math.isclose(segment.y1, segment.y2, abs_tol=0.01):
        return 0 if segment.x2 >= segment.x1 else 180
    return 90 if segment.y2 >= segment.y1 else 270


def _best_direct_route_with_protected_points(
    ex1: float,
    ey1: float,
    ex2: float,
    ey2: float,
    *,
    protected_points: set[tuple[float, float]] | None,
) -> list[WireSegment]:
    horizontal_first = _horizontal_first_l_route(ex1, ey1, ex2, ey2)
    if not protected_points:
        return horizontal_first

    vertical_first = _vertical_first_l_route(ex1, ey1, ex2, ey2)
    current_endpoints = {(round(ex1, 2), round(ey1, 2)), (round(ex2, 2), round(ey2, 2))}
    horizontal_score = _route_protected_point_score(
        horizontal_first,
        protected_points=protected_points,
        excluded_points=current_endpoints,
    )
    vertical_score = _route_protected_point_score(
        vertical_first,
        protected_points=protected_points,
        excluded_points=current_endpoints,
    )
    best_l_score = min(horizontal_score, vertical_score)
    best_l_route = vertical_first if vertical_score < horizontal_score else horizontal_first

    if best_l_score == 0:
        return best_l_route

    candidates: list[list[WireSegment]] = []
    for detour_multiplier in (1, 2, 3):
        detour = round(WIRE_EXTEND_MM * detour_multiplier, 2)
        candidates.extend(
            [
                _three_segment_route_via_x(ex1, ey1, ex2, ey2, max(ex1, ex2) + detour),
                _three_segment_route_via_y(ex1, ey1, ex2, ey2, min(ey1, ey2) - detour),
                _three_segment_route_via_y(ex1, ey1, ex2, ey2, max(ey1, ey2) + detour),
                _three_segment_route_via_x(ex1, ey1, ex2, ey2, min(ex1, ex2) - detour),
            ]
        )

    def _candidate_key(route: list[WireSegment]) -> tuple[int, float, int]:
        return (
            _route_protected_point_score(
                route,
                protected_points=protected_points,
                excluded_points=current_endpoints,
            ),
            _wire_path_length(route),
            len(route),
        )

    best_detour = min(candidates, key=_candidate_key)
    best_l_key = (best_l_score, _wire_path_length(best_l_route), len(best_l_route))
    if _candidate_key(best_detour) < best_l_key:
        return _simplify_wires(best_detour, protected_points=current_endpoints)
    return best_l_route


def _route_protected_point_score(
    route: list[WireSegment],
    *,
    protected_points: set[tuple[float, float]],
    excluded_points: set[tuple[float, float]],
) -> int:
    return sum(
        1
        for point in protected_points
        if point not in excluded_points
        and any(_point_on_segment(point, segment) for segment in route)
    )


def _route_candidate_key(
    route: list[WireSegment],
    *,
    protected_points: set[tuple[float, float]] | None,
    endpoints: list[tuple[float, float]],
) -> tuple[int, float, int]:
    excluded_points = {(round(x, 2), round(y, 2)) for x, y in endpoints}
    return (
        _route_protected_point_score(
            route,
            protected_points=protected_points or set(),
            excluded_points=excluded_points,
        ),
        _wire_path_length(route),
        len(route),
    )


def _occupied_wire_points(
    wires: list[WireSegment],
) -> set[tuple[float, float]]:
    """Return snapped grid points occupied by already-routed orthogonal wires."""
    occupied: set[tuple[float, float]] = set()
    for segment in wires:
        if math.isclose(segment.x1, segment.x2, abs_tol=0.01):
            x = round(segment.x1, 2)
            y0 = min(segment.y1, segment.y2)
            y1 = max(segment.y1, segment.y2)
            steps = int(round((y1 - y0) / WIRE_EXTEND_MM * 4)) + 1
            for index in range(steps + 1):
                y = round(_snap_grid(y0 + (index * 1.27)), 2)
                if y0 - 0.01 <= y <= y1 + 0.01:
                    occupied.add((x, y))
            continue
        if math.isclose(segment.y1, segment.y2, abs_tol=0.01):
            y = round(segment.y1, 2)
            x0 = min(segment.x1, segment.x2)
            x1 = max(segment.x1, segment.x2)
            steps = int(round((x1 - x0) / WIRE_EXTEND_MM * 4)) + 1
            for index in range(steps + 1):
                x = round(_snap_grid(x0 + (index * 1.27)), 2)
                if x0 - 0.01 <= x <= x1 + 0.01:
                    occupied.add((x, y))
    return occupied


def _occupied_label_points(routing: NetRouting) -> set[tuple[float, float]]:
    occupied: set[tuple[float, float]] = set()
    occupied.update((round(label.x, 2), round(label.y, 2)) for label in routing.labels)
    occupied.update((round(label.x, 2), round(label.y, 2)) for label in routing.global_labels)
    occupied.update((round(symbol.x, 2), round(symbol.y, 2)) for symbol in routing.power_symbols)
    return occupied


def _label_attachment_plan(
    *,
    pin_point: tuple[float, float],
    pin_angle: float,
    occupied_points: set[tuple[float, float]],
    protected: _ProtectedPointContext | None = None,
    prefer_perpendicular: bool = False,
) -> tuple[list[WireSegment], float, float]:
    """Return a short breakout route to a safe label attachment point."""
    pin_x, pin_y = pin_point
    stub_x, stub_y = _stub_end(pin_x, pin_y, pin_angle)
    stub = (round(stub_x, 2), round(stub_y, 2))
    start = (round(pin_x, 2), round(pin_y, 2))
    angle = int(round(pin_angle)) % 360
    blocked_points = set(occupied_points)
    start_was_occupied = start in occupied_points
    stub_was_occupied = stub in occupied_points
    if protected:
        blocked_points.update(protected.points)
    if not start_was_occupied:
        blocked_points.discard(start)
    if (
        (not protected or not protected.shared_points or stub not in protected.shared_points)
        and not stub_was_occupied
    ):
        blocked_points.discard(stub)

    if prefer_perpendicular:
        candidates = [
            _offset_point_along_angle(stub_x, stub_y, (angle + 90) % 360, WIRE_EXTEND_MM),
            _offset_point_along_angle(stub_x, stub_y, (angle + 270) % 360, WIRE_EXTEND_MM),
            _offset_point_along_angle(pin_x, pin_y, (angle + 90) % 360, WIRE_EXTEND_MM),
            _offset_point_along_angle(pin_x, pin_y, (angle + 270) % 360, WIRE_EXTEND_MM),
            (stub_x, stub_y),
            _offset_point_along_angle(stub_x, stub_y, angle, WIRE_EXTEND_MM),
            (pin_x, pin_y),
        ]
    else:
        candidates = [
            (stub_x, stub_y),
            _offset_point_along_angle(stub_x, stub_y, angle, WIRE_EXTEND_MM),
            _offset_point_along_angle(stub_x, stub_y, (angle + 90) % 360, WIRE_EXTEND_MM),
            _offset_point_along_angle(stub_x, stub_y, (angle + 270) % 360, WIRE_EXTEND_MM),
            _offset_point_along_angle(pin_x, pin_y, (angle + 90) % 360, WIRE_EXTEND_MM),
            _offset_point_along_angle(pin_x, pin_y, (angle + 270) % 360, WIRE_EXTEND_MM),
            (pin_x, pin_y),
        ]

    seen_candidates = {(round(x, 2), round(y, 2)) for x, y in candidates}
    for origin_x, origin_y in ((stub_x, stub_y), (pin_x, pin_y)):
        for step_count in range(2, 5):
            for x_steps in range(-step_count, step_count + 1):
                for y_steps in range(-step_count, step_count + 1):
                    if max(abs(x_steps), abs(y_steps)) != step_count:
                        continue
                    candidate = (
                        round(origin_x + (x_steps * WIRE_EXTEND_MM), 2),
                        round(origin_y + (y_steps * WIRE_EXTEND_MM), 2),
                    )
                    if candidate in seen_candidates:
                        continue
                    seen_candidates.add(candidate)
                    candidates.append(candidate)

    best_route: list[WireSegment] | None = None
    best_anchor = (stub_x, stub_y)
    best_key: tuple[int, int, int, float, int, int] | None = None
    for index, (candidate_x, candidate_y) in enumerate(candidates):
        candidate = (round(candidate_x, 2), round(candidate_y, 2))
        if candidate == start:
            route: list[WireSegment] = []
        else:
            route = _best_direct_route_with_protected_points(
                pin_x,
                pin_y,
                candidate_x,
                candidate_y,
                protected_points=blocked_points,
            )
        route_score = _route_protected_point_score(
            route,
            protected_points=blocked_points,
            excluded_points={start, candidate},
        )
        occupied_penalty = 1 if candidate in blocked_points else 0
        pin_penalty = 1 if candidate == start else 0
        key = (
            occupied_penalty,
            route_score,
            pin_penalty,
            _wire_path_length(route),
            len(route),
            index,
        )
        if best_key is None or key < best_key:
            best_key = key
            best_route = route
            best_anchor = (candidate_x, candidate_y)

    return best_route or [], best_anchor[0], best_anchor[1]


def _append_direct_power_symbol(
    routing: NetRouting,
    *,
    net_name: str,
    pin_ref: PinRefIR,
    endpoint: tuple[float, float, float],
    protected: _ProtectedPointContext,
) -> None:
    wx, wy, wa = endpoint
    occupied_label_points = _occupied_label_points(routing)
    anchor = _safe_stub_label_anchor(
        pin_point=(wx, wy),
        pin_angle=wa,
        occupied_label_points=occupied_label_points,
        protected_points=protected.points,
        shared_protected_points=protected.shared_points,
    )
    if anchor is None:
        occupied_points = _occupied_wire_points(routing.wires) | occupied_label_points
        route, anchor_x, anchor_y = _label_attachment_plan(
            pin_point=(wx, wy),
            pin_angle=wa,
            occupied_points=occupied_points,
            protected=_ProtectedPointContext(
                protected.points | occupied_points,
                protected.shared_points,
            ),
            prefer_perpendicular=True,
        )
        if route:
            connection_angle = _segment_label_angle(route[-1])
        else:
            connection_angle = _power_label_angle_for_pin(wa)
    else:
        anchor_x, anchor_y = anchor
        route = []
        if not (
            math.isclose(wx, anchor_x, abs_tol=0.01)
            and math.isclose(wy, anchor_y, abs_tol=0.01)
        ):
            route.append(WireSegment(wx, wy, anchor_x, anchor_y))
        connection_angle = _power_label_angle_for_pin(wa)
    px, py = _offset_point_along_angle(
        anchor_x,
        anchor_y,
        connection_angle,
        _POWER_LABEL_CLEARANCE_MM,
    )
    if not (
        math.isclose(anchor_x, px, abs_tol=0.01)
        and math.isclose(anchor_y, py, abs_tol=0.01)
    ):
        route.append(WireSegment(anchor_x, anchor_y, px, py))
    symbol_angle = _power_symbol_angle(net_name, connection_angle)
    routing.wires.extend(route)
    routing.power_symbols.append(PowerSymbolPlacement(net_name, px, py, symbol_angle))
    routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net_name))


def _foreign_attachment_points_for_known(
    known: list[tuple[PinRefIR, tuple[float, float, float]]],
    *,
    protected_pin_points: set[tuple[float, float]],
    protected_stub_points: set[tuple[float, float]],
    shared_protected_stub_points: set[tuple[float, float]] | None = None,
) -> set[tuple[float, float]]:
    current_pin_points = {
        (round(wx, 2), round(wy, 2)) for _pin_ref, (wx, wy, _wa) in known
    }
    current_stub_points = {
        (round(ex, 2), round(ey, 2))
        for _pin_ref, (wx, wy, wa) in known
        for ex, ey in [_stub_end(wx, wy, wa)]
    }
    foreign_stub_points = {
        point
        for point in protected_stub_points
        if point not in current_stub_points or point in (shared_protected_stub_points or set())
    }
    return (protected_pin_points - current_pin_points) | foreign_stub_points


def _known_pin_stub_hits_foreign_attachment(
    known: list[tuple[PinRefIR, tuple[float, float, float]]],
    *,
    protected_pin_points: set[tuple[float, float]],
    protected_stub_points: set[tuple[float, float]],
    shared_protected_stub_points: set[tuple[float, float]] | None = None,
) -> bool:
    foreign_attachment_points = _foreign_attachment_points_for_known(
        known,
        protected_pin_points=protected_pin_points,
        protected_stub_points=protected_stub_points,
        shared_protected_stub_points=shared_protected_stub_points,
    )
    return any(
        (round(ex, 2), round(ey, 2)) in foreign_attachment_points
        for _pin_ref, (wx, wy, wa) in known
        for ex, ey in [_stub_end(wx, wy, wa)]
    )


def _append_pin_endpoint_labels(
    routing: NetRouting,
    *,
    net_name: str,
    known: list[tuple[PinRefIR, tuple[float, float, float]]],
    protected: _ProtectedPointContext | None = None,
    prefer_stub_anchor: bool = False,
) -> None:
    for pin_ref, (wx, wy, wa) in known:
        label_anchor = None
        if prefer_stub_anchor:
            label_anchor = _safe_stub_label_anchor(
                pin_point=(wx, wy),
                pin_angle=wa,
                occupied_label_points=_occupied_label_points(routing),
                protected_points=protected.points if protected else None,
                shared_protected_points=protected.shared_points if protected else None,
            )
            if label_anchor is None:
                label_anchor = _safe_pin_label_anchor(
                    pin_point=(wx, wy),
                    occupied_label_points=_occupied_label_points(routing),
                    protected_points=protected.points if protected else None,
                )
        if label_anchor is None:
            occupied_points = _occupied_wire_points(routing.wires) | _occupied_label_points(routing)
            label_route, ex, ey = _label_attachment_plan(
                pin_point=(wx, wy),
                pin_angle=wa,
                occupied_points=occupied_points,
                protected=protected,
                prefer_perpendicular=True,
            )
        else:
            label_route = []
            ex, ey = label_anchor
        routing.wires.extend(label_route)
        routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net_name))
        label_angle = int((wa + 180) % 360)
        if net_name.startswith("/"):
            routing.global_labels.append(GlobalLabelPlacement(net_name, ex, ey, label_angle))
        else:
            routing.labels.append(NetLabel(net_name, ex, ey, label_angle))


def _point_on_segment(point: tuple[float, float], segment: WireSegment) -> bool:
    x, y = point
    if math.isclose(segment.y1, segment.y2, abs_tol=0.01):
        return (
            math.isclose(y, segment.y1, abs_tol=0.01)
            and min(segment.x1, segment.x2) <= x <= max(segment.x1, segment.x2)
        )
    if math.isclose(segment.x1, segment.x2, abs_tol=0.01):
        return (
            math.isclose(x, segment.x1, abs_tol=0.01)
            and min(segment.y1, segment.y2) <= y <= max(segment.y1, segment.y2)
        )
    return False


def _snap_grid(v: float, grid: float = 1.27) -> float:
    """Snap *v* to the nearest *grid* mm increment (KiCad 50-mil grid)."""
    return round(round(v / grid) * grid, 4)


def _offset_point_along_angle(
    x: float,
    y: float,
    angle: int,
    distance: float,
) -> tuple[float, float]:
    """Return *(x, y)* shifted *distance* mm along cardinal *angle*."""
    normalized = angle % 360
    if normalized == 0:
        return _snap_grid(x + distance), y
    if normalized == 90:
        return x, _snap_grid(y + distance)
    if normalized == 180:
        return _snap_grid(x - distance), y
    if normalized == 270:
        return x, _snap_grid(y - distance)
    rad = math.radians(normalized)
    return _snap_grid(x + math.cos(rad) * distance), _snap_grid(y + math.sin(rad) * distance)


def _power_label_angle_for_pin(pin_angle: float) -> int:
    """Return the outward-facing label angle for a power pin stub."""
    return int((pin_angle + 180) % 360)


def _power_symbol_angle(net_name: str, default_angle: int) -> int:
    """Return the preferred placed-symbol angle for a power net.

    Keep ground symbols visually consistent by always pointing them downward.
    This avoids sideways/upward ground glyphs that can read like lateral rail
    continuations in crowded local clusters.
    """
    if net_name.upper() == "GND":
        return 0
    return default_angle


def _power_cluster_angle(points: list[tuple[float, float]]) -> int:
    """Choose an outward direction for a shared power label cluster.

    The chosen angle points toward the nearest edge of the cluster bounding box,
    which keeps the visible label on the open side of the local geometry.
    """
    if not points:
        return 0

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    distances = {
        180: cx - min(xs),
        0: max(xs) - cx,
        270: cy - min(ys),
        90: max(ys) - cy,
    }
    priority = {180: 0, 0: 1, 270: 2, 90: 3}
    return min(distances, key=lambda angle: (distances[angle], priority[angle]))


def _fallback_power_label_position(
    x: float,
    y: float,
    angle: int,
) -> tuple[float, float, int]:
    """Return an off-axis fallback position for power nets missing a library symbol.

    Global labels render as text boxes anchored on the connection point, so
    leaving them colinear with the incoming rail tends to stamp the visible text
    directly on top of that rail. Move them one clearance step orthogonally into
    whitespace instead.
    """
    normalized = angle % 360
    fallback_angle = 270 if normalized in {0, 180} else 180
    fx, fy = _offset_point_along_angle(x, y, fallback_angle, _POWER_LABEL_CLEARANCE_MM)
    return fx, fy, fallback_angle


def _hub_route(
    endpoints: list[tuple[float, float]],
) -> tuple[list[WireSegment], list[JunctionPoint]]:
    """Route a multi-pin net through a central hub point.

    Computes the centroid of *endpoints*, snaps it to the KiCad grid, then
    L-routes each endpoint to the hub.  A :class:`JunctionPoint` is added at
    the hub when ≥ 3 spokes converge there.

    Returns *(segments, junctions)*.
    """
    hub_x = _snap_grid(sum(e[0] for e in endpoints) / len(endpoints))
    hub_y = _snap_grid(sum(e[1] for e in endpoints) / len(endpoints))

    segs: list[WireSegment] = []
    for ex, ey in endpoints:
        segs.extend(_l_route(ex, ey, hub_x, hub_y))

    junctions: list[JunctionPoint] = []
    if len(endpoints) >= 3:
        junctions.append(JunctionPoint(hub_x, hub_y))

    return segs, junctions


def _spine_route(
    endpoints: list[tuple[float, float]],
) -> tuple[list[WireSegment], list[JunctionPoint]]:
    """Route a multi-pin net as a spine with T-junction taps.

    Determines the dominant axis (horizontal vs vertical) from the bounding
    box of *endpoints*, draws a single spine wire along that axis, then
    connects each endpoint to the nearest point on the spine with a
    perpendicular segment and a :class:`JunctionPoint` at the T-intersection.

    This produces a cleaner "bus-style" visual than the centroid-hub approach
    when all stubs lie roughly along a line.

    Returns *(segments, junctions)*.
    """
    xs = [e[0] for e in endpoints]
    ys = [e[1] for e in endpoints]
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)

    segs: list[WireSegment] = []
    junctions: list[JunctionPoint] = []

    if x_span >= y_span:
        # Horizontal spine — spine runs left-to-right at the mean Y.
        spine_y = _snap_grid(sum(ys) / len(ys))
        spine_x0 = _snap_grid(min(xs))
        spine_x1 = _snap_grid(max(xs))
        segs.append(WireSegment(spine_x0, spine_y, spine_x1, spine_y))
        for ex, ey in endpoints:
            sx = _snap_grid(ex)
            if not math.isclose(ey, spine_y, abs_tol=0.01):
                segs.append(WireSegment(sx, ey, sx, spine_y))
            junctions.append(JunctionPoint(sx, spine_y))
    else:
        # Vertical spine — spine runs top-to-bottom at the mean X.
        spine_x = _snap_grid(sum(xs) / len(xs))
        spine_y0 = _snap_grid(min(ys))
        spine_y1 = _snap_grid(max(ys))
        segs.append(WireSegment(spine_x, spine_y0, spine_x, spine_y1))
        for ex, ey in endpoints:
            sy = _snap_grid(ey)
            if not math.isclose(ex, spine_x, abs_tol=0.01):
                segs.append(WireSegment(ex, sy, spine_x, sy))
            junctions.append(JunctionPoint(spine_x, sy))

    return segs, junctions


def _shared_lane_route(
    endpoints: list[tuple[float, float]],
    *,
    axis: str | None = None,
    coordinate: float | None = None,
    min_bound: float | None = None,
    max_bound: float | None = None,
) -> tuple[list[WireSegment], list[JunctionPoint]]:
    """Route a local net along a repeated endpoint lane.

    When two or more endpoints already share the same X or Y coordinate, keep
    the trunk on that existing lane instead of inventing a new centroid spine.
    This avoids the nested-rectangle look common in compact passive ladders.
    """
    if len(endpoints) < 2:
        return [], []

    rounded_xs = [round(point[0], 2) for point in endpoints]
    rounded_ys = [round(point[1], 2) for point in endpoints]

    x_counts: dict[float, int] = {}
    y_counts: dict[float, int] = {}
    for value in rounded_xs:
        x_counts[value] = x_counts.get(value, 0) + 1
    for value in rounded_ys:
        y_counts[value] = y_counts.get(value, 0) + 1

    shared_x, shared_x_count = max(x_counts.items(), key=lambda item: item[1])
    shared_y, shared_y_count = max(y_counts.items(), key=lambda item: item[1])

    if axis is None or coordinate is None:
        if shared_x_count >= shared_y_count and shared_x_count >= 2:
            axis = "vertical"
            coordinate = shared_x
        elif shared_y_count >= 2:
            axis = "horizontal"
            coordinate = shared_y
        else:
            return _spine_route(endpoints)

    segs: list[WireSegment] = []
    junctions: list[JunctionPoint] = []

    if axis == "vertical":
        trunk_x = coordinate
        min_y = min(point[1] for point in endpoints) if min_bound is None else min_bound
        max_y = max(point[1] for point in endpoints) if max_bound is None else max_bound
        segs.append(WireSegment(trunk_x, min_y, trunk_x, max_y))
        for ex, ey in endpoints:
            target_y = min(max(ey, min_y), max_y)
            if not math.isclose(ey, target_y, abs_tol=0.01):
                segs.append(WireSegment(ex, ey, ex, target_y))
            if not math.isclose(ex, trunk_x, abs_tol=0.01):
                segs.append(WireSegment(ex, target_y, trunk_x, target_y))
            junctions.append(JunctionPoint(trunk_x, target_y))
        return _simplify_wires(segs), junctions

    if axis == "horizontal":
        trunk_y = coordinate
        min_x = min(point[0] for point in endpoints) if min_bound is None else min_bound
        max_x = max(point[0] for point in endpoints) if max_bound is None else max_bound
        segs.append(WireSegment(min_x, trunk_y, max_x, trunk_y))
        for ex, ey in endpoints:
            target_x = min(max(ex, min_x), max_x)
            if not math.isclose(ex, target_x, abs_tol=0.01):
                segs.append(WireSegment(ex, ey, target_x, ey))
            if not math.isclose(ey, trunk_y, abs_tol=0.01):
                segs.append(WireSegment(target_x, ey, target_x, trunk_y))
            junctions.append(JunctionPoint(target_x, trunk_y))
        return _simplify_wires(segs), junctions

    return _spine_route(endpoints)


def _is_local_ladder_net(
    endpoints: list[tuple[float, float]],
) -> bool:
    """Return True when a net is compact enough to participate in a ladder neighborhood."""
    xs = [point[0] for point in endpoints]
    ys = [point[1] for point in endpoints]
    return (max(xs) - min(xs)) <= 80.0 and (max(ys) - min(ys)) <= 50.0


def _boxes_touch_or_overlap(
    box_a: tuple[float, float, float, float],
    box_b: tuple[float, float, float, float],
    *,
    margin: float = 15.0,
) -> bool:
    """Return True when two axis-aligned boxes overlap or nearly touch."""
    ax0, ax1, ay0, ay1 = box_a
    bx0, bx1, by0, by1 = box_b
    return not (
        ax1 + margin < bx0 or bx1 + margin < ax0 or ay1 + margin < by0 or by1 + margin < ay0
    )


def _preferred_shared_lane(
    endpoints: list[tuple[float, float]],
) -> tuple[str, float] | None:
    """Return the dominant repeated lane for a compact local net, if one exists."""
    rounded_xs = [round(point[0], 2) for point in endpoints]
    rounded_ys = [round(point[1], 2) for point in endpoints]

    x_counts: dict[float, int] = {}
    y_counts: dict[float, int] = {}
    for value in rounded_xs:
        x_counts[value] = x_counts.get(value, 0) + 1
    for value in rounded_ys:
        y_counts[value] = y_counts.get(value, 0) + 1

    shared_x, shared_x_count = max(x_counts.items(), key=lambda item: item[1])
    shared_y, shared_y_count = max(y_counts.items(), key=lambda item: item[1])

    if shared_x_count >= shared_y_count and shared_x_count >= 2:
        return "vertical", shared_x
    if shared_y_count >= 2:
        return "horizontal", shared_y
    return None


def _is_compact_rightward_tail(
    endpoints: list[tuple[float, float]],
    *,
    axis: str,
    coordinate: float,
) -> bool:
    """Return True when a single vertical lane is just a compact rightward tail.

    These 3-pin tails already read cleanly as a short chain: two nearby entry
    points clustered on the same vertical lane region and one downstream point
    extending to the right. One entry point may land slightly left of the lane
    after symbol/stub geometry, but the shape still reads as a short output
    tail rather than a ladder trunk.
    """
    if axis != "vertical" or len(endpoints) != 3:
        return False

    lane_tolerance = (WIRE_EXTEND_MM / 4) + 0.05
    near_lane_points = [
        point for point in endpoints if math.isclose(point[0], coordinate, abs_tol=lane_tolerance)
    ]
    other_points = [
        point
        for point in endpoints
        if not math.isclose(point[0], coordinate, abs_tol=lane_tolerance)
    ]
    if len(near_lane_points) != 2 or len(other_points) != 1:
        return False

    other_x, other_y = other_points[0]
    if other_x <= coordinate + 0.01:
        return False

    nearest_lane_y = min(abs(other_y - point[1]) for point in near_lane_points)
    return nearest_lane_y <= (1.5 * WIRE_EXTEND_MM) and (other_x - coordinate) <= (
        6 * WIRE_EXTEND_MM
    )


def _is_compact_horizontal_stage_tail(
    endpoints: list[tuple[float, float]],
    *,
    axis: str,
) -> bool:
    """Return True when a local 3-pin net reads as stage then downstream tail.

    This covers the mirror-image analog-audio case where a short support point
    on the left feeds a middle stage node (for example a potentiometer wiper),
    and the visually dominant continuation should then run rightward toward the
    downstream stage instead of dropping immediately into a shared horizontal bus.
    """
    if axis != "horizontal" or len(endpoints) != 3:
        return False

    left, middle, right = sorted(endpoints, key=lambda point: (point[0], point[1]))
    left_x, left_y = left
    middle_x, middle_y = middle
    right_x, right_y = right

    left_gap = middle_x - left_x
    right_gap = right_x - middle_x
    if left_gap <= 0.01 or right_gap <= 0.01:
        return False
    if left_gap > (2 * WIRE_EXTEND_MM) + 0.05:
        return False
    if right_gap <= left_gap + 0.01:
        return False
    if abs(left_y - right_y) > WIRE_EXTEND_MM + 0.05:
        return False

    stage_offset = min(abs(middle_y - left_y), abs(middle_y - right_y))
    return stage_offset > WIRE_EXTEND_MM + 0.05


def _plan_single_grouped_ladder_lane(
    *,
    axis: str,
    base_coordinate: float,
    endpoints: list[tuple[float, float]],
    refs: tuple[str, ...] = (),
    heuristic_policy: RoutingHeuristicPolicy = DEFAULT_ROUTING_HEURISTIC_POLICY,
) -> SharedLanePlan | None:
    """Return the single-net lane plan, or ``None`` when chain routing should win."""
    candidate_plan = SharedLanePlan(axis, base_coordinate)
    if heuristic_policy.should_skip_shared_lane_plan(
        endpoints, candidate_plan
    ) or heuristic_policy.should_prefer_small_analog_chain(
        endpoints,
        inferred_plan=candidate_plan,
        refs=refs,
    ):
        return None

    if axis == "horizontal":
        shared_points = [
            point for point in endpoints if math.isclose(point[1], base_coordinate, abs_tol=0.01)
        ]
        other_points = [
            point
            for point in endpoints
            if not math.isclose(point[1], base_coordinate, abs_tol=0.01)
        ]
        if len(shared_points) == 2 and len(other_points) == 1:
            other_x = other_points[0][0]
            anchor_x = max(
                (point[0] for point in shared_points),
                key=lambda x: abs(x - other_x),
            )
            return SharedLanePlan(
                axis,
                base_coordinate,
                min(anchor_x, other_x),
                max(anchor_x, other_x),
            )

    return SharedLanePlan(axis, base_coordinate)


def _should_skip_inferred_lane_plan(
    endpoints: list[tuple[float, float]],
    inferred_plan: SharedLanePlan | None,
) -> bool:
    """Return True when an inferred lane would overfit a compact output tail."""
    if inferred_plan is None:
        return False
    return _prefer_chain_route(endpoints) and _is_compact_rightward_tail(
        endpoints,
        axis=inferred_plan.axis,
        coordinate=inferred_plan.coordinate,
    )


def _compact_horizontal_stage_tail_route(
    endpoints: list[tuple[float, float]],
) -> tuple[list[WireSegment], list[JunctionPoint]]:
    """Route a short left support into a stage node plus downstream continuation.

    The middle point stays visually dominant: the support enters the stage from
    the left, then the main continuation runs rightward from that stage toward
    the downstream endpoint. This makes small analog chains read as deliberate
    signal flow instead of taps into a shared horizontal scaffold.
    """
    if len(endpoints) != 3:
        return _chain_route(endpoints)

    left, middle, right = sorted(endpoints, key=lambda point: (point[0], point[1]))
    left_x, left_y = left
    middle_x, middle_y = middle
    right_x, right_y = right

    segs: list[WireSegment] = []
    if not math.isclose(left_x, middle_x, abs_tol=0.01):
        segs.append(WireSegment(left_x, left_y, middle_x, left_y))
    if not math.isclose(left_y, middle_y, abs_tol=0.01):
        segs.append(WireSegment(middle_x, left_y, middle_x, middle_y))
    if not math.isclose(middle_x, right_x, abs_tol=0.01):
        segs.append(WireSegment(middle_x, middle_y, right_x, middle_y))
    if not math.isclose(middle_y, right_y, abs_tol=0.01):
        segs.append(WireSegment(right_x, middle_y, right_x, right_y))

    protected = {(round(x, 2), round(y, 2)) for x, y in endpoints}
    return _simplify_wires(segs, protected_points=protected), []


def _compact_vertical_tail_members(
    endpoints: list[tuple[float, float]],
    *,
    coordinate: float,
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float], bool] | None:
    """Return ``(upstream, pivot, downstream, exact_lane_match)`` for a compact tail."""
    lane_tolerance = (WIRE_EXTEND_MM / 4) + 0.05
    near_lane_points = [
        point for point in endpoints if math.isclose(point[0], coordinate, abs_tol=lane_tolerance)
    ]
    other_points = [
        point
        for point in endpoints
        if not math.isclose(point[0], coordinate, abs_tol=lane_tolerance)
    ]
    if len(near_lane_points) == 2 and len(other_points) == 1:
        downstream = other_points[0]
        pivot = min(near_lane_points, key=lambda point: abs(point[1] - downstream[1]))
        upstream = next(point for point in near_lane_points if point != pivot)
        return upstream, pivot, downstream, True
    if len(endpoints) != 3:
        return None
    downstream = max(endpoints, key=lambda point: (point[0], -point[1]))
    remaining = [point for point in endpoints if point != downstream]
    if len(remaining) != 2:
        return None
    pivot = min(remaining, key=lambda point: abs(point[1] - downstream[1]))
    upstream = next(point for point in remaining if point != pivot)
    return upstream, pivot, downstream, False


def _compact_vertical_tail_tail_y(
    *,
    pivot_y: float,
    downstream_y: float,
    prefer_below: bool,
) -> float:
    if abs(downstream_y - pivot_y) > (1.5 * WIRE_EXTEND_MM):
        return pivot_y
    edge_y = max(pivot_y, downstream_y) if prefer_below else min(pivot_y, downstream_y)
    offset = (2.5 * WIRE_EXTEND_MM) + (1.27 if prefer_below else 0.0)
    return _snap_grid(edge_y + offset if prefer_below else edge_y - offset)


def _compact_vertical_tail_clearance_x(
    *,
    downstream_x: float,
    downstream_y: float,
    tail_y: float,
    positions: Mapping[str, tuple[float, float, float | None]] | None,
) -> float:
    clearance_x = downstream_x
    if positions is None or math.isclose(tail_y, downstream_y, abs_tol=0.01):
        return clearance_x
    for position in positions.values():
        bx = position[0]
        by = position[1]
        if _wire_crosses_box(
            downstream_x,
            tail_y,
            downstream_x,
            downstream_y,
            bx,
            by,
            SYMBOL_HALF_SIZE_MM,
        ):
            clearance_x = max(clearance_x, bx + (2 * SYMBOL_HALF_SIZE_MM))
    return clearance_x


def _compact_vertical_tail_route(
    endpoints: list[tuple[float, float]],
    *,
    coordinate: float,
    positions: Mapping[str, tuple[float, float, float | None]] | None = None,
    prefer_below: bool = False,
) -> tuple[list[WireSegment], list[JunctionPoint]]:
    """Route a compact asymmetric output tail with one long downstream run."""
    members = _compact_vertical_tail_members(endpoints, coordinate=coordinate)
    if members is None:
        return _chain_route(endpoints)

    upstream, pivot, downstream, exact_lane_match = members
    trunk_x = coordinate
    upstream_x, upstream_y = upstream
    pivot_x, pivot_y = pivot
    downstream_x, downstream_y = downstream
    tail_y = _compact_vertical_tail_tail_y(
        pivot_y=pivot_y,
        downstream_y=downstream_y,
        prefer_below=prefer_below,
    )
    clearance_x = _compact_vertical_tail_clearance_x(
        downstream_x=downstream_x,
        downstream_y=downstream_y,
        tail_y=tail_y,
        positions=positions,
    )

    segs: list[WireSegment] = []
    if not math.isclose(upstream_x, trunk_x, abs_tol=0.01):
        segs.append(WireSegment(upstream_x, upstream_y, trunk_x, upstream_y))
    if not math.isclose(upstream_y, tail_y, abs_tol=0.01):
        segs.append(WireSegment(trunk_x, upstream_y, trunk_x, tail_y))
    if exact_lane_match:
        if not math.isclose(pivot_y, tail_y, abs_tol=0.01):
            segs.append(WireSegment(pivot_x, pivot_y, pivot_x, tail_y))
        segs.append(WireSegment(pivot_x, tail_y, clearance_x, tail_y))
    else:
        if not math.isclose(pivot_x, trunk_x, abs_tol=0.01):
            segs.append(WireSegment(pivot_x, pivot_y, trunk_x, pivot_y))
        if not math.isclose(pivot_y, tail_y, abs_tol=0.01):
            segs.append(WireSegment(trunk_x, pivot_y, trunk_x, tail_y))
        segs.append(WireSegment(trunk_x, tail_y, clearance_x, tail_y))
    if not math.isclose(downstream_y, tail_y, abs_tol=0.01):
        segs.append(WireSegment(clearance_x, tail_y, clearance_x, downstream_y))
    if not math.isclose(clearance_x, downstream_x, abs_tol=0.01):
        segs.append(WireSegment(clearance_x, downstream_y, downstream_x, downstream_y))

    protected = {(round(x, 2), round(y, 2)) for x, y in endpoints}
    junctions: list[JunctionPoint] = []
    if not math.isclose(trunk_x, clearance_x, abs_tol=0.01):
        junctions.append(JunctionPoint(trunk_x, tail_y))
    if exact_lane_match and math.isclose(pivot_x, trunk_x, abs_tol=0.01):
        junctions = []
    return _simplify_wires(segs, protected_points=protected), junctions


def _best_compact_vertical_tail_route(
    endpoints: list[tuple[float, float]],
    *,
    preferred_coordinate: float,
    protected_points: set[tuple[float, float]] | None = None,
    positions: Mapping[str, tuple[float, float, float | None]] | None = None,
) -> tuple[list[WireSegment], list[JunctionPoint]]:
    """Choose the cleanest compact vertical tail route near the preferred lane."""
    preferred_route = _compact_vertical_tail_route(
        endpoints,
        coordinate=preferred_coordinate,
        positions=positions,
    )
    if not protected_points:
        return preferred_route

    candidate_coordinates = [preferred_coordinate]
    seen_coordinates = {round(preferred_coordinate, 2)}
    for offset in range(1, 5):
        for direction in (-1, 1):
            candidate = _snap_grid(preferred_coordinate + (direction * offset * 1.27))
            rounded = round(candidate, 2)
            if rounded in seen_coordinates:
                continue
            seen_coordinates.add(rounded)
            candidate_coordinates.append(candidate)

    best_choice: (
        tuple[
            tuple[int, float, int],
            float,
            tuple[list[WireSegment], list[JunctionPoint]],
        ]
        | None
    ) = None
    for candidate_coordinate in candidate_coordinates:
        for prefer_below in (False, True):
            candidate_route = _compact_vertical_tail_route(
                endpoints,
                coordinate=candidate_coordinate,
                positions=positions,
                prefer_below=prefer_below,
            )
            route, junctions = candidate_route
            route_key = _route_candidate_key(
                route,
                protected_points=protected_points,
                endpoints=endpoints,
            )
            choice = (route_key, abs(candidate_coordinate - preferred_coordinate), candidate_route)
            if best_choice is None or choice < best_choice:
                best_choice = choice

    assert best_choice is not None
    return best_choice[2]


def _compact_local_ground_cluster_route(
    cluster: list[tuple[PinRefIR, tuple[float, float, float]]],
    *,
    positions: Mapping[str, tuple[float, float, float | None]] | None = None,
) -> tuple[list[WireSegment], list[JunctionPoint], tuple[float, float]] | None:
    """Route a compact local 3-pin ground cluster on one calm horizontal lane.

    This is intentionally narrow: it only handles small horizontal GND groups
    where a lane anchored on the lowest stub end can avoid the member body boxes
    and the old centroid-based cluster route would otherwise create several
    short non-stub cleanup fragments.
    """
    if len(cluster) != 3:
        return None

    stub_ends = [_stub_end(x, y, angle) for _pin_ref, (x, y, angle) in cluster]
    xs = [point[0] for point in stub_ends]
    ys = [point[1] for point in stub_ends]
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)
    if x_span > 80.0 or y_span > 30.0:
        return None
    if x_span + 2.54 < y_span:
        return None

    lane_y = min(ys)
    lane_x0 = min(xs)
    lane_x1 = max(xs)
    vertical_target_x: dict[tuple[float, float], float] = {(x, y): x for x, y in stub_ends}
    if positions is not None:
        cluster_positions = [
            pos for pin_ref, _anchor in cluster if (pos := positions.get(pin_ref.ref)) is not None
        ]
        lane_candidates = sorted(set(ys))
        best_lane: tuple[float, float, dict[tuple[float, float], float]] | None = None
        for candidate_y in lane_candidates:
            candidate_targets = {(x, y): x for x, y in stub_ends}
            for x, y in stub_ends:
                if math.isclose(y, candidate_y, abs_tol=0.01):
                    continue
                clearance_x = x
                for bx, by, _rotation in cluster_positions:
                    if _wire_crosses_box(x, y, x, candidate_y, bx, by, SYMBOL_HALF_SIZE_MM):
                        clearance_x = min(clearance_x, bx - (2 * SYMBOL_HALF_SIZE_MM))
                candidate_targets[(x, y)] = _snap_grid(clearance_x)

            candidate_x0 = min(lane_x0, *candidate_targets.values())
            blocked = any(
                _wire_crosses_box(
                    candidate_x0,
                    candidate_y,
                    lane_x1,
                    candidate_y,
                    bx,
                    by,
                    SYMBOL_HALF_SIZE_MM,
                )
                for bx, by, _rotation in cluster_positions
            )
            if blocked:
                continue

            score = sum(abs(y - candidate_y) for _x, y in stub_ends) + sum(
                abs(x - candidate_targets[(x, y)]) for x, y in stub_ends
            )
            if (
                best_lane is None
                or score < best_lane[0]
                or (math.isclose(score, best_lane[0], abs_tol=0.01) and candidate_y < best_lane[1])
            ):
                best_lane = (score, candidate_y, candidate_targets)

        if best_lane is None:
            return None

        _score, lane_y, vertical_target_x = best_lane
        lane_x0 = min(lane_x0, *vertical_target_x.values())

    segs = [WireSegment(lane_x0, lane_y, lane_x1, lane_y)]
    junctions: list[JunctionPoint] = []
    for x, y in stub_ends:
        target_x = vertical_target_x[(x, y)]
        if not math.isclose(x, target_x, abs_tol=0.01):
            segs.append(WireSegment(x, y, target_x, y))
        if not math.isclose(y, lane_y, abs_tol=0.01):
            segs.append(WireSegment(target_x, y, target_x, lane_y))
        junctions.append(JunctionPoint(target_x, lane_y))

    symbol_x = _snap_grid(lane_x1 + (2 * SYMBOL_HALF_SIZE_MM))
    segs.append(WireSegment(lane_x1, lane_y, symbol_x, lane_y))
    protected = {(round(x, 2), round(y, 2)) for x, y in stub_ends}
    protected.add((round(lane_x1, 2), round(lane_y, 2)))
    return _simplify_wires(segs, protected_points=protected), junctions, (symbol_x, lane_y)


def _decoupling_ground_members(
    cluster: list[tuple[PinRefIR, tuple[float, float, float]]],
) -> (
    tuple[
        list[tuple[PinRefIR, tuple[float, float, float]]],
        list[tuple[PinRefIR, tuple[float, float, float]]],
    ]
    | None
):
    """Return decoupling-cap and support members for a local decoupling GND cluster."""
    if not 2 <= len(cluster) <= 5:
        return None

    capacitor_members = [
        (pin_ref, anchor) for pin_ref, anchor in cluster if pin_ref.ref.upper().startswith("C")
    ]
    support_members = [
        (pin_ref, anchor) for pin_ref, anchor in cluster if not pin_ref.ref.upper().startswith("C")
    ]
    if len(capacitor_members) < 2 or len(support_members) > 2:
        return None
    for support_pin_ref, _anchor in support_members:
        support_kind = _component_type(support_pin_ref.ref)
        if support_kind not in {"ic", "passive"}:
            return None

    return capacitor_members, support_members


def _choose_compact_ground_lane(
    *,
    cluster: list[tuple[PinRefIR, tuple[float, float, float]]],
    stub_ends: list[tuple[float, float]],
    lane_bounds: tuple[float, float],
    avg_y: float,
    positions: Mapping[str, tuple[float, float, float | None]] | None,
) -> tuple[float, dict[tuple[float, float], float], float]:
    """Return the best local ground lane candidate for a compact cluster."""
    ys = [point[1] for point in stub_ends]
    lane_y = min(sorted(set(ys)), key=lambda y: abs(y - avg_y))
    vertical_target_x: dict[tuple[float, float], float] = {(x, y): x for x, y in stub_ends}
    lane_x0, lane_x1 = lane_bounds
    if positions is None:
        return lane_y, vertical_target_x, lane_x0

    positioned_cluster = [
        (pin_ref.ref, pos, stub_ends[index])
        for index, (pin_ref, _anchor) in enumerate(cluster)
        if (pos := positions.get(pin_ref.ref)) is not None
    ]
    lane_candidates = sorted(set(ys))
    best_lane: tuple[float, float, dict[tuple[float, float], float], float] | None = None
    for candidate_y in lane_candidates:
        candidate_targets = {(x, y): x for x, y in stub_ends}
        for x, y in stub_ends:
            if math.isclose(y, candidate_y, abs_tol=0.01):
                continue
            clearance_x = x
            for ref, (bx, by, _rotation), member_stub in positioned_cluster:
                is_own_member = math.isclose(member_stub[0], x, abs_tol=0.01) and math.isclose(
                    member_stub[1], y, abs_tol=0.01
                )
                if _wire_crosses_box(x, y, x, candidate_y, bx, by, SYMBOL_HALF_SIZE_MM):
                    clearance_x = min(
                        clearance_x,
                        _compact_cluster_detour_x(
                            ref,
                            bx,
                            crossing_own_member=is_own_member,
                        ),
                    )
            candidate_targets[(x, y)] = _snap_grid(clearance_x)

        candidate_x0 = min(lane_x0, *candidate_targets.values())
        blocked = any(
            _wire_crosses_box(
                candidate_x0,
                candidate_y,
                lane_x1,
                candidate_y,
                bx,
                by,
                SYMBOL_HALF_SIZE_MM,
            )
            for _ref, (bx, by, _rotation), _member_stub in positioned_cluster
        )
        if blocked:
            continue

        vertical_cost = sum(abs(y - candidate_y) for _x, y in stub_ends)
        horizontal_cost = sum(abs(x - candidate_targets[(x, y)]) for x, y in stub_ends)
        centering_cost = abs(candidate_y - avg_y)
        score = vertical_cost + horizontal_cost + centering_cost
        if (
            best_lane is None
            or score < best_lane[0]
            or (
                math.isclose(score, best_lane[0], abs_tol=0.01)
                and centering_cost < abs(best_lane[1] - avg_y)
            )
        ):
            best_lane = (score, candidate_y, candidate_targets, candidate_x0)

    if best_lane is None:
        return lane_y, vertical_target_x, lane_x0

    _score, lane_y, vertical_target_x, lane_x0 = best_lane
    return lane_y, vertical_target_x, lane_x0


def _compact_local_decoupling_ground_cluster_route(
    cluster: list[tuple[PinRefIR, tuple[float, float, float]]],
    *,
    positions: Mapping[str, tuple[float, float, float | None]] | None = None,
) -> tuple[list[WireSegment], list[JunctionPoint], tuple[float, float]] | None:
    """Route a compact local GND lane for a small decoupling support cluster.

    This extends the original cap-only helper by allowing up to two nearby
    local support members such as power-unit ground pins or shunt elements to
    share the same calm ground lane as the decoupling bank.
    """
    members = _decoupling_ground_members(cluster)
    if members is None:
        return None
    capacitor_members, _support_members = members

    cap_stub_ends = [_stub_end(x, y, angle) for _pin_ref, (x, y, angle) in capacitor_members]
    cap_xs = [point[0] for point in cap_stub_ends]
    cap_ys = [point[1] for point in cap_stub_ends]
    cap_x_span = max(cap_xs) - min(cap_xs)
    cap_y_span = max(cap_ys) - min(cap_ys)
    if cap_x_span > 50.0 or cap_y_span > 60.0:
        return None

    stub_ends = [_stub_end(x, y, angle) for _pin_ref, (x, y, angle) in cluster]
    xs = [point[0] for point in stub_ends]
    ys = [point[1] for point in stub_ends]
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)
    if x_span > 80.0 or y_span > 60.0:
        return None

    lane_x0 = min(xs)
    lane_x1 = max(xs)
    avg_y = sum(cap_ys) / len(cap_ys)
    lane_y, vertical_target_x, lane_x0 = _choose_compact_ground_lane(
        cluster=cluster,
        stub_ends=stub_ends,
        lane_bounds=(lane_x0, lane_x1),
        avg_y=avg_y,
        positions=positions,
    )

    segs = [WireSegment(lane_x0, lane_y, lane_x1, lane_y)]
    junctions: list[JunctionPoint] = []
    for x, y in stub_ends:
        target_x = vertical_target_x[(x, y)]
        if not math.isclose(x, target_x, abs_tol=0.01):
            segs.append(WireSegment(x, y, target_x, y))
        if not math.isclose(y, lane_y, abs_tol=0.01):
            segs.append(WireSegment(target_x, y, target_x, lane_y))
        junctions.append(JunctionPoint(target_x, lane_y))

    symbol_x = _snap_grid(lane_x1 + (2 * SYMBOL_HALF_SIZE_MM))
    segs.append(WireSegment(lane_x1, lane_y, symbol_x, lane_y))
    protected = {(round(x, 2), round(y, 2)) for x, y in stub_ends}
    return _simplify_wires(segs, protected_points=protected), junctions, (symbol_x, lane_y)


def _compact_local_decoupling_power_cluster_route(  # noqa: PLR0911, PLR0915
    net_name: str,
    cluster: list[tuple[PinRefIR, tuple[float, float, float]]],
    *,
    positions: Mapping[str, tuple[float, float, float | None]] | None = None,
) -> tuple[list[WireSegment], list[JunctionPoint], tuple[float, float]] | None:
    """Route a compact decoupling rail on one calm horizontal lane.

    This targets small local supply groups such as ``U1P/C1/C3`` or
    ``U1P/C2/C4`` where the generic centroid cluster route produces a noisy
    rail knot around the decoupler bank.  The rail is pulled onto a single
    horizontal lane near the IC/capacitor members so the decouplers read as
    short local drops from a compact local rail.
    """
    if len(cluster) < 2 or len(cluster) > 4:
        return None
    rail_polarity = power_rail_polarity(net_name)
    if rail_polarity is None:
        return None

    component_kinds = [_component_type(pin_ref.ref) for pin_ref, _anchor in cluster]
    has_capacitor = any(pin_ref.ref.upper().startswith("C") for pin_ref, _anchor in cluster)
    if "ic" not in component_kinds or not has_capacitor:
        return None

    stub_ends = [_stub_end(x, y, angle) for _pin_ref, (x, y, angle) in cluster]
    xs = [point[0] for point in stub_ends]
    ys = [point[1] for point in stub_ends]
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)
    if x_span > 90.0 or y_span > 70.0:
        return None
    if len(cluster) > 2 and x_span + 15.0 < y_span:
        return None

    local_points = [
        stub_ends[index]
        for index, (pin_ref, _anchor) in enumerate(cluster)
        if _component_type(pin_ref.ref) != "connector"
    ]
    if len(local_points) < 2:
        return None

    if (
        rail_polarity == "positive"
        and len(cluster) == 2
        and not math.isclose(stub_ends[0][0], stub_ends[1][0], abs_tol=0.01)
    ):
        lane_x0 = min(xs)
        lane_x1 = max(xs)
        lane_y = min(point[1] for point in local_points)
        segs: list[WireSegment] = []
        if not math.isclose(lane_x0, lane_x1, abs_tol=0.01):
            segs.append(WireSegment(lane_x0, lane_y, lane_x1, lane_y))
        initial_junctions = [JunctionPoint(x, lane_y) for x, _y in stub_ends]
        for x, y in stub_ends:
            if not math.isclose(y, lane_y, abs_tol=0.01):
                segs.append(WireSegment(x, y, x, lane_y))
        symbol_x = _snap_grid(lane_x1 + (2 * SYMBOL_HALF_SIZE_MM))
        segs.append(WireSegment(lane_x1, lane_y, symbol_x, lane_y))
        protected = {(round(x, 2), round(y, 2)) for x, y in stub_ends}
        return (
            _simplify_wires(segs, protected_points=protected),
            initial_junctions,
            (symbol_x, lane_y),
        )

    lane_x1 = max(xs)
    prefer_upper_lane = rail_polarity == "positive"
    lane_y = (
        min(point[1] for point in local_points)
        if prefer_upper_lane
        else max(point[1] for point in local_points)
    )
    vertical_target_x: dict[tuple[float, float], float] = {(x, y): x for x, y in stub_ends}

    if positions is not None:
        positioned_cluster = [
            (pin_ref.ref, pos, stub_ends[index])
            for index, (pin_ref, _anchor) in enumerate(cluster)
            if (pos := positions.get(pin_ref.ref)) is not None
        ]
        lane_candidates = sorted(
            {point[1] for point in local_points},
            reverse=not prefer_upper_lane,
        )
        best_lane: tuple[float, float, dict[tuple[float, float], float], float] | None = None
        for candidate_y in lane_candidates:
            candidate_targets = {(x, y): x for x, y in stub_ends}
            for x, y in stub_ends:
                if math.isclose(y, candidate_y, abs_tol=0.01):
                    continue
                clearance_x = x
                for _ref, (bx, by, _rotation), _member_stub in positioned_cluster:
                    is_own_member = math.isclose(_member_stub[0], x, abs_tol=0.01) and math.isclose(
                        _member_stub[1], y, abs_tol=0.01
                    )
                    if is_own_member and _ref.upper().startswith("C"):
                        box_top = by - SYMBOL_HALF_SIZE_MM
                        box_bottom = by + SYMBOL_HALF_SIZE_MM
                        if (
                            bx - SYMBOL_HALF_SIZE_MM <= x <= bx + SYMBOL_HALF_SIZE_MM
                            and min(y, candidate_y) < box_bottom
                            and max(y, candidate_y) > box_top
                        ):
                            clearance_x = min(
                                clearance_x,
                                _compact_cluster_detour_x(
                                    _ref,
                                    bx,
                                    crossing_own_member=True,
                                ),
                            )
                    if (
                        math.isclose(_member_stub[0], x, abs_tol=0.01)
                        and math.isclose(_member_stub[1], y, abs_tol=0.01)
                        and not (_ref.upper().startswith("C") or _component_type(_ref) == "ic")
                    ):
                        continue
                    if _wire_crosses_box(x, y, x, candidate_y, bx, by, SYMBOL_HALF_SIZE_MM):
                        clearance_x = min(
                            clearance_x,
                            _compact_cluster_detour_x(
                                _ref,
                                bx,
                                crossing_own_member=is_own_member,
                            ),
                        )
                candidate_targets[(x, y)] = _snap_grid(clearance_x)

            candidate_x0 = min(*xs, *candidate_targets.values())
            blocked_positions = [
                pos
                for _ref, pos, (_stub_x, stub_y) in positioned_cluster
                if not math.isclose(stub_y, candidate_y, abs_tol=0.01)
            ]
            blocked = any(
                _wire_crosses_box(
                    candidate_x0,
                    candidate_y,
                    lane_x1,
                    candidate_y,
                    bx,
                    by,
                    SYMBOL_HALF_SIZE_MM,
                )
                for bx, by, _rotation in blocked_positions
            )
            if blocked:
                continue

            vertical_cost = sum(abs(y - candidate_y) for _x, y in local_points)
            horizontal_cost = sum(abs(x - candidate_targets[(x, y)]) for x, y in stub_ends)
            score = vertical_cost + horizontal_cost
            if (
                best_lane is None
                or score < best_lane[0]
                or (
                    math.isclose(score, best_lane[0], abs_tol=0.01)
                    and (
                        candidate_y < best_lane[1]
                        if prefer_upper_lane
                        else candidate_y > best_lane[1]
                    )
                )
            ):
                best_lane = (score, candidate_y, candidate_targets, candidate_x0)

        if best_lane is not None:
            _score, lane_y, vertical_target_x, lane_x0 = best_lane
        else:
            lane_x0 = min(xs)
    else:
        lane_x0 = min(xs)

    segs = [WireSegment(lane_x0, lane_y, lane_x1, lane_y)]
    result_junctions: list[JunctionPoint] = []
    for x, y in stub_ends:
        target_x = vertical_target_x[(x, y)]
        if not math.isclose(x, target_x, abs_tol=0.01):
            segs.append(WireSegment(x, y, target_x, y))
        if not math.isclose(y, lane_y, abs_tol=0.01):
            segs.append(WireSegment(target_x, y, target_x, lane_y))
        result_junctions.append(JunctionPoint(target_x, lane_y))

    symbol_x = _snap_grid(lane_x1 + (2 * SYMBOL_HALF_SIZE_MM))
    segs.append(WireSegment(lane_x1, lane_y, symbol_x, lane_y))
    protected = {(round(x, 2), round(y, 2)) for x, y in stub_ends}
    return _simplify_wires(segs, protected_points=protected), result_junctions, (symbol_x, lane_y)


def _assign_connector_entry_grouped_lanes(
    grouped_names: list[str],
    *,
    axis: str,
    base_coordinate: float,
    endpoints_by_net: dict[str, list[tuple[float, float]]],
    connector_entry_x_by_net: dict[str, float],
) -> dict[str, SharedLanePlan] | None:
    """Return the special connector-entry lane assignment for one grouped component."""
    connector_entry_names = [
        net_name for net_name in grouped_names if net_name in connector_entry_x_by_net
    ]
    if axis != "vertical" or len(connector_entry_names) != 1:
        return None

    entry_net = connector_entry_names[0]
    entry_coordinate = round(connector_entry_x_by_net[entry_net] + WIRE_EXTEND_MM, 2)
    if entry_coordinate >= round(base_coordinate, 2):
        return None

    planned_routes: dict[str, SharedLanePlan] = {entry_net: SharedLanePlan(axis, entry_coordinate)}
    remaining_names = [name for name in grouped_names if name != entry_net]
    for index, net_name in enumerate(remaining_names):
        offset = (index + 1.25) * WIRE_EXTEND_MM
        coordinate = round(base_coordinate + offset, 2)
        if len(remaining_names) == 1:
            shared_points = [
                point
                for point in endpoints_by_net[net_name]
                if math.isclose(point[0], base_coordinate, abs_tol=0.01)
            ]
            other_points = [
                point
                for point in endpoints_by_net[net_name]
                if not math.isclose(point[0], base_coordinate, abs_tol=0.01)
            ]
            if len(shared_points) == 2 and len(other_points) == 1:
                other_y = other_points[0][1]
                anchor_y = min(
                    (point[1] for point in shared_points),
                    key=lambda y: abs(y - other_y),
                )
                planned_routes[net_name] = SharedLanePlan(
                    axis,
                    coordinate,
                    min(anchor_y, other_y),
                    max(anchor_y, other_y),
                )
                continue
        planned_routes[net_name] = SharedLanePlan(axis, coordinate)
    return planned_routes


def _infer_bounded_local_lane_plan(
    endpoints: list[tuple[float, float]],
) -> SharedLanePlan | None:
    """Infer a bounded ladder lane for compact 3-pin nets without an exact shared axis.

    This covers full-layout cases where two nearby pins are visually aligned as a
    ladder rung but land on slightly different coordinates after symbol placement.
    """
    if len(endpoints) != 3:
        return None

    xs = [point[0] for point in endpoints]
    ys = [point[1] for point in endpoints]
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)

    indexed_points = list(enumerate(endpoints))
    horizontal_pairs = sorted(
        (
            abs(first[1][1] - second[1][1]),
            first[0],
            second[0],
        )
        for first in indexed_points
        for second in indexed_points
        if first[0] < second[0]
    )
    vertical_pairs = sorted(
        (
            abs(first[1][0] - second[1][0]),
            first[0],
            second[0],
        )
        for first in indexed_points
        for second in indexed_points
        if first[0] < second[0]
    )

    horizontal_gap, horizontal_i, horizontal_j = horizontal_pairs[0]
    vertical_gap, vertical_i, vertical_j = vertical_pairs[0]
    if min(horizontal_gap, vertical_gap) > WIRE_EXTEND_MM:
        return None

    if x_span >= y_span:
        pair_i, pair_j = horizontal_i, horizontal_j
        axis = "horizontal"
        third_index = next(index for index in range(3) if index not in {pair_i, pair_j})
        pair_points = [endpoints[pair_i], endpoints[pair_j]]
        third_point = endpoints[third_index]
        coordinate = min(
            (pair_points[0][1], pair_points[1][1]),
            key=lambda value: abs(value - third_point[1]),
        )
        return SharedLanePlan(
            axis,
            round(coordinate, 2),
            round(min(pair_points[0][0], pair_points[1][0]), 2),
            round(max(pair_points[0][0], pair_points[1][0]), 2),
        )

    pair_i, pair_j = vertical_i, vertical_j
    axis = "vertical"
    third_index = next(index for index in range(3) if index not in {pair_i, pair_j})
    pair_points = [endpoints[pair_i], endpoints[pair_j]]
    third_point = endpoints[third_index]
    coordinate = min(
        (pair_points[0][0], pair_points[1][0]),
        key=lambda value: abs(value - third_point[0]),
    )
    return SharedLanePlan(
        axis,
        round(coordinate, 2),
        round(min(pair_points[0][1], pair_points[1][1]), 2),
        round(max(pair_points[0][1], pair_points[1][1]), 2),
    )


def _collect_local_ladder_candidates(
    ir: CircuitIR,
    pin_anchors: Mapping[tuple[str, str], PinAnchor],
) -> tuple[
    dict[str, tuple[float, float, float, float]],
    dict[str, int],
    dict[str, tuple[str, float]],
    dict[str, list[tuple[float, float]]],
    dict[str, tuple[str, ...]],
    dict[str, float],
]:
    """Collect compact local nets that may participate in ladder routing."""
    candidate_boxes: dict[str, tuple[float, float, float, float]] = {}
    candidate_degree: dict[str, int] = {}
    shared_lane_by_net: dict[str, tuple[str, float]] = {}
    endpoints_by_net: dict[str, list[tuple[float, float]]] = {}
    refs_by_net: dict[str, tuple[str, ...]] = {}
    connector_entry_x_by_net: dict[str, float] = {}

    for net in ir.nets:
        if _is_power_net_name(net.name):
            continue

        endpoints: list[tuple[float, float]] = []
        for pin in net.pins:
            anchor = pin_anchors.get((pin.ref, pin.pin))
            if anchor is None:
                endpoints = []
                break
            endpoints.append(_stub_end(anchor.x, anchor.y, anchor.angle))

        if len(endpoints) < 2 or len(endpoints) > 3:
            continue
        if not _is_local_ladder_net(endpoints):
            continue

        endpoints_by_net[net.name] = endpoints
        refs_by_net[net.name] = tuple(pin.ref for pin in net.pins)
        xs = [point[0] for point in endpoints]
        ys = [point[1] for point in endpoints]
        candidate_boxes[net.name] = (min(xs), max(xs), min(ys), max(ys))
        candidate_degree[net.name] = len(endpoints)

        if any(_component_type(pin.ref) == "connector" for pin in net.pins):
            connector_entry_x_by_net[net.name] = min(xs)

        lane = _preferred_shared_lane(endpoints)
        if lane is not None:
            shared_lane_by_net[net.name] = lane

    return (
        candidate_boxes,
        candidate_degree,
        shared_lane_by_net,
        endpoints_by_net,
        refs_by_net,
        connector_entry_x_by_net,
    )


def _build_ladder_adjacency(
    candidate_boxes: dict[str, tuple[float, float, float, float]],
) -> dict[str, set[str]]:
    """Connect compact local nets whose bounding boxes nearly touch."""
    adjacency: dict[str, set[str]] = {name: set() for name in candidate_boxes}
    names = sorted(candidate_boxes)
    for index, net_name in enumerate(names):
        for other_name in names[index + 1 :]:
            if not _boxes_touch_or_overlap(candidate_boxes[net_name], candidate_boxes[other_name]):
                continue
            adjacency[net_name].add(other_name)
            adjacency[other_name].add(net_name)
    return adjacency


def _connected_ladder_component(
    start_name: str,
    adjacency: dict[str, set[str]],
    visited: set[str],
) -> list[str]:
    """Return one connected component from the ladder-neighborhood graph."""
    stack = [start_name]
    component: list[str] = []
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        component.append(current)
        stack.extend(sorted(adjacency[current] - visited))
    return component


def _lane_center(
    net_name: str,
    axis: str,
    endpoints_by_net: dict[str, list[tuple[float, float]]],
) -> float:
    """Return the orthogonal-axis center used to order parallel local lanes."""
    coordinates = endpoints_by_net[net_name]
    if axis == "vertical":
        return sum(point[1] for point in coordinates) / len(coordinates)
    return sum(point[0] for point in coordinates) / len(coordinates)


def _assign_grouped_ladder_lanes(
    component: list[str],
    *,
    context: LadderLanePlannerContext,
) -> dict[str, SharedLanePlan]:
    """Assign distinct parallel lanes to all 3-pin nets in one neighborhood."""
    grouped: dict[tuple[str, float], list[str]] = {}
    for net_name in component:
        if context.candidate_degree.get(net_name) != 3:
            continue
        lane = context.shared_lane_by_net.get(net_name)
        if lane is None:
            continue
        axis, base_coordinate = lane
        grouped.setdefault((axis, round(base_coordinate, 2)), []).append(net_name)

    planned_routes: dict[str, SharedLanePlan] = {}
    skipped_single_lane_nets: set[str] = set()
    for (axis, base_coordinate), grouped_names in grouped.items():
        grouped_names.sort(
            key=lambda net_name: _lane_center(net_name, axis, context.endpoints_by_net)
        )
        if len(grouped_names) == 1:
            endpoints = context.endpoints_by_net[grouped_names[0]]
            single_lane_plan = _plan_single_grouped_ladder_lane(
                axis=axis,
                base_coordinate=base_coordinate,
                endpoints=endpoints,
                refs=context.refs_by_net.get(grouped_names[0], ()),
                heuristic_policy=context.heuristic_policy,
            )
            if single_lane_plan is None:
                skipped_single_lane_nets.add(grouped_names[0])
                continue
            planned_routes[grouped_names[0]] = single_lane_plan
            continue

        connector_group_routes = _assign_connector_entry_grouped_lanes(
            grouped_names,
            axis=axis,
            base_coordinate=base_coordinate,
            endpoints_by_net=context.endpoints_by_net,
            connector_entry_x_by_net=context.connector_entry_x_by_net,
        )
        if connector_group_routes is not None:
            planned_routes.update(connector_group_routes)
            continue

        for index, net_name in enumerate(grouped_names):
            offset = (index - (len(grouped_names) - 1) / 2) * WIRE_EXTEND_MM
            planned_routes[net_name] = SharedLanePlan(axis, round(base_coordinate + offset, 2))

    for net_name in component:
        if net_name in planned_routes or context.candidate_degree.get(net_name) != 3:
            continue
        if net_name in skipped_single_lane_nets:
            continue
        inferred_plan = _infer_bounded_local_lane_plan(context.endpoints_by_net[net_name])
        if inferred_plan is None:
            continue
        if context.heuristic_policy.should_skip_shared_lane_plan(
            context.endpoints_by_net[net_name],
            inferred_plan,
        ) or context.heuristic_policy.should_prefer_small_analog_chain(
            context.endpoints_by_net[net_name],
            inferred_plan=inferred_plan,
            refs=context.refs_by_net.get(net_name, ()),
        ):
            continue
        planned_routes[net_name] = inferred_plan

    return planned_routes


def _plan_local_ladder_routes(
    ir: CircuitIR,
    pin_anchors: Mapping[tuple[str, str], PinAnchor | tuple[float, float, float]] | None = None,
    *,
    pin_endpoints: Mapping[tuple[str, str], tuple[float, float, float]] | None = None,
    heuristic_policy: RoutingHeuristicPolicy = DEFAULT_ROUTING_HEURISTIC_POLICY,
) -> dict[str, SharedLanePlan]:
    """Detect nearby small-signal net neighborhoods that should share ladder-style routing.

    The detector intentionally works across multiple nets. A 3-pin local net is
    considered a ladder candidate only when it lives in a compact neighborhood
    with at least one adjacent local 2-pin or 3-pin signal net.
    """
    resolved_anchors = _resolve_pin_anchors(
        pin_endpoints or {},
        _coerce_pin_anchor_map(pin_anchors),
    )
    (
        candidate_boxes,
        candidate_degree,
        shared_lane_by_net,
        endpoints_by_net,
        refs_by_net,
        connector_entry_x_by_net,
    ) = _collect_local_ladder_candidates(ir, resolved_anchors)
    planner_context = LadderLanePlannerContext(
        candidate_degree=candidate_degree,
        shared_lane_by_net=shared_lane_by_net,
        endpoints_by_net=endpoints_by_net,
        refs_by_net=refs_by_net,
        connector_entry_x_by_net=connector_entry_x_by_net,
        heuristic_policy=heuristic_policy,
    )
    adjacency = _build_ladder_adjacency(candidate_boxes)

    visited: set[str] = set()
    planned_routes: dict[str, SharedLanePlan] = {}

    for net_name in sorted(candidate_boxes):
        if net_name in visited:
            continue

        component = _connected_ladder_component(net_name, adjacency, visited)
        if len(component) < 2:
            continue

        planned_routes.update(
            _assign_grouped_ladder_lanes(
                component,
                context=planner_context,
            )
        )

    return planned_routes


def _chain_route(
    endpoints: list[tuple[float, float]],
    *,
    protected_points: set[tuple[float, float]] | None = None,
) -> tuple[list[WireSegment], list[JunctionPoint]]:
    """Route a compact 3-pin net as a simple ordered chain.

    Endpoints are ordered along the dominant axis and connected pairwise with
    direct orthogonal segments. This avoids the boxy mini-bus look that the
    generic spine route can produce for short local support nets.
    """
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
    """Route a 3-pin buffer follower net as a short local loop plus branch.

    This targets the common unity-gain-buffer pattern where two pins belong to
    the same placed ``BUFFER_STAGE`` unit and the third pin is the first
    downstream output-support element. Rather than drawing those three points as
    a generic left-to-right chain, the route explicitly preserves a compact
    output-to-inverting feedback loop around the buffer stage and lets the
    output branch leave from the output-side pin.
    """

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


def _simplify_wires(  # noqa: PLR0912, PLR0915
    wires: list[WireSegment],
    *,
    protected_points: set[tuple[float, float]] | None = None,
) -> list[WireSegment]:
    """Simplify wire routing by merging consecutive colinear segments.

    Reduces visual clutter by combining wire segments that lie on the same
    horizontal or vertical line into longer, direct wire runs.  This
    eliminates unnecessary intermediate junctions and shortens the total
    wire segment count (Phase 6.1).

    Parameters
    ----------
    wires:
        Input wire segments; order-independent.
    protected_points:
        Optional set of (x, y) coordinates that must be preserved as wire
        start/end points.  Typically pin endpoints.  Segments will not be
        merged if doing so would eliminate a protected point.

    Returns
    -------
    list[WireSegment]
        Simplified wire list with consecutive colinear segments merged,
        preserving all protected connection points and junction points.

    Notes
    -----
    The algorithm iteratively merges pairs of segments that:

    * Share at least one endpoint.
    * Lie on the same horizontal line (y₁ = y₂) or vertical line (x₁ = x₂).
    * The shared endpoint is NOT in the protected set.
    * The shared endpoint is NOT a junction (degree ≥ 3).

    Merging continues until no more pairs can be combined.  The resulting
    list typically contains fewer short wire segments and a cleaner visual
    appearance.
    """
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

            # Try to find a mergeable partner
            merged = False
            for j in range(i + 1, len(segments)):
                if j in used:
                    continue

                seg2 = segments[j]

                # Find shared endpoints (rounded to 0.01 mm for floating-point stability)
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

                # Don't merge if shared endpoint is protected
                if any(pt in protected_points for pt in shared):
                    continue

                # Don't merge if shared endpoint is a junction (degree >= 3)
                if any(degree.get(pt, 0) >= 3 for pt in shared):
                    continue

                # Check if segments are colinear
                # Horizontal segments (both have y1 == y2)
                if seg1.y1 == seg1.y2 and seg2.y1 == seg2.y2 and seg1.y1 == seg2.y1:
                    # Get all x coordinates
                    xs = sorted([seg1.x1, seg1.x2, seg2.x1, seg2.x2])
                    # Merge: use min and max x coordinates
                    new_segments.append(WireSegment(xs[0], seg1.y1, xs[-1], seg1.y1))
                    used.add(i)
                    used.add(j)
                    merged = True
                    changed = True
                    break

                # Vertical segments (both have x1 == x2)
                elif seg1.x1 == seg1.x2 and seg2.x1 == seg2.x2 and seg1.x1 == seg2.x1:
                    # Get all y coordinates
                    ys = sorted([seg1.y1, seg1.y2, seg2.y1, seg2.y2])
                    # Merge: use min and max y coordinates
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
                if math.isclose(point[0], x1, abs_tol=0.01)
                and min(y1, y2) < point[1] < max(y1, y2)
            )
            if y2 < y1:
                candidate_points.reverse()
        elif math.isclose(y1, y2, abs_tol=0.01):
            candidate_points = sorted(
                point
                for point in split_points
                if math.isclose(point[1], y1, abs_tol=0.01)
                and min(x1, x2) < point[0] < max(x1, x2)
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


def _clamp_emitted_power_symbols(
    power_symbols: list[PowerSymbolPlacement],
    *,
    emitted_wires: list[WireSegment],
) -> list[PowerSymbolPlacement]:
    """Clamp emitted power symbols onto the page and extend wires to match."""
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
            if (
                (cx - SYMBOL_HALF_SIZE_MM) <= px <= (cx + SYMBOL_HALF_SIZE_MM)
                and (cy - SYMBOL_HALF_SIZE_MM) <= py <= (cy + SYMBOL_HALF_SIZE_MM)
            ):
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
# Public API
# ---------------------------------------------------------------------------
def route_nets(  # noqa: PLR0912, PLR0913, PLR0915
    *,
    ir: CircuitIR,
    pin_endpoints: dict[tuple[str, str], tuple[float, float, float]],
    pin_anchors: Mapping[tuple[str, str], PinAnchor] | None = None,
    block_layout: BlockLayout | None = None,
    use_bus: bool = True,
    tiers: dict[str, int] | None = None,
    positions: Mapping[str, tuple[float, float, float | None]] | None = None,
    policy: LabelPolicy = DEFAULT_LABEL_POLICY,
    heuristic_policy: RoutingHeuristicPolicy = DEFAULT_ROUTING_HEURISTIC_POLICY,
    strict: bool = False,
) -> NetRouting:
    """Compute routing decisions for all nets in *ir*.

    Strategy per net:

    * **Power net** (`GND`, `VCC`, etc.) — emit a
      :class:`GlobalLabelPlacement` (power shape) per pin; avoids spaghetti
      on high-fanout rails.
    * **Direct route** — exactly 2 known pins.  Routing condition (Rule §4):

      * When *tiers* is provided: direct route is used only when the
        tier distance between the two pins is ≤ 1 **and** the Manhattan
        distance is ≤ :data:`MAX_DIRECT_DIST_MM`.  The tier-distance guard
        prevents cross-tier spaghetti; the Manhattan cap prevents wire runs
        so long they become unreadable.
      * When *tiers* is ``None`` (default): the tier guard is skipped;
        only the Manhattan-distance cap (:data:`MAX_DIRECT_DIST_MM`) applies.

      No net labels are emitted for direct routes.
    * **Hub route** — 3–:data:`_HUB_MAX_DEGREE` known pins, all endpoints
      reachable: route spokes to a centroid hub; add a
      :class:`JunctionPoint` at the hub.  No net labels emitted.
      When *use_bus* is ``True`` the hub strategy is replaced by
      :func:`_spine_route` which draws a straight spine wire with
      T-junction taps for a cleaner bus-style visual.
    * **Global-label route** — high-degree non-power nets (> ``_HUB_MAX_DEGREE``):
      emit one :class:`GlobalLabelPlacement` per pin (same result as power
      nets but using the ``passive`` shape so it's visually distinct).
    * **Label route** — fallback for anything else: one stub wire + one
      local net label per pin.

    Unknown pins (absent from the resolved anchor set) always fall back to an
    off-canvas position with local labels.

    Parameters
    ----------
    ir:
        Circuit IR with nets and components.
    pin_endpoints:
        ``{(ref, pin): (x, y, angle)}`` map produced by the schematic builder.
    pin_anchors:
        Optional ``{(ref, pin): PinAnchor}`` map carrying explicit placed-unit
        anchor ownership plus endpoint geometry. When supplied, router helpers
        use this richer model as their source of truth and only fall back to
        *pin_endpoints* for legacy callers.
    block_layout:
        Optional functional block classification from
        :func:`kicad_pcb.block_detection.classify_circuit`. When supplied,
        net classification prefers structural roles before falling back to
        net-name heuristics.
    use_bus:
        When ``True``, replace centroid-hub routing for multi-pin local nets
        with spine-style routing (:func:`_spine_route`).  Produces a cleaner
        "one long wire with taps" visual instead of star-shaped spokes.
    tiers:
        Optional ``{ref: tier_index}`` map.  When supplied, tier distance
        governs the direct-route decision instead of Manhattan distance.
    positions:
        Optional ``{ref: (x, y, rotation)}`` layout position map.  When
        supplied, wire segments that cross component bounding boxes are
        automatically rerouted via :func:`detect_body_crossings`.

    policy:
        Label deduplication policy; controls how many local and global
        labels are emitted per net.  Defaults to
        :data:`DEFAULT_LABEL_POLICY` (2 local labels per net, 4 global
        labels per high-degree net). Use :data:`LABEL_MODE_POLICIES` for the
        bundled ``minimal``, ``debug``, and ``always-show-important-labels`` modes.
    heuristic_policy:
        Analog-specific routing policy that governs compact output-tail and
        local-ground-cluster special cases while leaving generic routing
        strategies unchanged.
    strict:
        When ``True``, unknown pin endpoints are treated as an error instead
        of falling back to off-canvas stub+label/symbol routing.

    Returns a :class:`NetRouting` with all decisions.
    """
    routing = NetRouting()
    fallback_y = -1500.0
    resolved_anchors = _resolve_pin_anchors(pin_endpoints, pin_anchors)
    protected_pin_points = {
        (
            round(anchor.x, 2),
            round(anchor.y, 2),
        )
        for anchor in resolved_anchors.values()
    }
    protected_stub_point_counts = Counter(
        (
            round(stub_x, 2),
            round(stub_y, 2),
        )
        for anchor in resolved_anchors.values()
        for stub_x, stub_y in [_stub_end(anchor.x, anchor.y, anchor.angle)]
    )
    protected_stub_points = set(protected_stub_point_counts)
    shared_protected_stub_points = {
        point for point, count in protected_stub_point_counts.items() if count > 1
    }
    ladder_routes = _plan_local_ladder_routes(
        ir,
        resolved_anchors,
        heuristic_policy=heuristic_policy,
    )

    for net in sorted(ir.nets, key=lambda n: n.name):
        pins = sorted(net.pins, key=lambda p: (p.ref, p.pin))
        net_refs = tuple(dict.fromkeys(pin.ref for pin in pins))
        known = [
            (
                p,
                (
                    resolved_anchors[(p.ref, p.pin)].x,
                    resolved_anchors[(p.ref, p.pin)].y,
                    resolved_anchors[(p.ref, p.pin)].angle,
                ),
            )
            for p in pins
            if (p.ref, p.pin) in resolved_anchors
        ]
        unknown = [p for p in pins if (p.ref, p.pin) not in resolved_anchors]

        if strict and unknown:
            raise UserError(
                f"Cannot route net '{net.name}' with unknown pin endpoints in strict mode",
                code=ErrorCode.PIN_INVALID,
                details={
                    "net_name": net.name,
                    "missing_pins": [
                        {
                            "ref": pin.ref,
                            "pin": pin.pin,
                        }
                        for pin in unknown
                    ],
                },
            )

        is_power = _is_power_net_name(net.name)
        net_classification = _classify_routing_net(
            net.name,
            net_refs,
            block_layout=block_layout,
        )
        occupied_label_points = _occupied_label_points(routing)
        occupied_route_points = _occupied_wire_points(routing.wires) | occupied_label_points
        foreign_attachment_points = _foreign_attachment_points_for_known(
            known,
            protected_pin_points=protected_pin_points,
            protected_stub_points=protected_stub_points,
            shared_protected_stub_points=shared_protected_stub_points,
        )
        dynamic_protected_points = foreign_attachment_points | occupied_route_points
        use_named_global_labels = net.name.startswith("/")
        strategy = "local_labels"
        heuristic_override: str | None = None

        # ----------------------------------------------------------------
        # Power nets → cluster-based power symbol placement (Phase 5.1)
        # ----------------------------------------------------------------
        if is_power:
            compact_power_override: str | None = None
            whole_power_cluster = heuristic_policy.route_compact_power_cluster(
                net_name=net.name,
                cluster=known,
                positions=positions,
            )
            occupied_route_points = _occupied_wire_points(routing.wires) | _occupied_label_points(
                routing
            )
            known_stub_ends = [_stub_end(wx, wy, wa) for _pin_ref, (wx, wy, wa) in known]
            if (
                whole_power_cluster is not None
                and _route_candidate_key(
                    whole_power_cluster[0],
                    protected_points=occupied_route_points,
                    endpoints=known_stub_ends,
                )[0]
                > 0
            ):
                whole_power_cluster = None
            if whole_power_cluster is not None:
                compact_power_override = (
                    "compact_local_ground_cluster"
                    if net.name.upper() == "GND"
                    else "compact_local_decoupling_cluster"
                )
                for pin_ref, (wx, wy, wa) in known:
                    ex, ey = _stub_end(wx, wy, wa)
                    routing.wires.append(WireSegment(wx, wy, ex, ey))
                    routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                cluster_segs, cluster_junctions, (px, py) = whole_power_cluster
                routing.wires.extend(cluster_segs)
                routing.junctions.extend(cluster_junctions)
                routing.power_symbols.append(
                    PowerSymbolPlacement(
                        net.name,
                        px,
                        py,
                        _power_symbol_angle(net.name, 0),
                    )
                )
                for pin_ref in unknown:
                    wx, wy = -1200.0, fallback_y
                    ex, ey = wx + WIRE_EXTEND_MM, wy
                    power_angle = _power_symbol_angle(net.name, 0)
                    px, py = _offset_point_along_angle(
                        ex,
                        ey,
                        power_angle,
                        _POWER_LABEL_CLEARANCE_MM,
                    )
                    routing.wires.append(WireSegment(wx, wy, ex, ey))
                    routing.wires.append(WireSegment(ex, ey, px, py))
                    routing.power_symbols.append(
                        PowerSymbolPlacement(net.name, px, py, power_angle)
                    )
                    routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                    fallback_y -= 10.0
                routing.route_decisions.append(
                    RouteDecision(
                        net_name=net.name,
                        classification=net_classification,
                        strategy="power_symbols",
                        pin_count=len(pins),
                        known_pin_count=len(known),
                        unknown_pin_count=len(unknown),
                        use_bus=use_bus,
                        heuristic_override=compact_power_override,
                    )
                )
                continue

            # Cluster known pins by proximity to share power symbols
            clusters = _cluster_power_pins(known, radius=_POWER_CLUSTER_RADIUS_MM)

            for cluster in clusters:
                if len(cluster) == 1:
                    # Single pin: traditional stub + symbol
                    pin_ref, (wx, wy, wa) = cluster[0]
                    _append_direct_power_symbol(
                        routing,
                        net_name=net.name,
                        pin_ref=pin_ref,
                        endpoint=(wx, wy, wa),
                        protected=_ProtectedPointContext(
                            foreign_attachment_points,
                            shared_protected_stub_points,
                        ),
                    )
                else:
                    # Multiple pins: compute cluster centroid for shared symbol
                    compact_ground_cluster = heuristic_policy.route_compact_power_cluster(
                        net_name=net.name,
                        cluster=cluster,
                        positions=positions,
                    )
                    cluster_stub_ends = [
                        _stub_end(wx, wy, wa) for _pin_ref, (wx, wy, wa) in cluster
                    ]
                    if (
                        compact_ground_cluster is not None
                        and _route_candidate_key(
                            compact_ground_cluster[0],
                            protected_points=occupied_route_points,
                            endpoints=cluster_stub_ends,
                        )[0]
                        > 0
                    ):
                        compact_ground_cluster = None
                    if compact_ground_cluster is not None:
                        compact_power_override = (
                            "compact_local_ground_cluster"
                            if net.name.upper() == "GND"
                            else "compact_local_decoupling_cluster"
                        )
                        for pin_ref, (wx, wy, wa) in cluster:
                            ex, ey = _stub_end(wx, wy, wa)
                            routing.wires.append(WireSegment(wx, wy, ex, ey))
                            routing.bind_markers.append(
                                BindMarker(pin_ref.ref, pin_ref.pin, net.name)
                            )
                        cluster_segs, cluster_junctions, (px, py) = compact_ground_cluster
                        routing.wires.extend(cluster_segs)
                        routing.junctions.extend(cluster_junctions)
                        routing.power_symbols.append(
                            PowerSymbolPlacement(
                                net.name,
                                px,
                                py,
                                _power_symbol_angle(net.name, 0),
                            )
                        )
                        continue

                    cx = _snap_grid(sum(cpx for _, (cpx, _, _) in cluster) / len(cluster))
                    cy = _snap_grid(sum(cpy for _, (_, cpy, _) in cluster) / len(cluster))

                    # Wire each pin to centroid via hub routing. If every stub end
                    # is already collinear, keep the outward stubs and place the
                    # shared lane on a nearby parallel track so no pin is attached
                    # from the symbol/body side through an overlapping collinear run.
                    (
                        stub_wires,
                        stub_ends,
                        aligned_axis,
                        aligned_coordinate,
                    ) = _aligned_power_cluster_route_points(cluster)
                    cluster_foreign_attachment_points = _foreign_attachment_points_for_known(
                        cluster,
                        protected_pin_points=protected_pin_points,
                        protected_stub_points=protected_stub_points,
                        shared_protected_stub_points=shared_protected_stub_points,
                    )
                    candidate_protected_points = (
                        cluster_foreign_attachment_points | occupied_route_points
                    )
                    if (
                        net.name.upper() == "GND"
                        and len(cluster) == 2
                        and use_bus
                        and aligned_axis == "vertical"
                    ):
                        # KiCad 9 still drops one pin from some aligned two-pin GND
                        # fallback clusters even with an offset shared lane, so keep
                        # vertically aligned cases as direct per-pin GND symbol
                        # attachments instead.
                        for pin_ref, (wx, wy, wa) in cluster:
                            _append_direct_power_symbol(
                                routing,
                                net_name=net.name,
                                pin_ref=pin_ref,
                                endpoint=(wx, wy, wa),
                                protected=_ProtectedPointContext(
                                    cluster_foreign_attachment_points,
                                    shared_protected_stub_points,
                                ),
                            )
                        continue
                    power_angle = _power_cluster_angle(stub_ends)
                    symbol_angle = _power_symbol_angle(net.name, power_angle)
                    px, py = _offset_point_along_angle(
                        cx,
                        cy,
                        power_angle,
                        _POWER_LABEL_CLEARANCE_MM,
                    )

                    # Add the snapped centroid as the hub target so the power-symbol
                    # branch wire and the spine-route junction land on the same point.
                    route_anchor = (cx, cy)
                    route_points = list(stub_ends)
                    if use_bus and aligned_axis is not None and aligned_coordinate is not None:
                        preferred_sign = 1 if (
                            (aligned_axis == "vertical" and power_angle == 180)
                            or (aligned_axis == "horizontal" and power_angle == 270)
                        ) else -1
                        preferred_lane_coordinate = _snap_grid(
                            aligned_coordinate + preferred_sign * WIRE_EXTEND_MM
                        )
                        candidate_offsets = (
                            preferred_sign,
                            preferred_sign * 2,
                            -preferred_sign,
                            -preferred_sign * 2,
                        )
                        candidate_coordinates: list[float] = [preferred_lane_coordinate]
                        seen_coordinates = {preferred_lane_coordinate}
                        for offset_sign in candidate_offsets:
                            candidate_coordinate = _snap_grid(
                                aligned_coordinate + offset_sign * WIRE_EXTEND_MM
                            )
                            if candidate_coordinate in seen_coordinates:
                                continue
                            seen_coordinates.add(candidate_coordinate)
                            candidate_coordinates.append(candidate_coordinate)

                        best_lane: tuple[
                            tuple[int, float, int],
                            float,
                            tuple[float, float],
                            list[WireSegment],
                            list[JunctionPoint],
                        ] | None = None
                        for candidate_coordinate in candidate_coordinates:
                            candidate_anchor = (
                                (candidate_coordinate, cy)
                                if aligned_axis == "vertical"
                                else (cx, candidate_coordinate)
                            )
                            candidate_points = [*stub_ends, candidate_anchor]
                            candidate_segs, candidate_junctions = _shared_lane_route(
                                candidate_points,
                                axis=aligned_axis,
                                coordinate=candidate_coordinate,
                            )
                            candidate_key = _route_candidate_key(
                                candidate_segs,
                                protected_points=candidate_protected_points,
                                endpoints=stub_ends,
                            )
                            lane_choice = (
                                candidate_key,
                                abs(candidate_coordinate - preferred_lane_coordinate),
                                candidate_anchor,
                                candidate_segs,
                                candidate_junctions,
                            )
                            if best_lane is None or lane_choice < best_lane:
                                best_lane = lane_choice

                        assert best_lane is not None
                        default_route_points = [*stub_ends, route_anchor]
                        if use_bus:
                            default_hub_segs, default_hub_junctions = _spine_route(
                                default_route_points
                            )
                        else:
                            default_hub_segs, default_hub_junctions = _hub_route(
                                default_route_points
                            )
                        default_key = _route_candidate_key(
                            default_hub_segs,
                            protected_points=candidate_protected_points,
                            endpoints=stub_ends,
                        )
                        (
                            best_aligned_key,
                            _distance_from_preferred,
                            best_anchor,
                            best_hub_segs,
                            best_hub_junctions,
                        ) = best_lane
                        if best_aligned_key < default_key:
                            route_anchor = best_anchor
                            hub_segs = best_hub_segs
                            hub_junctions = best_hub_junctions
                        else:
                            hub_segs = default_hub_segs
                            hub_junctions = default_hub_junctions
                    else:
                        route_points.append(route_anchor)
                        # Route stubs to centroid via spine/hub
                        if use_bus:
                            hub_segs, hub_junctions = _spine_route(route_points)
                        else:
                            hub_segs, hub_junctions = _hub_route(route_points)
                    selected_hub_key = _route_candidate_key(
                        hub_segs,
                        protected_points=candidate_protected_points,
                        endpoints=stub_ends,
                    )
                    if selected_hub_key[0] > 0:
                        for pin_ref, (wx, wy, wa) in cluster:
                            _append_direct_power_symbol(
                                routing,
                                net_name=net.name,
                                pin_ref=pin_ref,
                                endpoint=(wx, wy, wa),
                                protected=_ProtectedPointContext(
                                    cluster_foreign_attachment_points,
                                    shared_protected_stub_points,
                                ),
                            )
                        continue
                    routing.wires.extend(stub_wires)
                    for pin_ref, _anchor in cluster:
                        routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                    routing.power_symbols.append(
                        PowerSymbolPlacement(net.name, px, py, symbol_angle)
                    )
                    routing.wires.extend(hub_segs)
                    routing.junctions.extend(hub_junctions)
                    if (
                        aligned_axis == "horizontal"
                        and len(cluster) == 2
                        and power_angle in {90, 270}
                    ):
                        tail_y = _snap_grid(
                            route_anchor[1] - WIRE_EXTEND_MM
                            if power_angle == 90
                            else route_anchor[1] + WIRE_EXTEND_MM
                        )
                        routing.wires.append(WireSegment(px, py, route_anchor[0], tail_y))
                    else:
                        routing.wires.append(
                            WireSegment(route_anchor[0], route_anchor[1], px, py)
                        )

            # Off-canvas fallback for power pins with no known endpoint
            for pin_ref in unknown:
                wx, wy = -1200.0, fallback_y
                ex, ey = wx + WIRE_EXTEND_MM, wy
                power_angle = _power_symbol_angle(net.name, 0)
                px, py = _offset_point_along_angle(
                    ex,
                    ey,
                    power_angle,
                    _POWER_LABEL_CLEARANCE_MM,
                )
                routing.wires.append(WireSegment(wx, wy, ex, ey))
                routing.wires.append(WireSegment(ex, ey, px, py))
                routing.power_symbols.append(PowerSymbolPlacement(net.name, px, py, power_angle))
                routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                fallback_y -= 10.0
            routing.route_decisions.append(
                RouteDecision(
                    net_name=net.name,
                    classification=net_classification,
                    strategy="power_symbols",
                    pin_count=len(pins),
                    known_pin_count=len(known),
                    unknown_pin_count=len(unknown),
                    use_bus=use_bus,
                    heuristic_override=compact_power_override,
                )
            )
            continue

        # ----------------------------------------------------------------
        # 2-pin direct route
        # ----------------------------------------------------------------
        routed_directly = False
        if len(known) == 2 and not unknown:
            p0, (wx0, wy0, wa0) = known[0]
            p1, (wx1, wy1, wa1) = known[1]
            ex0, ey0 = _stub_end(wx0, wy0, wa0)
            ex1, ey1 = _stub_end(wx1, wy1, wa1)
            if (
                use_bus
                and net.name.startswith("/")
                and _known_pin_stub_hits_foreign_attachment(
                    known,
                    protected_pin_points=protected_pin_points,
                    protected_stub_points=protected_stub_points,
                    shared_protected_stub_points=shared_protected_stub_points,
                )
            ):
                _append_pin_endpoint_labels(
                    routing,
                    net_name=net.name,
                    known=_prioritize_label_candidates(
                        known,
                        block_layout=block_layout,
                        classification=net_classification,
                    ),
                    protected=_ProtectedPointContext(
                        foreign_attachment_points,
                        shared_protected_stub_points,
                    ),
                )
                routed_directly = True
                strategy = "global_labels" if net.name.startswith("/") else "local_labels"
                heuristic_override = "foreign_attachment_label_breakout"
            elif (
                use_bus
                and net_classification == "connector_attachment"
                and net.name.startswith("/")
                and abs(ex1 - ex0) > 40.0
            ):
                _append_pin_endpoint_labels(
                    routing,
                    net_name=net.name,
                    known=_prioritize_label_candidates(
                        known,
                        block_layout=block_layout,
                        classification=net_classification,
                    ),
                    prefer_stub_anchor=True,
                )
                routed_directly = True
                strategy = "global_labels"
                heuristic_override = "connector_label_breakout"
            if routed_directly:
                routing.route_decisions.append(
                    RouteDecision(
                        net_name=net.name,
                        classification=net_classification,
                        strategy=strategy,
                        pin_count=len(pins),
                        known_pin_count=len(known),
                        unknown_pin_count=len(unknown),
                        use_bus=use_bus,
                        heuristic_override=heuristic_override,
                    )
                )
                continue
            manhattan = _manhattan(ex0, ey0, ex1, ey1)
            if tiers is not None:
                # Rule §4: tier distance ≤ 1 guards signal-flow adjacency;
                # manhattan cap (MAX_DIRECT_DIST_MM) guards physical wire length,
                # matching the behaviour of the non-tier path.
                tdist = _tier_distance(p0.ref, p1.ref, tiers)
                short_local_override = (
                    _classification_prefers_short_local_direct_route(net_classification)
                    and manhattan <= MAX_DIRECT_WIRE_MM
                )
                can_direct = manhattan <= MAX_DIRECT_DIST_MM and (
                    tdist <= 1 or _is_connector_passive_edge(p0.ref, p1.ref) or short_local_override
                )
            else:
                can_direct = manhattan <= MAX_DIRECT_DIST_MM
            if can_direct:
                direct_route = _best_direct_route_with_protected_points(
                    ex0,
                    ey0,
                    ex1,
                    ey1,
                    protected_points=dynamic_protected_points,
                )
                routing.wires.append(WireSegment(wx0, wy0, ex0, ey0))
                routing.wires.append(WireSegment(wx1, wy1, ex1, ey1))
                routing.wires.extend(direct_route)
                routing.bind_markers.append(BindMarker(p0.ref, p0.pin, net.name))
                routing.bind_markers.append(BindMarker(p1.ref, p1.pin, net.name))
                if policy.force_all_signal_labels and direct_route:
                    anchor_segment = direct_route[0]
                    label_x = anchor_segment.x2
                    label_y = anchor_segment.y2
                    label_angle = _segment_label_angle(anchor_segment)
                    if net.name.startswith("/"):
                        routing.global_labels.append(
                            GlobalLabelPlacement(net.name, label_x, label_y, label_angle)
                        )
                    else:
                        routing.labels.append(
                            NetLabel(
                                net.name,
                                label_x,
                                label_y,
                                label_angle,
                            )
                        )
                else:
                    _append_promoted_visible_label(
                        routing,
                        promotion=_VisibleLabelPromotion(
                            net_name=net.name,
                            refs=net_refs,
                            block_layout=block_layout,
                            classification=net_classification,
                            label_candidates=known,
                        ),
                        policy=policy,
                        protected_points=foreign_attachment_points,
                        shared_protected_points=shared_protected_stub_points,
                    )
                routed_directly = True
                strategy = "direct"

        if routed_directly:
            routing.route_decisions.append(
                RouteDecision(
                    net_name=net.name,
                    classification=net_classification,
                    strategy=strategy,
                    pin_count=len(pins),
                    known_pin_count=len(known),
                    unknown_pin_count=len(unknown),
                    use_bus=use_bus,
                    heuristic_override=heuristic_override,
                )
            )
            continue

        # ----------------------------------------------------------------
        # Hub route (3 – _HUB_MAX_DEGREE known, no unknown pins)
        # ----------------------------------------------------------------
        if 3 <= len(known) <= _HUB_MAX_DEGREE and not unknown:
            wire_start = len(routing.wires)
            bind_start = len(routing.bind_markers)
            stub_ends = [_stub_end(wx, wy, wa) for _pin_ref, (wx, wy, wa) in known]
            xs = [point[0] for point in stub_ends]
            compact_tail_plan = (
                ladder_routes[net.name]
                if use_bus and net.name in ladder_routes
                else _infer_bounded_local_lane_plan(stub_ends)
            )
            compact_tail_candidate = (
                use_bus
                and _classification_prefers_compact_tail(net_classification)
                and heuristic_policy.route_compact_signal_tail(
                    stub_ends,
                    inferred_plan=compact_tail_plan,
                    protected_points=dynamic_protected_points,
                    positions=positions,
                )
                is not None
            )
            if (
                use_bus
                and not compact_tail_candidate
                and net_classification not in {"connector_attachment", "signal_chain"}
                and _known_pin_stub_hits_foreign_attachment(
                    known,
                    protected_pin_points=protected_pin_points,
                    protected_stub_points=protected_stub_points,
                    shared_protected_stub_points=shared_protected_stub_points,
                )
            ):
                _append_pin_endpoint_labels(
                    routing,
                    net_name=net.name,
                    known=_prioritize_label_candidates(
                        known,
                        block_layout=block_layout,
                        classification=net_classification,
                    ),
                    prefer_stub_anchor=True,
                )
                routing.route_decisions.append(
                    RouteDecision(
                        net_name=net.name,
                        classification=net_classification,
                        strategy=(
                            "global_labels"
                            if net.name.startswith("/")
                            else "local_labels"
                        ),
                        pin_count=len(pins),
                        known_pin_count=len(known),
                        unknown_pin_count=len(unknown),
                        use_bus=use_bus,
                        heuristic_override="foreign_attachment_label_breakout",
                    )
                )
                continue
            if (
                use_bus
                and len(known) == 3
                and net_classification == "connector_attachment"
                and (max(xs) - min(xs)) > 80.0
            ):
                _append_pin_endpoint_labels(
                    routing,
                    net_name=net.name,
                    known=_prioritize_label_candidates(
                        known,
                        block_layout=block_layout,
                        classification=net_classification,
                    ),
                    prefer_stub_anchor=True,
                )
                routing.route_decisions.append(
                    RouteDecision(
                        net_name=net.name,
                        classification=net_classification,
                        strategy="global_labels",
                        pin_count=len(pins),
                        known_pin_count=len(known),
                        unknown_pin_count=len(unknown),
                        use_bus=use_bus,
                        heuristic_override="connector_label_breakout",
                    )
                )
                continue
            if use_bus and net.name in ladder_routes:
                lane_plan = ladder_routes[net.name]
                stub_ends = []
                for pin_ref, (wx, wy, wa) in known:
                    ex, ey = _stub_end(wx, wy, wa)
                    routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                    # For a horizontal shared lane, suppress the sideways stub from
                    # pins that exit horizontally (angle ≈ 0° or 180°).  Routing
                    # from the pin endpoint directly avoids the "right/left-then-up"
                    # L-shaped detour; _shared_lane_route reproduces any needed
                    # horizontal span internally, leaving connectivity unchanged.
                    if lane_plan.axis == "horizontal" and math.isclose(
                        math.sin(math.radians(wa)), 0.0, abs_tol=0.01
                    ):
                        stub_ends.append((wx, wy))
                    else:
                        routing.wires.append(WireSegment(wx, wy, ex, ey))
                        stub_ends.append((ex, ey))
                compact_tail_route = None
                small_analog_candidate = False
                prefer_small_analog_chain = False
                if _classification_prefers_compact_tail(net_classification):
                    compact_tail_route = heuristic_policy.route_compact_signal_tail(
                        stub_ends,
                        inferred_plan=lane_plan,
                        protected_points=dynamic_protected_points,
                        positions=positions,
                    )
                small_analog_candidate = (
                    use_bus
                    and _classification_prefers_local_chain(net_classification)
                    and heuristic_policy.enable_small_analog_local_routing
                    and _is_small_analog_chain_candidate(
                        stub_ends,
                        refs=tuple(pin_ref.ref for pin_ref, _anchor in known),
                    )
                )
                prefer_small_analog_chain = (
                    small_analog_candidate
                    and heuristic_policy.should_prefer_small_analog_chain(
                        stub_ends,
                        inferred_plan=lane_plan,
                        refs=tuple(pin_ref.ref for pin_ref, _anchor in known),
                    )
                )
                if compact_tail_route is not None:
                    hub_segs, hub_junctions = compact_tail_route
                    strategy = "compact_signal_tail"
                    heuristic_override = "compact_output_tail"
                elif (
                    use_bus
                    and _classification_prefers_local_chain(net_classification)
                    and heuristic_policy.enable_small_analog_local_routing
                    and (
                        follower_feedback_route := _buffer_follower_feedback_route(
                            known,
                            stub_ends,
                            block_layout=block_layout,
                        )
                    )
                    is not None
                ):
                    hub_segs, hub_junctions = follower_feedback_route
                    strategy = "chain"
                    heuristic_override = "small_analog_local_routing"
                elif prefer_small_analog_chain:
                    hub_segs, hub_junctions = _compact_aligned_chain_route(
                        stub_ends,
                        protected_points=dynamic_protected_points,
                    )
                    strategy = "chain"
                    heuristic_override = "small_analog_local_routing"
                else:
                    hub_segs, hub_junctions = _shared_lane_route(
                        stub_ends,
                        axis=lane_plan.axis,
                        coordinate=lane_plan.coordinate,
                        min_bound=lane_plan.min_orthogonal,
                        max_bound=lane_plan.max_orthogonal,
                    )
                    strategy = "shared_lane"
                    if small_analog_candidate:
                        heuristic_override = "small_analog_local_routing"
            else:
                stub_ends = []
                for pin_ref, (wx, wy, wa) in known:
                    ex, ey = _stub_end(wx, wy, wa)
                    routing.wires.append(WireSegment(wx, wy, ex, ey))
                    routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                    stub_ends.append((ex, ey))
                compact_tail_plan = _infer_bounded_local_lane_plan(stub_ends)
                compact_tail_route = None
                small_analog_candidate = False
                prefer_small_analog_chain = False
                if use_bus and _classification_prefers_compact_tail(net_classification):
                    compact_tail_route = heuristic_policy.route_compact_signal_tail(
                        stub_ends,
                        inferred_plan=compact_tail_plan,
                        protected_points=dynamic_protected_points,
                        positions=positions,
                    )
                small_analog_candidate = (
                    use_bus
                    and _classification_prefers_local_chain(net_classification)
                    and heuristic_policy.enable_small_analog_local_routing
                    and _is_small_analog_chain_candidate(
                        stub_ends,
                        refs=tuple(pin_ref.ref for pin_ref, _anchor in known),
                    )
                )
                prefer_small_analog_chain = (
                    small_analog_candidate
                    and heuristic_policy.should_prefer_small_analog_chain(
                        stub_ends,
                        inferred_plan=compact_tail_plan,
                        refs=tuple(pin_ref.ref for pin_ref, _anchor in known),
                    )
                )
                if compact_tail_route is not None:
                    hub_segs, hub_junctions = compact_tail_route
                    strategy = "compact_signal_tail"
                    heuristic_override = "compact_output_tail"
                elif (
                    use_bus
                    and _classification_prefers_local_chain(net_classification)
                    and heuristic_policy.enable_small_analog_local_routing
                    and (
                        follower_feedback_route := _buffer_follower_feedback_route(
                            known,
                            stub_ends,
                            block_layout=block_layout,
                        )
                    )
                    is not None
                ):
                    hub_segs, hub_junctions = follower_feedback_route
                    strategy = "chain"
                    heuristic_override = "small_analog_local_routing"
                elif prefer_small_analog_chain:
                    hub_segs, hub_junctions = _compact_aligned_chain_route(
                        stub_ends,
                        protected_points=dynamic_protected_points,
                    )
                    strategy = "chain"
                    heuristic_override = "small_analog_local_routing"
                elif (
                    use_bus
                    and _classification_prefers_local_chain(net_classification)
                    and _prefer_chain_route(stub_ends)
                ):
                    hub_segs, hub_junctions = _compact_aligned_chain_route(
                        stub_ends,
                        protected_points=dynamic_protected_points,
                    )
                    strategy = "chain"
                elif use_bus:
                    hub_segs, hub_junctions = _spine_route(stub_ends)
                    strategy = "spine"
                    if small_analog_candidate:
                        heuristic_override = "small_analog_local_routing"
                else:
                    hub_segs, hub_junctions = _hub_route(stub_ends)
                    strategy = "hub"
            if use_bus and net.name.startswith("/") and len(stub_ends) == 3:
                best_key = _route_candidate_key(
                    hub_segs,
                    protected_points=dynamic_protected_points,
                    endpoints=stub_ends,
                )
                if best_key[0] > 0:
                    protected_shared_lane = _protected_shared_lane_route(
                        stub_ends,
                        protected_points=dynamic_protected_points,
                    )
                    if protected_shared_lane is not None:
                        protected_lane_segs, protected_lane_junctions = protected_shared_lane
                        protected_lane_key = _route_candidate_key(
                            protected_lane_segs,
                            protected_points=dynamic_protected_points,
                            endpoints=stub_ends,
                        )
                        if protected_lane_key < best_key:
                            hub_segs = protected_lane_segs
                            hub_junctions = protected_lane_junctions
                            strategy = "shared_lane"
                            best_key = protected_lane_key
                            if strategy != "shared_lane":
                                heuristic_override = "protected_stub_avoidance"
                    protected_chain_segs, protected_chain_junctions = _compact_aligned_chain_route(
                        stub_ends,
                        protected_points=dynamic_protected_points,
                    )
                    protected_chain_key = _route_candidate_key(
                        protected_chain_segs,
                        protected_points=dynamic_protected_points,
                        endpoints=stub_ends,
                    )
                    if protected_chain_key < best_key:
                        hub_segs = protected_chain_segs
                        hub_junctions = protected_chain_junctions
                        strategy = "chain"
                        heuristic_override = "protected_stub_avoidance"
            elif use_bus and len(stub_ends) >= 4:
                current_key = _route_candidate_key(
                    hub_segs,
                    protected_points=dynamic_protected_points,
                    endpoints=stub_ends,
                )
                if current_key[0] > 0:
                    protected_shared_lane = _protected_shared_lane_route(
                        stub_ends,
                        protected_points=dynamic_protected_points,
                    )
                    if protected_shared_lane is not None:
                        protected_lane_segs, protected_lane_junctions = protected_shared_lane
                        protected_lane_key = _route_candidate_key(
                            protected_lane_segs,
                            protected_points=dynamic_protected_points,
                            endpoints=stub_ends,
                        )
                        if protected_lane_key < current_key:
                            hub_segs = protected_lane_segs
                            hub_junctions = protected_lane_junctions
                            strategy = "shared_lane"
                            heuristic_override = "protected_stub_avoidance"
                            current_key = protected_lane_key
                if current_key[0] > 0:
                    protected_chain_segs, protected_chain_junctions = _compact_aligned_chain_route(
                        stub_ends,
                        protected_points=dynamic_protected_points,
                    )
                    protected_chain_key = _route_candidate_key(
                        protected_chain_segs,
                        protected_points=dynamic_protected_points,
                        endpoints=stub_ends,
                    )
                    if protected_chain_key < current_key:
                        hub_segs = protected_chain_segs
                        hub_junctions = protected_chain_junctions
                        strategy = "chain"
                        heuristic_override = "protected_stub_avoidance"
                        current_key = protected_chain_key
                if (
                    heuristic_override == "protected_stub_avoidance"
                    and not net.name.startswith("/")
                    and (
                        max(point[0] for point in stub_ends)
                        - min(point[0] for point in stub_ends)
                    )
                    > 80.0
                ):
                    del routing.wires[wire_start:]
                    del routing.bind_markers[bind_start:]
                    _append_pin_endpoint_labels(
                        routing,
                        net_name=net.name,
                        known=_prioritize_label_candidates(
                            known,
                            block_layout=block_layout,
                            classification=net_classification,
                        ),
                        protected=_ProtectedPointContext(
                            foreign_attachment_points,
                            shared_protected_stub_points,
                        ),
                    )
                    routing.route_decisions.append(
                        RouteDecision(
                            net_name=net.name,
                            classification=net_classification,
                            strategy="local_labels",
                            pin_count=len(pins),
                            known_pin_count=len(known),
                            unknown_pin_count=len(unknown),
                            use_bus=use_bus,
                            heuristic_override="foreign_attachment_label_breakout",
                        )
                    )
                    continue
                if current_key[0] > 0:
                    del routing.wires[wire_start:]
                    del routing.bind_markers[bind_start:]
                    _append_pin_endpoint_labels(
                        routing,
                        net_name=net.name,
                        known=_prioritize_label_candidates(
                            known,
                            block_layout=block_layout,
                            classification=net_classification,
                        ),
                        protected=_ProtectedPointContext(
                            foreign_attachment_points,
                            shared_protected_stub_points,
                        ),
                    )
                    routing.route_decisions.append(
                        RouteDecision(
                            net_name=net.name,
                            classification=net_classification,
                            strategy=(
                                "global_labels"
                                if net.name.startswith("/")
                                else "local_labels"
                            ),
                            pin_count=len(pins),
                            known_pin_count=len(known),
                            unknown_pin_count=len(unknown),
                            use_bus=use_bus,
                            heuristic_override="foreign_attachment_label_breakout",
                        )
                    )
                    continue
            routing.wires.extend(hub_segs)
            routing.junctions.extend(hub_junctions)
            _append_promoted_visible_label(
                routing,
                promotion=_VisibleLabelPromotion(
                    net_name=net.name,
                    refs=net_refs,
                    block_layout=block_layout,
                    classification=net_classification,
                    label_candidates=known,
                ),
                policy=policy,
                protected_points=foreign_attachment_points,
                shared_protected_points=shared_protected_stub_points,
            )
            routing.route_decisions.append(
                RouteDecision(
                    net_name=net.name,
                    classification=net_classification,
                    strategy=strategy,
                    pin_count=len(pins),
                    known_pin_count=len(known),
                    unknown_pin_count=len(unknown),
                    use_bus=use_bus,
                    heuristic_override=heuristic_override,
                )
            )
            continue

        # ----------------------------------------------------------------
        # High-degree non-power → global label per pin (capped by policy)
        # ----------------------------------------------------------------
        label_candidates = _prioritize_label_candidates(
            known,
            block_layout=block_layout,
            classification=net_classification,
        )

        if len(known) > _HUB_MAX_DEGREE:
            strategy = "global_labels"
            global_label_count = 0
            for pin_ref, (wx, wy, wa) in label_candidates:
                label_route, ex, ey = _label_attachment_plan(
                    pin_point=(wx, wy),
                    pin_angle=wa,
                    occupied_points=occupied_route_points,
                    protected=_ProtectedPointContext(
                        foreign_attachment_points,
                        shared_protected_stub_points,
                    ),
                )
                label_angle = int((wa + 180) % 360)
                routing.wires.extend(label_route)
                routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                if global_label_count < policy.max_global_labels_per_net:
                    routing.global_labels.append(
                        GlobalLabelPlacement(net.name, ex, ey, label_angle)
                    )
                    global_label_count += 1
                occupied_route_points |= _occupied_wire_points(label_route)
                occupied_route_points.add((round(ex, 2), round(ey, 2)))
            for pin_ref in unknown:
                wx, wy = -1200.0, fallback_y
                ex, ey = wx + WIRE_EXTEND_MM, wy
                routing.wires.append(WireSegment(wx, wy, ex, ey))
                routing.global_labels.append(GlobalLabelPlacement(net.name, ex, ey, 0))
                routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                fallback_y -= 10.0
            routing.route_decisions.append(
                RouteDecision(
                    net_name=net.name,
                    classification=net_classification,
                    strategy=strategy,
                    pin_count=len(pins),
                    known_pin_count=len(known),
                    unknown_pin_count=len(unknown),
                    use_bus=use_bus,
                )
            )
            continue

        # ----------------------------------------------------------------
        # Label route (classic fallback: stub + local net label per pin)
        # Labels are capped at policy.max_labels_per_net to reduce clutter.
        # Stub wires and bind markers are always emitted (every pin).
        # ----------------------------------------------------------------
        if use_named_global_labels:
            strategy = "global_labels"
        label_count = 0
        for pin_ref, (wx, wy, wa) in label_candidates:
            label_route, ex, ey = _label_attachment_plan(
                pin_point=(wx, wy),
                pin_angle=wa,
                occupied_points=occupied_route_points,
                protected=_ProtectedPointContext(
                    foreign_attachment_points,
                    shared_protected_stub_points,
                ),
            )
            label_angle = int((wa + 180) % 360)
            routing.wires.extend(label_route)
            routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
            if use_named_global_labels:
                routing.global_labels.append(GlobalLabelPlacement(net.name, ex, ey, label_angle))
            elif label_count < policy.max_labels_per_net:
                routing.labels.append(NetLabel(net.name, ex, ey, label_angle))
                label_count += 1
            occupied_route_points |= _occupied_wire_points(label_route)
            occupied_route_points.add((round(ex, 2), round(ey, 2)))

        for pin_ref in unknown:
            wx, wy = -1200.0, fallback_y
            ex, ey = wx + WIRE_EXTEND_MM, wy
            routing.wires.append(WireSegment(wx, wy, ex, ey))
            routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
            # Off-canvas unknown pins always get a label regardless of policy
            # (they have no physical wire connection; the label IS their connection).
            if use_named_global_labels:
                routing.global_labels.append(GlobalLabelPlacement(net.name, ex, ey, 0))
            else:
                routing.labels.append(NetLabel(net.name, ex, ey, 0))
            fallback_y -= 10.0

        routing.route_decisions.append(
            RouteDecision(
                net_name=net.name,
                classification=net_classification,
                strategy=strategy,
                pin_count=len(pins),
                known_pin_count=len(known),
                unknown_pin_count=len(unknown),
                use_bus=use_bus,
            )
        )

    # ----------------------------------------------------------------
    # Body-crossing guard (Rule §4.3)
    # ----------------------------------------------------------------
    if positions is not None:
        routing.wires = detect_body_crossings(routing.wires, positions)

    # ----------------------------------------------------------------
    # Wire simplification pass (Phase 6.1)
    # ----------------------------------------------------------------
    # Protect pin endpoints and visible/off-canvas label attachment points from
    # being merged away; they are required electrical boundaries.  Round to
    # 2 decimal places (0.01 mm precision) to avoid floating-point comparison
    # issues.
    protected = {(round(x, 2), round(y, 2)) for x, y, _angle in pin_endpoints.values()}
    protected.update(
        (round(stub_x, 2), round(stub_y, 2))
        for x, y, angle in pin_endpoints.values()
        for stub_x, stub_y in [_stub_end(x, y, angle)]
    )
    protected.update((round(label.x, 2), round(label.y, 2)) for label in routing.labels)
    protected.update((round(label.x, 2), round(label.y, 2)) for label in routing.global_labels)
    protected.update((round(symbol.x, 2), round(symbol.y, 2)) for symbol in routing.power_symbols)
    protected.update((round(junction.x, 2), round(junction.y, 2)) for junction in routing.junctions)
    routing.wires = _simplify_wires(routing.wires, protected_points=protected)

    oriented_wires: list[WireSegment] = []
    for seg in routing.wires:
        start = (round(seg.x1, 2), round(seg.y1, 2))
        end = (round(seg.x2, 2), round(seg.y2, 2))
        if end in protected and start not in protected:
            oriented_wires.append(WireSegment(seg.x2, seg.y2, seg.x1, seg.y1))
        else:
            oriented_wires.append(seg)
    routing.wires = oriented_wires

    return routing


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
    """Emit *routing* decisions into the schematic document *doc*.

    Writes wire segments (``(wire …)``), local net labels (``(label …)``),
    global labels (``(global_label …)``), power symbols (``(symbol …)``
    with ``lib_id`` from the ``power:`` library), junction markers
    (``(junction …)``), and bind markers (hidden ``(text …)`` nodes).
    Updates *stats* counters:
    ``wires``, ``labels``, ``global_labels``, ``power_symbols``,
    ``junctions``, ``binding_markers``.

    Parameters
    ----------
    symbols_dir:
        Directory containing ``.kicad_sym`` files.  ``None`` uses the system
        default (``/usr/share/kicad/symbols``).  Used only when embedding
        power-library symbols.
    project_name:
        KiCad project name embedded in power-symbol ``(instances …)``
        annotations.  Defaults to ``"project"``.
    strict:
        When ``True``, missing power-library symbols are treated as errors
        instead of falling back to ``global_label`` insertion.
    """
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
        (round(label.x, 2), round(label.y, 2))
        for label in routing.labels
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
        binding_text = "OpenClaw:bind=" + json.dumps(
            {"ref": bm.ref, "pin": bm.pin, "net_name": bm.net_name},
            separators=(",", ":"),
            sort_keys=True,
        )
        doc.add_text(binding_text, -1200.0, -1500.0 - 10.0 * idx, hidden=True)
        stats["binding_markers"] += 1

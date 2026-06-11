"""Type definitions, constants, and policy dataclasses for the router.

This module is imported by all other ``_router_*`` sub-modules.  It must not
import from them (no circular dependencies).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .circuit_ir import PinRefIR

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
RoutingClassification = Literal[
    "power",
    "local_decoupling",
    "shunt_ground",
    "connector_only",
    "connector_attachment",
    "signal_chain",
    "feedback",
    "generic_signal",
]

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


# ---------------------------------------------------------------------------
# Label policies
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# RoutingHeuristicPolicy — strategy helpers are imported lazily inside each
# method body to avoid circular imports (strategies import types).
# ---------------------------------------------------------------------------
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
        from ._router_strategies import _should_skip_inferred_lane_plan  # noqa: PLC0415

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
        from ._router_strategies import _prefer_small_analog_chain_route  # noqa: PLC0415

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
        from ._router_classify import _is_compact_horizontal_stage_tail  # noqa: PLC0415
        from ._router_strategies import (  # noqa: PLC0415
            _best_compact_vertical_tail_route,
            _compact_horizontal_stage_tail_route,
        )

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
        from ._router_strategies import (  # noqa: PLC0415
            _compact_local_decoupling_ground_cluster_route,
            _compact_local_decoupling_power_cluster_route,
            _compact_local_ground_cluster_route,
        )
        from .component_types import power_rail_polarity  # noqa: PLC0415

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


# ---------------------------------------------------------------------------
# LadderLanePlannerContext — defined after RoutingHeuristicPolicy so the
# default_factory lambda can reference DEFAULT_ROUTING_HEURISTIC_POLICY.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LadderLanePlannerContext:
    """Grouped-ladder planning inputs shared across one local neighborhood."""

    candidate_degree: dict[str, int]
    shared_lane_by_net: dict[str, tuple[str, float]]
    endpoints_by_net: dict[str, list[tuple[float, float]]]
    refs_by_net: dict[str, tuple[str, ...]]
    connector_entry_x_by_net: dict[str, float]
    heuristic_policy: RoutingHeuristicPolicy = field(
        default_factory=lambda: DEFAULT_ROUTING_HEURISTIC_POLICY
    )

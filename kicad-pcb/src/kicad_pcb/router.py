"""Orthogonal net routing for kicad_pcb managed schematics.

Routes 2-pin nets directly with L-shaped wire segments when both pins
are known and within :data:`MAX_DIRECT_DIST_MM` (Manhattan distance,
stub-end to stub-end).  All other nets fall back to the classic
wire-stub + net-label-per-pin approach.

Public API
----------
:func:`route_nets`     — compute routing decisions for all nets in a CircuitIR.
:func:`write_routing`  — emit computed decisions into a SchematicDoc.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .circuit_ir import CircuitIR
    from .sch_doc import SchematicDoc

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
WIRE_EXTEND_MM: float = 5.08  # pin stub length — 200 mil (one KiCad grid unit)
MAX_DIRECT_DIST_MM: float = 120.0  # Manhattan distance cap for direct routing


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


@dataclass
class NetRouting:
    """Aggregated routing decisions for a whole schematic."""

    wires: list[WireSegment] = field(default_factory=list)
    labels: list[NetLabel] = field(default_factory=list)
    bind_markers: list[BindMarker] = field(default_factory=list)


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


def _l_route(ex1: float, ey1: float, ex2: float, ey2: float) -> list[WireSegment]:
    """Return up to two orthogonal segments that connect (ex1,ey1) to (ex2,ey2).

    Uses horizontal-first L-routing: go horizontally to (ex2, ey1), then
    vertically to (ex2, ey2).  Degenerate segments (zero length) are omitted.
    """
    segs: list[WireSegment] = []
    if not math.isclose(ex1, ex2, abs_tol=0.01):
        # Horizontal leg
        segs.append(WireSegment(ex1, ey1, ex2, ey1))
    if not math.isclose(ey1, ey2, abs_tol=0.01):
        # Vertical leg (starts at corner (ex2, ey1))
        segs.append(WireSegment(ex2, ey1, ex2, ey2))
    return segs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def route_nets(
    *,
    ir: CircuitIR,
    pin_endpoints: dict[tuple[str, str], tuple[float, float, float]],
) -> NetRouting:
    """Compute routing decisions for all nets in *ir*.

    For each net:

    * **Direct route** — if the net has exactly 2 pins, both are in
      *pin_endpoints*, and the Manhattan distance between their stub-ends is
      ≤ :data:`MAX_DIRECT_DIST_MM`: draw stub + stub + L-shaped connecting
      wire segments.  No net labels are emitted for this net.
    * **Label route** — all other nets: draw one stub-wire + one net label
      per pin (the classic approach).

    Unknown pins (absent from *pin_endpoints*, e.g. when no library symbol
    was found) fall back to a floating off-canvas position and always use
    the label route.  This should not occur after ``validate_ir_symbols``
    has passed.

    Returns a :class:`NetRouting` containing all decisions.
    """
    routing = NetRouting()
    fallback_y = -1500.0

    for net in sorted(ir.nets, key=lambda n: n.name):
        pins = sorted(net.pins, key=lambda p: (p.ref, p.pin))
        known = [
            (p, pin_endpoints[(p.ref, p.pin)]) for p in pins if (p.ref, p.pin) in pin_endpoints
        ]
        unknown = [p for p in pins if (p.ref, p.pin) not in pin_endpoints]

        # ----------------------------------------------------------------
        # Decide routing strategy
        # ----------------------------------------------------------------
        routed_directly = False
        if len(known) == 2 and not unknown:
            p0, (wx0, wy0, wa0) = known[0]
            p1, (wx1, wy1, wa1) = known[1]
            ex0, ey0 = _stub_end(wx0, wy0, wa0)
            ex1, ey1 = _stub_end(wx1, wy1, wa1)
            if _manhattan(ex0, ey0, ex1, ey1) <= MAX_DIRECT_DIST_MM:
                # Emit: pin-0 stub, pin-1 stub, L-route between stub ends,
                # two bind markers (no labels for this net).
                routing.wires.append(WireSegment(wx0, wy0, ex0, ey0))
                routing.wires.append(WireSegment(wx1, wy1, ex1, ey1))
                routing.wires.extend(_l_route(ex0, ey0, ex1, ey1))
                routing.bind_markers.append(BindMarker(p0.ref, p0.pin, net.name))
                routing.bind_markers.append(BindMarker(p1.ref, p1.pin, net.name))
                routed_directly = True

        if not routed_directly:
            # Classic stub + label for every known pin.
            for pin_ref, (wx, wy, wa) in known:
                ex, ey = _stub_end(wx, wy, wa)
                label_angle = int((wa + 180) % 360)
                routing.wires.append(WireSegment(wx, wy, ex, ey))
                routing.labels.append(NetLabel(net.name, ex, ey, label_angle))
                routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))

            # Off-canvas fallback for pins with no known endpoint.
            for pin_ref in unknown:
                wx, wy = -1200.0, fallback_y
                ex, ey = wx + WIRE_EXTEND_MM, wy
                routing.wires.append(WireSegment(wx, wy, ex, ey))
                routing.labels.append(NetLabel(net.name, ex, ey, 0))
                routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                fallback_y -= 10.0

    return routing


def write_routing(
    *,
    doc: SchematicDoc,
    routing: NetRouting,
    new_uuid: Callable[[], str],
    stats: dict[str, int],
) -> None:
    """Emit *routing* decisions into the schematic document *doc*.

    Writes all wire segments (as ``(wire …)`` nodes), net labels
    (as ``(label …)`` nodes), and bind markers (as hidden off-canvas
    ``(text …)`` nodes).  Updates *stats* counters:
    ``wires``, ``labels``, ``binding_markers``.
    """
    for seg in routing.wires:
        doc.add_wire(seg.x1, seg.y1, seg.x2, seg.y2, new_uuid())
        stats["wires"] += 1

    for lbl in routing.labels:
        doc.add_label(lbl.name, lbl.x, lbl.y, new_uuid(), angle=lbl.angle)
        stats["labels"] += 1

    for idx, bm in enumerate(routing.bind_markers):
        binding_text = "OpenClaw:bind=" + json.dumps(
            {"ref": bm.ref, "pin": bm.pin, "net_name": bm.net_name},
            separators=(",", ":"),
            sort_keys=True,
        )
        doc.add_text(binding_text, -1200.0, -1500.0 - 10.0 * idx, hidden=True)
        stats["binding_markers"] += 1

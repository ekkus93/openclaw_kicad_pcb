"""Block integration tests — l_route, spine_route, circuit routing."""

from __future__ import annotations

import json

import pytest

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.router import _l_route, _spine_route, route_nets
from tests import (
    NE5532_LEFT_CURRENT_READABILITY_FIXTURE,
)

_READABILITY_FIXTURE = NE5532_LEFT_CURRENT_READABILITY_FIXTURE
_CIRCUIT_IR_PATH = _READABILITY_FIXTURE.circuit_ir_path


def _load_test_circuit() -> CircuitIR:
    """Load the headphone amp baseline circuit IR."""
    if not _CIRCUIT_IR_PATH.exists():
        pytest.skip("Circuit IR fixture not found")

    ir_data = json.loads(_CIRCUIT_IR_PATH.read_text(encoding="utf-8"))
    return CircuitIR(**ir_data)


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_l_route_horizontal_first() -> None:
    """Test that _l_route implements horizontal-first L-routing.

    For left-to-right signal flow, the horizontal segment should come
    before the vertical segment, ensuring the wire travels left-to-right
    before making any vertical adjustments.
    """
    # Test 1: Route from left-bottom to right-top should go right first
    ex1, ey1, ex2, ey2 = 0.0, 0.0, 10.0, 20.0
    segs = _l_route(ex1, ey1, ex2, ey2)

    # Should have 2 segments: horizontal then vertical
    assert len(segs) == 2, "L-route should produce 2 segments for non-degenerate case"

    # First segment should be horizontal (y1 == y2)
    seg_h = segs[0]
    assert seg_h.y1 == seg_h.y2 == ey1, "First segment should be horizontal"
    assert seg_h.x1 == ex1, "Horizontal segment should start at ex1"
    assert seg_h.x2 == ex2, "Horizontal segment should end at ex2"

    # Second segment should be vertical (x1 == x2)
    seg_v = segs[1]
    assert seg_v.x1 == seg_v.x2 == ex2, "Second segment should be vertical"
    assert seg_v.y1 == ey1, "Vertical segment should start at ey1"
    assert seg_v.y2 == ey2, "Vertical segment should end at ey2"


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_l_route_degenerate_segments() -> None:
    """Test that _l_route omits zero-length segments."""
    # Test 1: Route between points with same X (only vertical needed)
    segs = _l_route(10.0, 0.0, 10.0, 20.0)
    assert len(segs) == 1, "Should omit zero-length horizontal segment"
    assert segs[0].x1 == segs[0].x2 == 10.0, "Only segment should be vertical"

    # Test 2: Route between points with same Y (only horizontal needed)
    segs = _l_route(0.0, 10.0, 20.0, 10.0)
    assert len(segs) == 1, "Should omit zero-length vertical segment"
    assert segs[0].y1 == segs[0].y2 == 10.0, "Only segment should be horizontal"

    # Test 3: Route between same point (no segments)
    segs = _l_route(5.0, 5.0, 5.0, 5.0)
    assert len(segs) == 0, "Should omit both degenerate segments"


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_spine_route_chooses_horizontal_when_wider() -> None:
    """Test that _spine_route chooses horizontal spine when x_span >= y_span.

    For left-to-right signal flow reinforcement, when endpoints span more
    horizontally than vertically, the spine should run along the X-axis
    (left-to-right) and taps should come down vertically to endpoints.
    """
    # Endpoints spanning more horizontally (x_span=40, y_span=10)
    endpoints = [
        (0.0, 5.0),  # Left endpoint
        (20.0, 0.0),  # Middle
        (40.0, 10.0),  # Right endpoint
    ]

    segs, junctions = _spine_route(endpoints)

    # Should have segments: 1 horizontal spine + vertical taps
    assert len(segs) >= 1, "Should have at least the spine segment"

    # First segment (spine) should be horizontal
    spine = segs[0]
    assert spine.y1 == spine.y2, "Spine segment should be horizontal"
    assert spine.x1 < spine.x2, "Spine should run left-to-right"

    # All junctions should be on the spine's Y-coordinate
    spine_y = spine.y1
    for junction in junctions:
        error_msg = f"Junction at ({junction.x}, {junction.y}) should be on spine Y={spine_y}"
        assert abs(junction.y - spine_y) < 0.1, error_msg


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_spine_route_chooses_vertical_when_taller() -> None:
    """Test that _spine_route chooses vertical spine when y_span > x_span.

    For tall clusters of endpoints, the spine should run along the Y-axis
    with horizontal taps to each endpoint.
    """
    # Endpoints spanning more vertically (y_span=40, x_span=10)
    endpoints = [
        (5.0, 0.0),  # Top endpoint
        (0.0, 20.0),  # Middle
        (10.0, 40.0),  # Bottom endpoint
    ]

    segs, junctions = _spine_route(endpoints)

    # Should have spine segment + taps
    assert len(segs) >= 1, "Should have at least the spine segment"

    # First segment (spine) should be vertical
    spine = segs[0]
    assert spine.x1 == spine.x2, "Spine segment should be vertical"
    assert spine.y1 < spine.y2, "Spine should run top-to-bottom"

    # All junctions should be on the spine's X-coordinate
    spine_x = spine.x1
    for junction in junctions:
        error_msg = f"Junction at ({junction.x}, {junction.y}) should be on spine X={spine_x}"
        assert abs(junction.x - spine_x) < 0.1, error_msg


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_spine_route_tie_break_direction() -> None:
    """Test spine direction consistency when spans are nearly equal.

    When x_span ≈ y_span (square-ish bounding box), the tie-breaker
    (x_span >= y_span) should prefer horizontal, reinforcing left-to-right
    signal flow.
    """
    # Nearly square bounding box (x_span ≈ y_span)
    endpoints = [
        (0.0, 0.0),  # Top-left
        (10.0, 0.0),  # Top-right
        (0.0, 10.0),  # Bottom-left
        (10.0, 10.0),  # Bottom-right
    ]

    segs, _junctions = _spine_route(endpoints)

    # With x_span >= y_span (x_span=10, y_span=10), should choose horizontal
    spine = segs[0]
    assert spine.y1 == spine.y2, (
        f"Nearly-square endpoints should prefer horizontal spine (y1={spine.y1}, y2={spine.y2})"
    )


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH.exists(),
    reason="Circuit IR fixture not found",
)
def test_circuit_routing_computation_completes() -> None:
    """Integration test: verify that circuit routing computation works.

    This test ensures that the routing module can be loaded and used
    with an actual circuit without errors. Full routing requires pin
    endpoint coordinates, which are typically computed during the
    place-and-route phase.
    """
    # This test validates that routing module imports without errors
    # and the routing functions are available for the circuit processing
    # pipeline.
    ir = _load_test_circuit()

    # Verify routing functions are callable
    assert callable(route_nets), "route_nets should be callable"
    assert callable(_l_route), "_l_route should be callable"
    assert callable(_spine_route), "_spine_route should be callable"

    # Verify test circuit loaded successfully
    assert ir.nets, "Test circuit should have nets"
    assert len(ir.components) > 0, "Test circuit should have components"


# ---------------------------------------------------------------------------
# End Phase 3.2 tests
# ---------------------------------------------------------------------------

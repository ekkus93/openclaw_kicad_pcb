"""Schematic apply: pin transforms, mirror refinement, debug serialization, symbol writing."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .. import placeholder_symbol as _placeholder_mod
from .._router_geometry_basic import _stub_end
from ..component_types import (
    component_type,
    is_power_net,
    normalize_gnd_net_name,
    power_rail_polarity,
)
from ..errors import ErrorCode, UserError
from ..fs import _new_uuid
from ..layout import compute_orientations
from ..layout_engine import LayoutEngine, make_layout_engine
from ..router import PinAnchor, RouteDecision, RoutingHeuristicPolicy
from ..sch_doc import SchematicDoc
from ..symbol_index import SymbolIndex
from ..tier import assign_tiers
from ._sch_apply_embed import _embed_symbol_if_found, _resolve_placed_symbol_pin_at
from ._sch_apply_types import (
    _LOCAL_DECOUPLING_DISTANCE_WARN_MM,
    _PlacedSymbolSpec,
    _prefer_decoupling_side_candidates,
)

if TYPE_CHECKING:
    from ..circuit_ir import CircuitIR


def _transform_pin_at(
    pin_at: dict[str, tuple[float, float, float]],
    origin_x: float,
    origin_y: float,
    rotation: int,
) -> dict[str, tuple[float, float, float]]:
    """Apply KiCad 9 symbol rotation and origin translation to a library pin map.

    ``pin_at`` maps ``pin_num -> (px, py, pa)`` in library space. KiCad 9
    applies the symbol transform directly to those coordinates; there is no
    extra library-to-schematic Y-axis reflection. The returned angle is the
    router's bodyward direction in screen coordinates, which is the negation
    of KiCad's transformed pin angle so ``_stub_end`` extends outward.
    """
    if rotation == 0:
        return {
            pin_num: (origin_x + px, origin_y + py, (-pa) % 360)
            for pin_num, (px, py, pa) in pin_at.items()
        }
    theta = math.radians(rotation)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    return {
        pin_num: (
            origin_x + cos_t * px - sin_t * py,
            origin_y + sin_t * px + cos_t * py,
            (rotation - pa) % 360,
        )
        for pin_num, (px, py, pa) in pin_at.items()
    }


def _build_net_membership_maps(
    *,
    ir: CircuitIR,
    layout: dict[str, tuple[float, float]],
) -> tuple[
    dict[tuple[str, str], str],
    dict[str, list[tuple[str, tuple[float, float]]]],
]:
    net_by_pin: dict[tuple[str, str], str] = {}
    members_by_net: dict[str, list[tuple[str, tuple[float, float]]]] = {}
    for net in ir.nets:
        members: list[tuple[str, tuple[float, float]]] = []
        for pin in net.pins:
            net_by_pin[(pin.ref, pin.pin)] = net.name
            if pin.ref in layout:
                members.append((pin.ref, layout[pin.ref]))
        members_by_net[net.name] = members

    return net_by_pin, members_by_net


def _build_passive_mirror_pin_maps(
    *,
    ir: CircuitIR,
    placed_symbol_specs: dict[str, _PlacedSymbolSpec] | None,
    symbol_index: SymbolIndex,
) -> tuple[dict[str, tuple[str, ...]], dict[str, dict[str, tuple[float, float, float]]]]:
    placed_specs = placed_symbol_specs or {}
    pin_at_by_ref: dict[str, dict[str, tuple[float, float, float]]] = {}
    valid_pins_by_ref: dict[str, tuple[str, ...]] = {}
    for component in ir.components:
        placed_symbol = placed_specs.get(component.ref)
        if placed_symbol is None:
            placed_symbol = _PlacedSymbolSpec(
                unit=1,
                pin_nums=tuple(sorted(symbol_index.get_pins(component.symbol))),
                logical_ref=component.ref,
            )
        valid_pins_by_ref[component.ref] = tuple(placed_symbol.pin_nums)
        pin_at_by_ref[component.ref] = _resolve_placed_symbol_pin_at(
            component.symbol,
            placed_symbol,
            symbol_index,
        )

    return valid_pins_by_ref, pin_at_by_ref


def _refine_two_pin_passive_mirrors(
    *,
    ir: CircuitIR,
    layout: dict[str, tuple[float, float]],
    orientations: dict[str, int],
    placed_symbol_specs: dict[str, _PlacedSymbolSpec] | None,
    symbol_index: SymbolIndex,
) -> dict[str, int]:
    """Flip two-pin passives by 180° when that better matches connected nets."""

    net_by_pin, members_by_net = _build_net_membership_maps(ir=ir, layout=layout)
    valid_pins_by_ref, pin_at_by_ref = _build_passive_mirror_pin_maps(
        ir=ir,
        placed_symbol_specs=placed_symbol_specs,
        symbol_index=symbol_index,
    )

    def _transformed_endpoints(ref: str, rotation: int) -> dict[str, tuple[float, float, float]]:
        x, y = layout[ref]
        return _transform_pin_at(pin_at_by_ref[ref], x, y, rotation)

    refined = dict(orientations)
    for component in ir.components:
        ref = component.ref
        if ref not in layout or component_type(ref) != "passive":
            continue
        valid_pins = valid_pins_by_ref.get(ref, ())
        if len(valid_pins) != 2:
            continue
        if any((ref, pin_num) not in net_by_pin for pin_num in valid_pins):
            continue

        def _candidate_key(rotation: int) -> tuple[int, float, float]:
            transformed = _transformed_endpoints(ref, rotation)
            collision_count = 0
            distance_score = 0.0
            stub_distance_score = 0.0
            for pin_num in valid_pins:
                endpoint = transformed.get(pin_num)
                net_name = net_by_pin.get((ref, pin_num))
                if endpoint is None or net_name is None:
                    continue
                rounded_endpoint = (round(endpoint[0], 2), round(endpoint[1], 2))
                for other in ir.components:
                    if other.ref == ref or other.ref not in layout:
                        continue
                    other_endpoints = _transformed_endpoints(
                        other.ref,
                        refined.get(other.ref, 0),
                    )
                    for other_pin_num in valid_pins_by_ref.get(other.ref, ()):
                        other_endpoint = other_endpoints.get(other_pin_num)
                        if other_endpoint is None:
                            continue
                        if rounded_endpoint != (
                            round(other_endpoint[0], 2),
                            round(other_endpoint[1], 2),
                        ):
                            continue
                        other_net_name = net_by_pin.get((other.ref, other_pin_num))
                        if other_net_name is not None and other_net_name != net_name:
                            collision_count += 1
                neighbors = [
                    position
                    for other_ref, position in members_by_net.get(net_name, [])
                    if other_ref != ref
                ]
                if neighbors:
                    centroid_x = sum(px for px, _py in neighbors) / len(neighbors)
                    centroid_y = sum(py for _px, py in neighbors) / len(neighbors)
                    distance_score += math.dist(
                        (endpoint[0], endpoint[1]),
                        (centroid_x, centroid_y),
                    )
                    stub_x, stub_y = _stub_end(*endpoint)
                    stub_distance_score += math.dist(
                        (stub_x, stub_y),
                        (centroid_x, centroid_y),
                    )
            return collision_count, distance_score, stub_distance_score

        current_rotation = refined.get(ref, 0)
        mirrored_rotation = (current_rotation + 180) % 360
        if _candidate_key(mirrored_rotation) < _candidate_key(current_rotation):
            refined[ref] = mirrored_rotation

    return refined


def _layout_decoupling_distance_warnings(
    ir: CircuitIR,
    raw_layout: dict[str, tuple[float, float, float | None]],
) -> list[dict[str, object]]:
    component_nets: dict[str, set[str]] = {}
    for net in ir.nets:
        for pin_ref in net.pins:
            component_nets.setdefault(pin_ref.ref, set()).add(net.name)

    active_ics_by_rail: dict[str, list[str]] = {}
    for component in ir.components:
        if component_type(component.ref) != "ic":
            continue
        if component.ref not in raw_layout:
            continue

        nets = component_nets.get(component.ref, set())
        if not nets or not any(not is_power_net(net_name) for net_name in nets):
            continue

        for net_name in nets:
            if power_rail_polarity(net_name) is not None:
                active_ics_by_rail.setdefault(net_name, []).append(component.ref)

    warnings: list[dict[str, object]] = []
    for component in ir.components:
        if component_type(component.ref) != "passive" or not component.ref.upper().startswith("C"):
            continue
        if component.ref not in raw_layout:
            continue

        component_net_names = sorted(component_nets.get(component.ref, set()))
        if len(component_net_names) != 2:
            continue

        rail_net: str | None = None
        reference_net: str | None = None
        for net_name in component_net_names:
            if normalize_gnd_net_name(net_name) == "GND":
                reference_net = net_name
            elif power_rail_polarity(net_name) is not None:
                rail_net = net_name
        if rail_net is None or reference_net is None:
            continue

        candidate_refs = active_ics_by_rail.get(rail_net, [])
        if not candidate_refs:
            continue

        cap_x, cap_y, _ = raw_layout[component.ref]
        rail_polarity = power_rail_polarity(rail_net)
        candidate_refs = _prefer_decoupling_side_candidates(
            cap_y=cap_y,
            rail_polarity=rail_polarity,
            candidate_refs=candidate_refs,
            raw_layout=raw_layout,
        )
        nearest_ref = min(
            candidate_refs,
            key=lambda ref: math.dist((cap_x, cap_y), raw_layout[ref][:2]),
        )
        nearest_x, nearest_y, _ = raw_layout[nearest_ref]
        distance_mm = math.dist((cap_x, cap_y), (nearest_x, nearest_y))
        if distance_mm <= _LOCAL_DECOUPLING_DISTANCE_WARN_MM:
            continue

        warnings.append(
            {
                "code": "DECOUPLING_FAR_FROM_ACTIVE_DEVICE",
                "message": (
                    f"Decoupling capacitor {component.ref} between {rail_net} and {reference_net} "
                    f"is placed {distance_mm:.2f} mm from active device {nearest_ref}, which is "
                    "too far to read as local support circuitry."
                ),
                "details": {
                    "capacitor_ref": component.ref,
                    "rail_net": rail_net,
                    "rail_polarity": rail_polarity,
                    "reference_net": reference_net,
                    "nearest_active_ref": nearest_ref,
                    "distance_mm": round(distance_mm, 2),
                    "max_local_distance_mm": round(_LOCAL_DECOUPLING_DISTANCE_WARN_MM, 2),
                },
            }
        )

    return warnings


def _build_net_classification_summary(decisions: list[RouteDecision]) -> list[dict[str, object]]:
    """Return the coarse per-net classification debug summary."""
    return [
        {
            "net_name": decision.net_name,
            "classification": decision.classification,
            "pin_count": decision.pin_count,
            "known_pin_count": decision.known_pin_count,
            "unknown_pin_count": decision.unknown_pin_count,
        }
        for decision in decisions
    ]


def _serialize_route_decisions(decisions: list[RouteDecision]) -> list[dict[str, object]]:
    """Return the final routing strategy selected for each net."""
    return [
        {
            "net_name": decision.net_name,
            "classification": decision.classification,
            "strategy": decision.strategy,
            "pin_count": decision.pin_count,
            "known_pin_count": decision.known_pin_count,
            "unknown_pin_count": decision.unknown_pin_count,
            "use_bus": decision.use_bus,
            "heuristic_override": decision.heuristic_override,
        }
        for decision in decisions
    ]


def _serialize_routing_heuristic_policy(policy: RoutingHeuristicPolicy) -> dict[str, bool]:
    """Return the active routing-heuristic toggles for debug dumps."""
    return {
        "enable_compact_output_tails": policy.enable_compact_output_tails,
        "enable_compact_local_ground_clusters": policy.enable_compact_local_ground_clusters,
    }


def _write_schematic_debug_dump(path: Path, payload: dict[str, object]) -> None:
    """Merge post-generation schematic debug details into the JSON sidecar."""
    merged: dict[str, object] = {}
    if path.exists():
        merged = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
    merged.update(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")


def _write_symbols(  # noqa: PLR0913
    *,
    doc: SchematicDoc,
    ir: CircuitIR,
    symbol_index: SymbolIndex,
    placed_symbol_specs: dict[str, _PlacedSymbolSpec] | None = None,
    placeholders: dict[str, _placeholder_mod.PlaceholderSymbol] | None = None,
    project_name: str,
    stats: dict[str, int],
    engine: LayoutEngine | None = None,
    cache_path: Path | None = None,
    strict: bool = False,
) -> tuple[
    dict[str, tuple[float, float]],
    dict[tuple[str, str], tuple[float, float, float]],
    dict[tuple[str, str], PinAnchor],
    set[str],
    dict[str, tuple[float, float, float | None]],
]:
    """Place all symbols from *ir* into *doc*.

        Returns a 5-tuple of:
    * ``symbol_positions``  — ``{ref: (x, y)}`` placed-symbol origins.
    * ``pin_endpoints``     — ``{(ref, pin_num): (x, y, angle)}`` actual
      pin connection-point coordinates in schematic space, derived from the
      library symbol's ``(pin ... (at x y angle) ...)`` data translated by
      the symbol placement position.  *angle* is the KiCad pin direction
      (0=right, 90=down, 180=left, 270=up) pointing **from the endpoint
      toward the symbol body** — wire stubs extend in the opposite direction.
        * ``pin_anchors``       — ``{(ref, pin_num): PinAnchor(...)}`` explicit
            placed-unit anchor ownership plus the same schematic-space endpoint
            geometry used by the router.
    * ``symbol_defs_missing`` — set of symbol ids whose library def was
      not found (embedded as best-effort empty stubs).
    * ``raw_layout``        — ``{ref: (x, y, rotation)}`` full layout positions
      (including rotation) as returned by the layout engine; used by the router
      for body-crossing avoidance.
    """
    symbol_positions: dict[str, tuple[float, float]] = {}
    # (ref, pin_number) -> (schematic_x, schematic_y, pin_angle)
    pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {}
    pin_anchors: dict[tuple[str, str], PinAnchor] = {}
    symbol_defs_missing: set[str] = set()

    if engine is None:
        engine = make_layout_engine(cache_path=cache_path, strict=strict)
    raw_layout = engine.compute_symbol_positions(ir)
    # Build plain (x, y) map for coordinate lookup and orientation computation.
    layout: dict[str, tuple[float, float]] = {
        ref: (pos[0], pos[1]) for ref, pos in raw_layout.items()
    }
    # Prefer engine-provided rotation (non-None) over computing it separately.
    # GraphvizLayoutEngine always returns a float rotation; NoneLayoutEngine returns
    # None, in which case we fall back to the standalone compute_orientations call.
    _need_fallback_orientations = any(pos[2] is None for pos in raw_layout.values())
    if _need_fallback_orientations:
        if strict:
            missing_rotation_refs = sorted(
                ref for ref, (_x, _y, rot) in raw_layout.items() if rot is None
            )
            raise UserError(
                "Layout engine returned missing symbol rotations in strict mode",
                code=ErrorCode.IR_SEMANTIC_INVALID,
                details={"refs_missing_rotation": missing_rotation_refs},
            )
        tiers: dict[str, int] = assign_tiers(ir, strict=strict)
        orientations: dict[str, int] = compute_orientations(
            ir,
            layout,
            tiers,
            placed_pin_numbers={
                ref: spec.pin_nums for ref, spec in (placed_symbol_specs or {}).items()
            }
            or None,
        )
    else:
        orientations = {ref: int(pos[2]) for ref, pos in raw_layout.items() if pos[2] is not None}
    orientations = _refine_two_pin_passive_mirrors(
        ir=ir,
        layout=layout,
        orientations=orientations,
        placed_symbol_specs=placed_symbol_specs,
        symbol_index=symbol_index,
    )

    for component in sorted(ir.components, key=lambda c: c.ref):
        x, y = layout[component.ref]
        placed_symbol = (placed_symbol_specs or {}).get(component.ref)
        if placed_symbol is None:
            placed_symbol = _PlacedSymbolSpec(
                unit=1,
                pin_nums=tuple(sorted(symbol_index.get_pins(component.symbol))),
                logical_ref=component.ref,
            )
        valid_pins = list(placed_symbol.pin_nums)
        pin_uuids = [_new_uuid() for _ in valid_pins]

        _ph = (placeholders or {}).get(component.symbol)
        if not _embed_symbol_if_found(
            doc=doc,
            symbol=component.symbol,
            symbol_index=symbol_index,
            placeholder=_ph,
        ):
            symbol_defs_missing.add(component.symbol)

        doc.add_symbol(
            component.symbol,
            component.ref,
            component.value or component.ref,
            component.footprint or "",
            x,
            y,
            _new_uuid(),
            valid_pins,
            pin_uuids,
            project_name,
            unit=placed_symbol.unit,
            rotation=orientations.get(component.ref, 0),
        )
        symbol_positions[component.ref] = (x, y)

        # Compute pin endpoint positions in schematic space.
        # Pin (at px py angle) in library space is transformed by rotation θ
        # via _transform_pin_at; when θ=0 this is a pure translation.
        rotation = orientations.get(component.ref, 0)
        pin_at = _resolve_placed_symbol_pin_at(
            component.symbol,
            placed_symbol,
            symbol_index,
        )
        transformed = _transform_pin_at(pin_at, x, y, rotation)
        for pin_num, endpoint in transformed.items():
            pin_endpoints[(component.ref, pin_num)] = endpoint
            pin_anchors[(component.ref, pin_num)] = PinAnchor(
                ref=component.ref,
                pin=pin_num,
                x=endpoint[0],
                y=endpoint[1],
                angle=endpoint[2],
                unit=placed_symbol.unit,
            )

        stats["symbols"] += 1

    return symbol_positions, pin_endpoints, pin_anchors, symbol_defs_missing, raw_layout

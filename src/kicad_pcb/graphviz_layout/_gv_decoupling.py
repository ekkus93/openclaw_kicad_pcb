"""Graphviz layout: shared-rail decoupling cap anchor refinement."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from ..component_types import (
    component_type,
    is_power_net,
    normalize_gnd_net_name,
    power_rail_polarity,
)

if TYPE_CHECKING:
    from ..circuit_ir import CircuitIR


def _prefer_decoupling_side_candidates_from_layout(
    *,
    cap_y: float,
    rail_polarity: str | None,
    candidate_refs: list[str],
    raw_layout: Mapping[str, tuple[float, float, float | None]],
) -> list[str]:
    """Prefer the active-device side that matches the rail polarity."""
    if rail_polarity == "positive":
        same_side = [ref for ref in candidate_refs if raw_layout[ref][1] > cap_y]
        return same_side or candidate_refs
    if rail_polarity == "negative":
        same_side = [ref for ref in candidate_refs if raw_layout[ref][1] < cap_y]
        return same_side or candidate_refs
    return candidate_refs


def _shared_rail_anchor_candidates_from_layout(
    *,
    rail_net: str,
    component_nets: Mapping[str, set[str]],
    raw_layout: Mapping[str, tuple[float, float, float | None]],
) -> list[str]:
    """Return active IC candidates for a shared rail, including split-unit siblings.

    In expanded generation IR, a shared rail can touch only a dedicated power
    unit such as ``U1P`` even though the visible signal stages are ``U1A`` and
    ``U1B``. The initial decoupling detector already falls back from the power
    unit to those sibling signal units. The raw-layout refinement must use the
    same candidate set or it will silently skip the exact multi-unit case that
    needs the geometry-aware tie-break.
    """

    active_ics_by_rail: dict[str, list[str]] = {}
    signal_ic_refs: set[str] = set()
    for component_ref, net_names in component_nets.items():
        if component_type(component_ref) != "ic" or component_ref not in raw_layout:
            continue
        if not net_names or not any(not is_power_net(net_name) for net_name in net_names):
            continue
        signal_ic_refs.add(component_ref)
        for net_name in net_names:
            if power_rail_polarity(net_name) is not None:
                active_ics_by_rail.setdefault(net_name, []).append(component_ref)

    direct_candidates = active_ics_by_rail.get(rail_net, [])
    if direct_candidates:
        return sorted(set(direct_candidates))

    sibling_candidates: list[str] = []
    for component_ref, net_names in component_nets.items():
        if (
            rail_net not in net_names
            or component_type(component_ref) != "ic"
            or len(component_ref) < 2
        ):
            continue
        suffix = component_ref[-1]
        if not suffix.isalpha():
            continue
        parent_ref = component_ref[:-1]
        sibling_candidates.extend(
            candidate_ref
            for candidate_ref in signal_ic_refs
            if candidate_ref[:-1] == parent_ref and candidate_ref != component_ref
        )
    return sorted(set(sibling_candidates))


def _refine_shared_rail_decoupling_map(
    ir: CircuitIR,
    raw_layout: Mapping[str, tuple[float, float, float | None]],
    decoupling_map: Mapping[str, str],
) -> dict[str, str]:
    """Refine ambiguous shared-rail decoupling anchors using raw layout geometry."""
    if not decoupling_map or not raw_layout:
        return dict(decoupling_map)

    component_nets: dict[str, set[str]] = {}
    for net in ir.nets:
        for pin_ref in net.pins:
            component_nets.setdefault(pin_ref.ref, set()).add(net.name)

    refined_map = dict(decoupling_map)
    for cap_ref in decoupling_map:
        if cap_ref not in raw_layout:
            continue

        net_names = sorted(component_nets.get(cap_ref, set()))
        rail_nets = [name for name in net_names if power_rail_polarity(name) is not None]
        ground_nets = [name for name in net_names if normalize_gnd_net_name(name) == "GND"]
        if len(rail_nets) != 1 or len(ground_nets) != 1:
            continue

        candidate_refs = _shared_rail_anchor_candidates_from_layout(
            rail_net=rail_nets[0],
            component_nets=component_nets,
            raw_layout=raw_layout,
        )
        if len(candidate_refs) <= 1:
            continue

        cap_x, cap_y, _ = raw_layout[cap_ref]
        filtered_candidates = _prefer_decoupling_side_candidates_from_layout(
            cap_y=cap_y,
            rail_polarity=power_rail_polarity(rail_nets[0]),
            candidate_refs=candidate_refs,
            raw_layout=raw_layout,
        )
        refined_map[cap_ref] = min(
            filtered_candidates,
            key=lambda ref: (abs(raw_layout[ref][0] - cap_x), abs(raw_layout[ref][1] - cap_y), ref),
        )

    return refined_map

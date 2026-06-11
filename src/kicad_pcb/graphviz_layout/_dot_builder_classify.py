"""Component/net classification, decoupling cap detection, and BFS tier assignment."""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..circuit_ir import CircuitIR

from ..block_detection import _is_supply_like_net
from ..component_types import CAPACITOR_PREFIXES as _CAPACITOR_PREFIXES_CT
from ..component_types import CONNECTOR_PREFIXES as _CONNECTOR_PREFIXES_CT
from ..component_types import component_type, power_rail_polarity
from ..component_types import is_ground_like_name as _is_ground_like_name
from ..component_types import is_power_net as _is_power_net
from ..lib_symbol import read_lib_symbol_pin_electrical_types


def _is_connector(ref: str) -> bool:
    """Return True if *ref* is a connector designator (``J*``, ``P*``, etc.)."""
    r = ref.upper()
    return any(r.startswith(p) for p in _CONNECTOR_PREFIXES_CT)


def _is_capacitor(ref: str) -> bool:
    """Return True if *ref* is a capacitor designator (``C*``)."""
    r = ref.upper()
    return any(r.startswith(p) for p in _CAPACITOR_PREFIXES_CT)


def _preferred_decoupling_anchor(
    candidate_refs: list[str],
    ref_to_nets: Mapping[str, list[str]],
) -> str | None:
    """Return the most relevant anchor ref for a decoupling capacitor.

    Prefer explicit IC refs first, then components that participate in more
    non-power nets. This prevents local rail passives from stealing the anchor
    when the same net also touches the actual active stage the capacitor serves.
    """
    unique_candidates = sorted(set(candidate_refs))
    if not unique_candidates:
        return None

    def _sort_key(ref: str) -> tuple[int, int, int, str]:
        nets_for_ref = ref_to_nets.get(ref, [])
        signal_net_count = sum(1 for net_name in nets_for_ref if not _is_power_net(net_name))
        rail_net_count = sum(
            1 for net_name in nets_for_ref if power_rail_polarity(net_name) is not None
        )
        return (
            1 if component_type(ref) == "ic" else 0,
            signal_net_count,
            rail_net_count,
            ref,
        )

    return max(unique_candidates, key=_sort_key)


def _non_power_net_uses_only_power_output_pins(
    ir: CircuitIR,
    cap_ref: str,
    net_name: str,
) -> bool:
    """Return True for charge-pump local nets driven only by IC ``power_out`` pins."""
    upper_name = net_name.strip().upper()
    match = re.match(r"^NET-\([^)]*-(.+)\)$", upper_name)
    if match is None or match.group(1) not in {"VS+", "VS-", "V+", "V-"}:
        return False

    component_by_ref = {comp.ref: comp for comp in ir.components}
    net = next((candidate for candidate in ir.nets if candidate.name == net_name), None)
    if net is None:
        return False

    candidate_pins = [
        pin for pin in net.pins if pin.ref != cap_ref and component_type(pin.ref) == "ic"
    ]
    if not candidate_pins:
        return False

    pin_types_by_symbol: dict[str, dict[str, str]] = {}
    resolved_types: list[str] = []
    for pin in candidate_pins:
        comp = component_by_ref.get(pin.ref)
        if comp is None or ":" not in comp.symbol:
            return False
        symbol_key = comp.symbol
        if symbol_key not in pin_types_by_symbol:
            lib_name, sym_name = symbol_key.split(":", 1)
            pin_types_by_symbol[symbol_key] = read_lib_symbol_pin_electrical_types(
                lib_name,
                sym_name,
            )
        pin_type = pin_types_by_symbol[symbol_key].get(pin.pin)
        if pin_type is None:
            return False
        resolved_types.append(pin_type)
    return bool(resolved_types) and all(pin_type == "power_out" for pin_type in resolved_types)


def _is_private_signal_net_name(net_name: str) -> bool:
    return re.match(r"^Net-\(", net_name.strip(), re.IGNORECASE) is not None


def _rail_anchor_candidates(
    rail_net: str,
    *,
    active_ics_by_rail: Mapping[str, list[str]],
    net_to_refs: Mapping[str, list[str]],
    signal_ic_refs: set[str],
) -> list[str]:
    direct_candidates = active_ics_by_rail.get(rail_net, [])
    if direct_candidates:
        return direct_candidates

    sibling_candidates: list[str] = []
    for neighbor_ref in net_to_refs.get(rail_net, []):
        if component_type(neighbor_ref) != "ic" or len(neighbor_ref) < 2:
            continue
        suffix = neighbor_ref[-1]
        if not suffix.isalpha():
            continue
        parent_ref = neighbor_ref[:-1]
        sibling_candidates.extend(
            candidate_ref
            for candidate_ref in signal_ic_refs
            if candidate_ref[:-1] == parent_ref and candidate_ref != neighbor_ref
        )
    return sorted(set(sibling_candidates))


def _signal_power_decoupling_anchor(
    *,
    ir: CircuitIR,
    cap_ref: str,
    nets_for_cap: list[str],
    net_to_refs: Mapping[str, list[str]],
    ref_to_nets: Mapping[str, list[str]],
) -> str | None:
    signal_nets_for_cap = [n for n in nets_for_cap if not _is_power_net(n)]
    power_nets_for_cap = [n for n in nets_for_cap if _is_power_net(n)]
    if len(signal_nets_for_cap) != 1 or not power_nets_for_cap:
        return None

    signal_net = signal_nets_for_cap[0]
    if _non_power_net_uses_only_power_output_pins(ir, cap_ref, signal_net):
        return None

    candidate_refs = [
        neighbor_ref
        for neighbor_ref in net_to_refs.get(signal_net, [])
        if neighbor_ref != cap_ref
        and not _is_connector(neighbor_ref)
        and not _is_capacitor(neighbor_ref)
    ]
    unique_candidate_refs = sorted(set(candidate_refs))
    if not (
        (len(unique_candidate_refs) == 1 and _is_private_signal_net_name(signal_net))
        or _is_supply_like_net(signal_net)
        or "BIAS" in signal_net.upper()
    ):
        return None

    return _preferred_decoupling_anchor(candidate_refs, ref_to_nets)


def _find_decoupling_caps(ir: CircuitIR) -> dict[str, str]:
    """Return ``{cap_ref: ic_ref}`` for decoupling/bypass capacitors.

    A decoupling cap is a ``C*`` component where **exactly one** pin is on a
    non-power signal net and the remaining pin(s) are on power nets.  The
    associated IC is the first *non-connector, non-capacitor* component that
    shares the same non-power net as the cap.

    This handles bypass caps wired as::

        VCC_LOCAL(signal net) ─── [C1] ─── GND(power net)

    where ``VCC_LOCAL`` is a local distribution net not matched by
    :func:`_is_power_net` (e.g. the IC's filtered supply net).  True
    VCC→GND caps (both pins on recognised power nets) are left in
    ``cluster_power``.
    """
    ref_to_nets: dict[str, list[str]] = {}
    net_to_refs: dict[str, list[str]] = {}

    for net in ir.nets:
        for pin in net.pins:
            ref_to_nets.setdefault(pin.ref, []).append(net.name)
            net_to_refs.setdefault(net.name, []).append(pin.ref)

    active_ics_by_rail: dict[str, list[str]] = {}
    for comp in ir.components:
        if component_type(comp.ref) != "ic":
            continue
        nets_for_ic = ref_to_nets.get(comp.ref, [])
        if not any(not _is_power_net(net_name) for net_name in nets_for_ic):
            continue
        for net_name in nets_for_ic:
            if power_rail_polarity(net_name) is not None:
                active_ics_by_rail.setdefault(net_name, []).append(comp.ref)

    signal_ic_refs = {
        comp.ref
        for comp in ir.components
        if component_type(comp.ref) == "ic"
        and any(not _is_power_net(net_name) for net_name in ref_to_nets.get(comp.ref, []))
    }

    result: dict[str, str] = {}
    for comp in ir.components:
        if not _is_capacitor(comp.ref):
            continue
        nets_for_cap = ref_to_nets.get(comp.ref, [])
        signal_nets_for_cap = [n for n in nets_for_cap if not _is_power_net(n)]
        if anchor_ref := _signal_power_decoupling_anchor(
            ir=ir,
            cap_ref=comp.ref,
            nets_for_cap=nets_for_cap,
            net_to_refs=net_to_refs,
            ref_to_nets=ref_to_nets,
        ):
            result[comp.ref] = anchor_ref

        if comp.ref in result:
            continue

        ground_nets_for_cap = [n for n in nets_for_cap if _is_ground_like_name(n)]
        rail_nets_for_cap = [n for n in nets_for_cap if power_rail_polarity(n) is not None]
        if signal_nets_for_cap or len(ground_nets_for_cap) != 1 or len(rail_nets_for_cap) != 1:
            continue

        candidate_refs = _rail_anchor_candidates(
            rail_nets_for_cap[0],
            active_ics_by_rail=active_ics_by_rail,
            net_to_refs=net_to_refs,
            signal_ic_refs=signal_ic_refs,
        )
        anchor_ref = _preferred_decoupling_anchor(candidate_refs, ref_to_nets)
        if anchor_ref is not None:
            result[comp.ref] = anchor_ref

    return result


def _assign_bfs_tiers(
    refs: list[str],
    signal_nets: list,  # list[NetIR] — avoid circular import at runtime
) -> dict[str, int]:
    """Return ``{ref: tier}`` for all *refs* using BFS from connector seeds.

    .. note::
        This function is **not called from any production code path** in
        ``gv_dot_builder`` or ``graphviz_layout``.  It is exposed as a
        backwards-compatible re-export (``assign_bfs_tiers``) for tests.
        Production layout uses :func:`~kicad_pcb.tier.assign_tiers`
        (longest-path from all sources) instead.

    Connector refs (``J*``, ``P*``, ``CON*``, etc.) are used as BFS seeds
    processed in sorted (alphabetical) order.  The alphabetically-first
    connector is the most likely signal source; downstream connectors receive
    higher tier values naturally via BFS traversal.

    Non-connector components receive intermediate tiers.  Power-only or
    isolated components default to tier ``0``.
    """
    ref_set = set(refs)
    adj: dict[str, list[str]] = {r: [] for r in refs}
    for net in signal_nets:
        pin_refs = [p.ref for p in net.pins if p.ref in ref_set]
        for i, r1 in enumerate(pin_refs):
            for r2 in pin_refs[i + 1 :]:
                adj[r1].append(r2)
                adj[r2].append(r1)

    all_connectors = sorted(r for r in refs if _is_connector(r))
    seeds = [all_connectors[0]] if all_connectors else sorted(refs)

    tier: dict[str, int] = {}
    queue: deque[str] = deque()
    for seed in seeds:
        if seed not in tier:
            tier[seed] = 0
            queue.append(seed)

    while queue:
        ref = queue.popleft()
        for neighbor in adj.get(ref, []):
            if neighbor not in tier:
                tier[neighbor] = tier[ref] + 1
                queue.append(neighbor)

    for r in refs:
        if r not in tier:
            tier[r] = 0

    return tier

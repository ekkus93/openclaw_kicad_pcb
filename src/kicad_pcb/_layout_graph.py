"""Signal-flow layout: graph builders, BFS, decoupling-cap detection helpers."""

from __future__ import annotations

import logging
import re
from collections import defaultdict, deque
from collections.abc import Mapping
from typing import TYPE_CHECKING

from .block_detection import _is_supply_like_net
from .component_types import CONNECTOR_PREFIXES as _CONNECTOR_PREFIXES_CT
from .component_types import IC_PREFIXES as _IC_PREFIXES_CT
from .component_types import POWER_NET_PREFIXES as _POWER_NET_PREFIXES_CT
from .component_types import component_type, is_power_net, power_rail_polarity
from .component_types import is_ground_like_name as _is_ground_like_name
from .lib_symbol import read_lib_symbol_pin_electrical_types

if TYPE_CHECKING:
    from .circuit_ir import CircuitIR

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Page dimensions (mm)
# ---------------------------------------------------------------------------
PAGE_WIDTH_MM: float = 297.0
PAGE_HEIGHT_MM: float = 210.0

GRID_COL_MM: float = 30.48
GRID_ROW_MM: float = 20.32
ORIGIN_X: float = 30.48
ORIGIN_Y: float = 50.80

MAX_ROWS_PER_COL: int = int((PAGE_HEIGHT_MM - ORIGIN_Y) / GRID_ROW_MM)
MIN_SEPARATION_MM: float = GRID_ROW_MM

_SOURCE_PREFIXES: tuple[str, ...] = _CONNECTOR_PREFIXES_CT
_MAX_COLS: int = 20
_MAX_REMEDIATION_SWEEPS: int = 3
_OP_AMP_PREFIXES: tuple[str, ...] = _IC_PREFIXES_CT
_PASSIVE_PREFIXES: tuple[str, ...] = ("R", "C", "L")
_POWER_NET_PREFIXES: tuple[str, ...] = _POWER_NET_PREFIXES_CT

#: Sentinel BFS distance for components unreachable from a connector direction.
_SDS_SENTINEL: int = 1000


def _is_power_net_layout(name: str) -> bool:
    """Return True when *name* looks like a power/ground rail."""
    upper = name.upper()
    return is_power_net(upper) or any(
        upper == prefix or upper.startswith(prefix) for prefix in _POWER_NET_PREFIXES
    )


def _build_signal_adjacency(ir: CircuitIR) -> dict[str, set[str]]:
    """Return adjacency graph built from signal nets only (power nets excluded)."""
    adjacency: dict[str, set[str]] = defaultdict(set)
    for net in ir.nets:
        if _is_power_net_layout(net.name):
            continue
        pin_refs = [p.ref for p in net.pins]
        for i, r_i in enumerate(pin_refs):
            for r_j in pin_refs[i + 1 :]:
                if r_i != r_j:
                    adjacency[r_i].add(r_j)
                    adjacency[r_j].add(r_i)
    return adjacency


def _build_power_adjacency(ir: CircuitIR) -> dict[str, set[str]]:
    """Return adjacency graph built from power nets only (signal nets excluded)."""
    adjacency: dict[str, set[str]] = defaultdict(set)
    for net in ir.nets:
        if not _is_power_net_layout(net.name):
            continue
        pin_refs = [p.ref for p in net.pins]
        for i, r_i in enumerate(pin_refs):
            for r_j in pin_refs[i + 1 :]:
                if r_i != r_j:
                    adjacency[r_i].add(r_j)
                    adjacency[r_j].add(r_i)
    return adjacency


def _bfs_distances(
    adjacency: dict[str, set[str]],
    seeds: list[str],
) -> dict[str, int]:
    """Return BFS distances from *seeds* over *adjacency*."""
    dist: dict[str, int] = {}
    queue: deque[str] = deque()
    for s in seeds:
        if s not in dist:
            dist[s] = 0
            queue.append(s)
    while queue:
        ref = queue.popleft()
        d = dist[ref]
        for nbr in adjacency.get(ref, set()):
            if nbr not in dist:
                dist[nbr] = d + 1
                queue.append(nbr)
    return dist


def _preferred_decoupling_anchor_layout(
    candidate_refs: list[str],
    ref_to_nets: Mapping[str, list[str]],
) -> str | None:
    """Return the most relevant anchor ref for a decoupling capacitor."""
    unique_candidates = sorted(set(candidate_refs))
    if not unique_candidates:
        return None

    def _sort_key(ref: str) -> tuple[int, int, int, str]:
        nets_for_ref = ref_to_nets.get(ref, [])
        signal_net_count = sum(1 for net_name in nets_for_ref if not _is_power_net_layout(net_name))
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


def _find_decoupling_caps_layout(ir: CircuitIR) -> dict[str, str]:  # noqa: PLR0912, PLR0915
    """Return ``{cap_ref: ic_ref}`` for bypass/decoupling capacitors."""

    def _is_private_signal_net_name(net_name: str) -> bool:
        return re.match(r"^Net-\(", net_name.strip(), re.IGNORECASE) is not None

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
        if not any(not _is_power_net_layout(net_name) for net_name in nets_for_ic):
            continue
        for net_name in nets_for_ic:
            if power_rail_polarity(net_name) is not None:
                active_ics_by_rail.setdefault(net_name, []).append(comp.ref)

    signal_ic_refs = {
        comp.ref
        for comp in ir.components
        if component_type(comp.ref) == "ic"
        and any(not _is_power_net_layout(net_name) for net_name in ref_to_nets.get(comp.ref, []))
    }

    def _rail_anchor_candidates(rail_net: str) -> list[str]:
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

    result: dict[str, str] = {}
    for comp in ir.components:
        if not comp.ref.upper().startswith("C"):
            continue
        nets_for_cap = ref_to_nets.get(comp.ref, [])
        signal_nets = [n for n in nets_for_cap if not _is_power_net_layout(n)]
        power_nets = [n for n in nets_for_cap if _is_power_net_layout(n)]
        if len(signal_nets) == 1 and power_nets:
            signal_net = signal_nets[0]
            if _non_power_net_uses_only_power_output_pins(ir, comp.ref, signal_net):
                continue
            candidate_refs = [
                neighbor_ref
                for neighbor_ref in net_to_refs.get(signal_net, [])
                if neighbor_ref != comp.ref
                and not any(neighbor_ref.upper().startswith(p) for p in _CONNECTOR_PREFIXES_CT)
                and not neighbor_ref.upper().startswith("C")
            ]
            unique_candidate_refs = sorted(set(candidate_refs))
            if not (
                (len(unique_candidate_refs) == 1 and _is_private_signal_net_name(signal_net))
                or _is_supply_like_net(signal_net)
                or "BIAS" in signal_net.upper()
            ):
                continue
            anchor_ref = _preferred_decoupling_anchor_layout(candidate_refs, ref_to_nets)
            if anchor_ref is not None:
                result[comp.ref] = anchor_ref

        if comp.ref in result:
            continue

        ground_nets = [n for n in nets_for_cap if _is_ground_like_name(n)]
        rail_nets = [n for n in nets_for_cap if power_rail_polarity(n) is not None]
        if signal_nets or len(ground_nets) != 1 or len(rail_nets) != 1:
            continue

        candidate_refs = _rail_anchor_candidates(rail_nets[0])
        anchor_ref = _preferred_decoupling_anchor_layout(candidate_refs, ref_to_nets)
        if anchor_ref is not None:
            result[comp.ref] = anchor_ref
    return result


def _preferred_shunt_passive_rotation(ir: CircuitIR, ref: str) -> int:
    """Return the preferred 90°/270° mirror for a shunt passive."""
    for net in ir.nets:
        if _is_power_net_layout(net.name) or not any(pin.ref == ref for pin in net.pins):
            continue
        match = re.match(r"^Net-\([^)]*-(.+)\)$", net.name.strip(), re.IGNORECASE)
        if match is None:
            continue
        if match.group(1).upper() in {"VS-", "V-"}:
            return 270
    return 90


def build_signal_adjacency(ir: CircuitIR) -> dict[str, set[str]]:
    """Return the undirected signal-net adjacency graph for *ir*.

    Power nets are excluded so only signal paths influence the result.
    Public wrapper for the internal :func:`_build_signal_adjacency`.
    """
    return _build_signal_adjacency(ir)

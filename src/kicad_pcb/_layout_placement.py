"""Signal-flow layout: component placement and affinity grouping."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ._layout_graph import (
    _MAX_COLS,
    _MAX_REMEDIATION_SWEEPS,
    _OP_AMP_PREFIXES,
    _PASSIVE_PREFIXES,
    GRID_COL_MM,
    GRID_ROW_MM,
    MAX_ROWS_PER_COL,
    ORIGIN_X,
    ORIGIN_Y,
    _build_power_adjacency,
    _build_signal_adjacency,
    _is_power_net_layout,
    _log,
)
from ._layout_sds import (
    _recursive_halving,
    barycentric_sort,
    compute_signal_distance_scores,
    count_wire_crossings,
)
from .errors import ErrorCode, UserError
from .tier import assign_tiers as _assign_tiers
from .tier import classify_connector_roles as _classify_connector_roles
from .tier import identify_main_signal_path

if TYPE_CHECKING:
    from .circuit_ir import CircuitIR


def compute_signal_flow_layout(  # noqa: PLR0912, PLR0915
    ir: CircuitIR,
    halo: dict[str, str] | None = None,
    roles: Mapping[str, str] | None = None,
    strict: bool = False,
) -> dict[str, tuple[float, float]]:
    """Return ``{ref: (x, y)}`` placements for all components in *ir*.

    .. note::
        **Not used by the production pipeline.**  Kept as a self-contained
        algorithm reference and test harness.
    """
    refs = sorted(c.ref for c in ir.components)
    if not refs:
        return {}

    active_roles: Mapping[str, str] | None = roles
    if active_roles is None:
        inferred_tiers = _assign_tiers(ir, strict=strict)
        inferred_roles = _classify_connector_roles(refs, inferred_tiers, ir=ir)
        inferred_input_refs = [ref for ref, role in inferred_roles.items() if role == "input"]
        inferred_output_refs = [ref for ref, role in inferred_roles.items() if role == "output"]

        if inferred_input_refs and inferred_output_refs:
            _log.debug(
                "layout diagnostic: inferred connector roles in "
                "compute_signal_flow_layout and avoided BFS fallback"
            )
            active_roles = inferred_roles
        else:
            raise UserError(
                "SDS layout requires inferable input and output connector roles",
                code=ErrorCode.IR_SEMANTIC_INVALID,
                details={
                    "missing_roles": [
                        role
                        for role, refs_for_role in (
                            ("input", inferred_input_refs),
                            ("output", inferred_output_refs),
                        )
                        if not refs_for_role
                    ],
                    "roles": dict(inferred_roles),
                },
            )

    if active_roles is not None:
        input_refs = [r for r, role in active_roles.items() if role == "input"]
        output_refs = [r for r, role in active_roles.items() if role == "output"]
        if not input_refs or not output_refs:
            missing_role = "input" if not input_refs else "output"
            raise UserError(
                "SDS layout requires both input and output connector roles",
                code=ErrorCode.IR_SEMANTIC_INVALID,
                details={
                    "missing_role": missing_role,
                    "roles": dict(active_roles),
                },
            )
        else:
            sds_scores = compute_signal_distance_scores(ir, active_roles)
            col = _recursive_halving(
                refs,
                sds_scores,
                ORIGIN_X,
                ORIGIN_X + _MAX_COLS * GRID_COL_MM,
                max_per_col=MAX_ROWS_PER_COL,
                grid_col_mm=GRID_COL_MM,
            )

    sig_adj = _build_signal_adjacency(ir)
    pwr_adj = _build_power_adjacency(ir)
    max_col = max(col.values(), default=0)

    for r in refs:
        upper = r.upper()
        if not any(upper.startswith(p) for p in _PASSIVE_PREFIXES):
            continue
        if sig_adj.get(r):
            continue
        ic_neighbors = [
            n
            for n in pwr_adj.get(r, set())
            if any(n.upper().startswith(p) for p in _OP_AMP_PREFIXES)
        ]
        if not ic_neighbors:
            continue
        anchor_col = min(col.get(n, max_col) for n in ic_neighbors)
        col[r] = min(anchor_col + 1, max_col)

    if halo:
        for halo_ref, anchor_ref in halo.items():
            if halo_ref in col and anchor_ref in col:
                old_col = col[halo_ref]
                anchor_col = col[anchor_ref]
                if old_col < anchor_col:
                    new_col = max(anchor_col - 1, 0)
                elif old_col > anchor_col or anchor_col <= 0:
                    new_col = anchor_col + 1
                else:
                    new_col = anchor_col - 1
                if old_col != new_col:
                    _log.debug(
                        "halo: softening %r column %d → %d (anchor %r col %d)",
                        halo_ref,
                        old_col,
                        new_col,
                        anchor_ref,
                        anchor_col,
                    )
                    col[halo_ref] = new_col

    by_col: dict[int, list[str]] = defaultdict(list)
    for r, c in col.items():
        by_col[c].append(r)

    by_col = barycentric_sort(by_col, sig_adj)

    _total_sig_wires = sum(len(v) for v in sig_adj.values()) // 2
    if _total_sig_wires > 0:
        for _sweep in range(_MAX_REMEDIATION_SWEEPS):
            _tentative: dict[str, tuple[float, float]] = {
                ref: (ORIGIN_X + ci * GRID_COL_MM, ORIGIN_Y + ri * GRID_ROW_MM)
                for ci, mems in by_col.items()
                for ri, ref in enumerate(mems)
            }
            _crossings = count_wire_crossings(_tentative, sig_adj)
            _ratio = _crossings / _total_sig_wires
            _log.debug(
                "R6: sweep %d crossings=%d wires=%d ratio=%.2f",
                _sweep + 1,
                _crossings,
                _total_sig_wires,
                _ratio,
            )
            if _ratio < 0.30 or _sweep == _MAX_REMEDIATION_SWEEPS - 1:
                break
            _log.debug(
                "R6: ratio %.2f >= 0.30; running remediation sweep %d",
                _ratio,
                _sweep + 2,
            )
            by_col = barycentric_sort(by_col, sig_adj)

    positions: dict[str, tuple[float, float]] = {}
    visual_col = 0
    for c_num, members in sorted(by_col.items()):
        ic_refs = [r for r in members if any(r.upper().startswith(p) for p in _OP_AMP_PREFIXES)]
        other_refs = [r for r in members if r not in set(ic_refs)]
        _halo_set = set(halo) if halo else set()
        halo_other = [r for r in other_refs if r in _halo_set]
        plain_other = [r for r in other_refs if r not in _halo_set]
        mid_plain = len(plain_other) // 2
        mid_halo = len(halo_other) // 2
        ordered = (
            plain_other[:mid_plain]
            + halo_other[:mid_halo]
            + ic_refs
            + halo_other[mid_halo:]
            + plain_other[mid_plain:]
        )
        num_sub = max(1, math.ceil(len(ordered) / MAX_ROWS_PER_COL))
        for idx, ref in enumerate(ordered):
            sub = idx // MAX_ROWS_PER_COL
            row = idx % MAX_ROWS_PER_COL
            x = ORIGIN_X + (visual_col + sub) * GRID_COL_MM
            y = ORIGIN_Y + row * GRID_ROW_MM
            positions[ref] = (x, y)
        visual_col += num_sub

    return positions


def compute_affinity_groups(
    ir: CircuitIR,
    tiers: dict[str, int],
) -> dict[int, list[str]]:
    """Return ``{tier_index: [ref, …]}`` sorted by signal affinity to the previous tier."""
    ref_nets: dict[str, set[str]] = defaultdict(set)
    for net in ir.nets:
        if _is_power_net_layout(net.name):
            continue
        for pin in net.pins:
            ref_nets[pin.ref].add(net.name)

    def _affinity(a: str, b: str) -> float:
        nets_a = ref_nets.get(a, set())
        nets_b = ref_nets.get(b, set())
        shared = len(nets_a & nets_b)
        denom = min(len(nets_a), len(nets_b))
        if denom == 0:
            return 0.0
        return shared / denom

    main_path = identify_main_signal_path(ir, tiers=tiers)
    main_path_rank = {ref: idx for idx, ref in enumerate(main_path)}

    tier_groups: dict[int, list[str]] = defaultdict(list)
    for ref, tier in tiers.items():
        tier_groups[tier].append(ref)

    result: dict[int, list[str]] = {}
    sorted_tiers = sorted(tier_groups)
    for i, tier in enumerate(sorted_tiers):
        members = tier_groups[tier]
        if i == 0:
            result[tier] = sorted(
                members,
                key=lambda r: (0 if r in main_path_rank else 1, main_path_rank.get(r, 10_000), r),
            )
            continue
        prev_tier = sorted_tiers[i - 1]
        prev_members = result.get(prev_tier, [])
        scores = {ref: sum(_affinity(ref, p) for p in prev_members) for ref in members}
        result[tier] = sorted(
            members,
            key=lambda r: (
                0 if r in main_path_rank else 1,
                main_path_rank.get(r, 10_000),
                -scores[r],
                r,
            ),
        )

    return dict(result)

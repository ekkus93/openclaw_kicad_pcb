"""DOT source builder for the Graphviz schematic layout engine.

Converts a :class:`~kicad_pcb.circuit_ir.CircuitIR` into a Graphviz DOT
source string ready to be piped to ``dot -Tplain``.

Bipartite graph model
---------------------
The DOT graph is *bipartite*:

* **Component nodes** — one ``box`` node per reference designator (e.g. ``R1``).
* **Net nodes** — one ``ellipse`` node per signal net (id ``net_<name>``).
* **Edges** — connect each component to every net it participates in.

Power nets (GND, VCC, VDD, V+, V−, etc.) are *excluded* from the bipartite
graph to prevent highly-connected power hubs from dominating the layout.
Components connected exclusively via power nets are normally placed in a
dedicated ``cluster_power`` subgraph on the right side of the schematic,
except for semantically-classified decoupling parts that should stay near the
active stage they support.

DOT emission strategy
---------------------
:func:`_build_dot_source` orchestrates the following steps:

1. Collect *signal_nets* (non-power, ≥2 pins).
2. Categorise refs as *signal-connected* or *power-only*.
3. Call :func:`~kicad_pcb.tier.assign_tiers` (longest-path) to determine
   the left-to-right rank of each component.
4. Emit one ``{ rank=source/same/sink }`` subgraph per tier via
   :func:`_emit_tier_subgraphs`.
5. Emit directed ``component → net → component`` edges with optional
   ``weight=N`` hints from :func:`_compute_net_weights`.
6. Emit the ``cluster_power`` subgraph.
7. Optionally emit invisible co-location edges for decoupling caps
   (:func:`_emit_decoupling_constraints`).
8. Optionally emit feedback dummy-node constraints for feedback components
   (:func:`_emit_feedback_constraints`).

Relationship to ``tier.assign_tiers`` vs ``_assign_bfs_tiers``
---------------------------------------------------------------
Two tier-assignment algorithms exist:

* :func:`~kicad_pcb.tier.assign_tiers` — **longest-path** from all source
  nodes.  This is the production algorithm used by ``_build_dot_source``.
* :func:`_assign_bfs_tiers` — **BFS from a single connector seed**.  This is
  an earlier, simpler algorithm.  It is currently only reachable through the
  backwards-compatible ``assign_bfs_tiers`` re-export in ``graphviz_layout.py``
  and is exercised by tests; it is **not** called from any production code path
  in this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..block_detection import BlockLayout
    from ..circuit_ir import CircuitIR

from ..block_detection import is_core_like_role, is_input_like_role, is_output_like_role
from ..component_types import is_power_net as _is_power_net
from ..tier import assign_tiers as _assign_tiers
from ._dot_builder_classify import (  # noqa: F401
    _assign_bfs_tiers,
    _find_decoupling_caps,
    _is_capacitor,
    _is_connector,
)
from ._dot_builder_emit import (  # noqa: F401
    _compute_net_weights,
    _emit_block_zone_constraints,
    _emit_connector_rank_constraints,
    _emit_decoupling_constraints,
    _emit_feedback_constraints,
    _emit_halo_constraints,
    _emit_tier_subgraphs,
    _emit_unit_sibling_constraints,
    _partition_power_cluster_refs,
    _partition_power_unit_refs,
    _safe_id,
    _tier_rank_keyword,
)

__all__ = [
    "_assign_bfs_tiers",
    "_build_dot_source",
    "_compute_net_weights",
    "_emit_decoupling_constraints",
    "_find_decoupling_caps",
    "_is_capacitor",
    "_is_connector",
    "_safe_id",
]


def _build_dot_source(  # noqa: PLR0912, PLR0913, PLR0915
    ir: CircuitIR,
    *,
    decoupling_map: dict[str, str] | None = None,
    feedback_refs: set[str] | None = None,
    power_unit_refs: set[str] | None = None,
    unit_sibling_pairs: list[tuple[str, str]] | None = None,
    tiers: dict[str, int] | None = None,
    connector_roles: Mapping[str, str] | None = None,
    halo: dict[str, str] | None = None,
    sds_cols: dict[str, int] | None = None,
    affinity_order: dict[int, list[str]] | None = None,
    block_layout: BlockLayout | None = None,
) -> str:
    """Build a Graphviz DOT source string for *ir* with signal-flow directionality.

    Uses longest-path tier assignment (:func:`tier.assign_tiers`) to determine
    the left-to-right rank of each component, then emits:

    * One ``{ rank=same; }`` (or ``rank=source`` / ``rank=sink`` for the first
      and last tiers) subgraph per BFS tier so Graphviz respects signal-flow
      column ordering.
    * Directed ``component → net_node → component`` edges where the upstream
      component has a lower BFS tier than the downstream component.  This
      gives Graphviz correct rank information so components spread across
      multiple columns instead of collapsing into a single column.
    * A ``cluster_power`` subgraph (``rank=max``) for components that are
      only connected via power nets (GND, VCC, etc.).
    * **Decoupling cap co-location** (optional): if *decoupling_map* is
      supplied, each ``{cap → ic}`` pair gets an invisible zero-weight edge
      (``style=invis, weight=10``) and a ``{rank=same; ic; cap}`` subgraph to
      pull the cap into the same Graphviz column as its associated IC.
    * **Block-based node sizing** (Phase 2.2): if *block_layout* is supplied,
      connector blocks (INPUT/OUTPUT) receive wider node dimensions to improve
      spacing and reduce visual crowding in pin-dense regions.
    """
    lines: list[str] = [
        "digraph sch {",
        "  rankdir=LR;",
        "  nodesep=0.8;",  # Rule 3: increased from 0.5 — more vertical room within tiers
        "  ranksep=2.5;",  # Rule 3: increased from 1.5 — more horizontal room between tiers
        "  ordering=out;",
        "  node [shape=box, width=0.8, height=0.5, fixedsize=true];",
    ]

    refs = sorted(c.ref for c in ir.components)

    signal_nets = [net for net in ir.nets if not _is_power_net(net.name) and len(net.pins) >= 2]

    net_weights = _compute_net_weights(signal_nets)

    signal_refs: set[str] = set()
    for net in signal_nets:
        signal_refs.update(p.ref for p in net.pins)
    power_only_refs = [r for r in refs if r not in signal_refs]

    if power_unit_refs:
        power_only_refs, signal_refs = _partition_power_unit_refs(
            refs, signal_refs, power_only_refs, power_unit_refs
        )

    power_only_refs, retained_power_support_refs = _partition_power_cluster_refs(
        power_only_refs,
        block_layout,
    )

    _tiers = tiers if tiers is not None else _assign_tiers(ir)

    _col_source: dict[str, int] = sds_cols if sds_cols is not None else _tiers
    tier_groups: dict[int, list[str]] = {}
    for ref in refs:
        if ref in power_only_refs:
            continue
        tier_groups.setdefault(_col_source.get(ref, 0), []).append(ref)
    for ref in retained_power_support_refs:
        tier_groups.setdefault(_col_source.get(ref, 0), []).append(ref)

    for ref in refs:
        safe = _safe_id(ref)
        if block_layout and ref in block_layout.assignments:
            role = block_layout.assignments[ref].role
            if is_input_like_role(role) or is_output_like_role(role):
                width, height = 1.0, 0.6
            elif is_core_like_role(role):
                width, height = 0.9, 0.55
            else:
                width, height = 0.8, 0.5
        else:
            width, height = 0.8, 0.5
        lines.append(f'  {safe} [label="{ref}", shape=box, width={width}, height={height}];')

    _emit_tier_subgraphs(lines, tier_groups, affinity_order=affinity_order)
    if unit_sibling_pairs:
        _emit_unit_sibling_constraints(lines, unit_sibling_pairs, _tiers, col_source=_col_source)

    if connector_roles:
        _emit_connector_rank_constraints(lines, connector_roles)
    if block_layout:
        _emit_block_zone_constraints(lines, block_layout)

    for net in signal_nets:
        pin_refs = [p.ref for p in net.pins]
        net_id = "net_" + _safe_id(net.name)
        lines.append(
            f'  {net_id} [label="{net.name}", shape=ellipse, width=0.6, '
            "height=0.4, fixedsize=false];"
        )
        sorted_pins = sorted(pin_refs, key=lambda r: (_col_source.get(r, 0), r))
        upstream = sorted_pins[0]
        w = net_weights.get(net.name, 1)
        weight_attr = f" [weight={w}]" if w > 1 else ""
        lines.append(f"  {_safe_id(upstream)} -> {net_id}{weight_attr};")
        for downstream in sorted_pins[1:]:
            is_fb = feedback_refs is not None and downstream in feedback_refs
            extra = " [constraint=false]" if is_fb else (weight_attr if weight_attr else "")
            lines.append(f"  {net_id} -> {_safe_id(downstream)}{extra};")

    if power_only_refs:
        lines.append("  subgraph cluster_power {")
        lines.append('    label="power";')
        lines.append("    rank=max;")
        for ref in power_only_refs:
            lines.append(f"    {_safe_id(ref)};")
        lines.append("  }")

    if decoupling_map:
        _emit_decoupling_constraints(lines, decoupling_map, tiers=tiers)

    if feedback_refs:
        _emit_feedback_constraints(lines, feedback_refs)

    if halo:
        _emit_halo_constraints(lines, halo)

    lines.append("}")
    return "\n".join(lines)

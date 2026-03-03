"""Graphviz-based schematic layout engine for kicad_pcb.

Uses ``dot -Tplain`` to lay out a *bipartite* graph of circuit components
and nets so that signals flow left → right (``rankdir=LR``).

Graph model (4.3 — bipartite)
------------------------------
* **Component nodes** — one node per reference designator (e.g. ``R1``).
* **Net nodes** — one node per net (id ``net_<name>``).
* **Edges** — connect each component to every net it participates in.
* **Power nets** (GND, VCC, VDD, V+, V- and similar short all-caps names)
  are *excluded* from the bipartite graph to avoid creating highly-connected
  hubs.  Components connected only via power nets are placed in a dedicated
  right-hand cluster.

Coordinate mapping
------------------
``dot -Tplain`` reports positions in Graphviz "point" units (72 pt/inch),
with the origin at the lower-left and y increasing upward.  We map to KiCad
mm coordinates with y increasing downward:

    x_mm = ORIGIN_X + gv_x × SCALE_MM_PER_GV
    y_mm = ORIGIN_Y + (max_gv_y − gv_y) × SCALE_MM_PER_GV

Public API
----------
:func:`find_dot_binary`     — locate the ``dot`` executable.
:class:`GraphvizLayoutEngine` — :class:`~kicad_pcb.layout_engine.LayoutEngine`
                                implementation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from collections import deque
from itertools import combinations
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .circuit_ir import CircuitIR

from .component_types import CAPACITOR_PREFIXES as _CAPACITOR_PREFIXES_CT
from .component_types import CONNECTOR_PREFIXES as _CONNECTOR_PREFIXES_CT
from .tier import assign_tiers as _assign_tiers

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ORIGIN_X: float = 30.48  # mm — left margin on an A4 page
ORIGIN_Y: float = 50.80  # mm — top margin on an A4 page

# Usable schematic area on an A4 sheet (page is 297 × 210 mm).
# Leave a 10 mm gutter on the right and bottom edges.
PAGE_MAX_X: float = 287.0  # mm (297 - 10)
PAGE_MAX_Y: float = 200.0  # mm (210 - 10)

# Scale factor: mm per one "graph unit" in dot -Tplain output.
# dot -Tplain reports node centre coordinates in inches (not points).
# _LAY_SYMBOL_HALF_SIZE_MM = 5.08 mm, so symbols need ≥ 10.16 mm
# centre-to-centre to avoid LAY003.  With nodesep=0.5 + node height=0.5
# the minimum same-rank separation is 1.0 inch, so:
#   SCALE ≥ 10.16 mm / 1.0 in  →  use 20 mm/in for comfortable margins.
SCALE_MM_PER_GV: float = 20.0

# Vertical spacing between a decoupling capacitor and its associated IC.
# One KiCad symbol row = 300 mil = 7.62 mm (KiCad default body height).
GRID_ROW_MM: float = 7.62

# Nets whose names match these patterns are treated as power rails and
# excluded from the main bipartite graph to avoid hub explosion.
_POWER_NET_PATTERN = re.compile(
    r"^(?:GND|AGND|DGND|PGND|VCC|VDD|VSS|V\+|V-|VBAT|VREF|"
    r"[+\-]?(?:\d+V\d*|\d*V\d+)|PWR_FLAG)$",
    re.IGNORECASE,
)

# Maximum number of subprocess attempts (retry on transient failures).
_MAX_ATTEMPTS = 2


# ---------------------------------------------------------------------------
# Layout cache
# ---------------------------------------------------------------------------

# Bump when the cache JSON schema changes to invalidate all persisted caches.
_CACHE_FORMAT_VERSION = 1


def _layout_cache_key(dot_source: str) -> str:
    """Return a stable SHA-256 hex digest for *dot_source*.

    The digest is used as the cache lookup key — any change in circuit topology
    (new component, different net) produces a different key and therefore a
    guaranteed cache miss.
    """
    return hashlib.sha256(dot_source.encode()).hexdigest()


def _load_layout_cache(
    cache_path: Path,
    cache_key: str,
) -> dict[str, tuple[float, float, float | None]] | None:
    """Return cached symbol positions if *cache_path* exists and *cache_key* matches.

    Returns ``None`` on any error (missing file, JSON parse failure, key or
    version mismatch) so the caller always falls through to a fresh layout
    computation.
    """
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if data.get("version") != _CACHE_FORMAT_VERSION or data.get("key") != cache_key:
            return None
        raw: dict[str, list[float | None]] = data.get("positions", {})
        return {
            ref: (float(v[0]), float(v[1]), None)  # type: ignore[arg-type]
            for ref, v in raw.items()
            if isinstance(v, list) and len(v) >= 2
        }
    except Exception:  # noqa: BLE001
        return None


def _save_layout_cache(
    cache_path: Path,
    cache_key: str,
    positions: dict[str, tuple[float, float, float | None]],
) -> None:
    """Persist *positions* to *cache_path*.  Silently ignores all write errors.

    The cache file is a JSON document::

        {
            "version": 1,
            "key": "<sha256-hex-of-dot-source>",
            "positions": {"R1": [x, y, null], ...}
        }
    """
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _CACHE_FORMAT_VERSION,
            "key": cache_key,
            "positions": {ref: [x, y, rot] for ref, (x, y, rot) in positions.items()},
        }
        cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        _log.debug("Failed to write layout cache to %s", cache_path)


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Bundled binary slot
# ---------------------------------------------------------------------------

# When a ``dot`` binary is shipped alongside the package it should be placed
# at ``<package_root>/bin/dot`` (or ``bin/dot.exe`` on Windows).  The slot is
# checked *before* the environment variable and PATH lookup so the bundled
# binary is always preferred when present.
#
# Currently no binary is bundled; the constant resolves to a path that will
# not exist, so the check always falls through to the env-var / PATH steps.
_BUNDLED_DOT_PATH: Path = Path(__file__).parent / "bin" / "dot"


def find_dot_binary() -> str | None:
    """Locate the ``dot`` binary used for Graphviz layout.

    Search order:

    1. **Bundled binary** — ``<package>/bin/dot`` if present and executable.
    2. :envvar:`GRAPHVIZ_DOT` environment variable.
    3. System :data:`PATH` (``shutil.which``).

    Returns the resolved path string or ``None`` if not found.

    Use :func:`find_dot_source` to get both the path and discovery source.
    """
    # 1. Bundled binary (preferred when present).
    if _BUNDLED_DOT_PATH.is_file() and os.access(_BUNDLED_DOT_PATH, os.X_OK):
        return str(_BUNDLED_DOT_PATH)
    # 2. GRAPHVIZ_DOT environment variable.
    env_val = os.environ.get("GRAPHVIZ_DOT", "").strip()
    if env_val and Path(env_val).is_file() and os.access(env_val, os.X_OK):
        return env_val
    # 3. System PATH.
    return shutil.which("dot")


def find_dot_source() -> tuple[str, str] | None:
    """Return ``(path, source)`` for the resolved ``dot`` binary.

    *source* is one of ``"bundled"``, ``"GRAPHVIZ_DOT"``, or ``"PATH"``.
    Returns ``None`` if ``dot`` cannot be found.
    """
    if _BUNDLED_DOT_PATH.is_file() and os.access(_BUNDLED_DOT_PATH, os.X_OK):
        return str(_BUNDLED_DOT_PATH), "bundled"
    env_val = os.environ.get("GRAPHVIZ_DOT", "").strip()
    if env_val and Path(env_val).is_file() and os.access(env_val, os.X_OK):
        return env_val, "GRAPHVIZ_DOT"
    path = shutil.which("dot")
    if path:
        return path, "PATH"
    return None


# ---------------------------------------------------------------------------
# Component classification (used by DOT builder and BFS tier assignment)
# ---------------------------------------------------------------------------

# Aliases to component_types constants — kept here for local helpers.
_CONNECTOR_PREFIXES: tuple[str, ...] = _CONNECTOR_PREFIXES_CT
_CAPACITOR_PREFIXES: tuple[str, ...] = _CAPACITOR_PREFIXES_CT


def _is_connector(ref: str) -> bool:
    """Return True if *ref* is a connector designator (``J*``, ``P*``, etc.)."""
    r = ref.upper()
    return any(r.startswith(p) for p in _CONNECTOR_PREFIXES)


def _is_capacitor(ref: str) -> bool:
    """Return True if *ref* is a capacitor designator (``C*``)."""
    r = ref.upper()
    return any(r.startswith(p) for p in _CAPACITOR_PREFIXES)


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
    # Build: ref → list of net names for that ref.
    ref_to_nets: dict[str, list[str]] = {}
    # Build: net_name → list of refs participating in that net.
    net_to_refs: dict[str, list[str]] = {}

    for net in ir.nets:
        for pin in net.pins:
            ref_to_nets.setdefault(pin.ref, []).append(net.name)
            net_to_refs.setdefault(net.name, []).append(pin.ref)

    result: dict[str, str] = {}
    for comp in ir.components:
        if not _is_capacitor(comp.ref):
            continue
        nets_for_cap = ref_to_nets.get(comp.ref, [])
        signal_nets_for_cap = [n for n in nets_for_cap if not _is_power_net(n)]
        power_nets_for_cap = [n for n in nets_for_cap if _is_power_net(n)]
        if len(signal_nets_for_cap) != 1 or not power_nets_for_cap:
            continue
        # Exactly one signal net — find the IC on that shared net.
        signal_net = signal_nets_for_cap[0]
        for neighbor_ref in net_to_refs.get(signal_net, []):
            if neighbor_ref == comp.ref:
                continue
            if _is_connector(neighbor_ref) or _is_capacitor(neighbor_ref):
                continue
            # First non-connector, non-capacitor neighbor → treated as the IC.
            result[comp.ref] = neighbor_ref
            break

    return result


# ---------------------------------------------------------------------------
# BFS tier assignment
# ---------------------------------------------------------------------------


def _assign_bfs_tiers(
    refs: list[str],
    signal_nets: list,  # list[NetIR] — avoid circular import at runtime
) -> dict[str, int]:
    """Return ``{ref: tier}`` for all *refs* using BFS from connector seeds.

    Connector refs (``J*``, ``P*``, ``CON*``, etc.) are used as BFS seeds
    processed in sorted (alphabetical) order.  The alphabetically-first
    connector is the most likely signal source; downstream connectors receive
    higher tier values naturally via BFS traversal.

    Non-connector components receive intermediate tiers.  Power-only or
    isolated components default to tier ``0``.
    """
    # Build undirected adjacency from signal nets.
    ref_set = set(refs)
    adj: dict[str, list[str]] = {r: [] for r in refs}
    for net in signal_nets:
        pin_refs = [p.ref for p in net.pins if p.ref in ref_set]
        for i, r1 in enumerate(pin_refs):
            for r2 in pin_refs[i + 1 :]:
                adj[r1].append(r2)
                adj[r2].append(r1)

    # Seeds: only the alphabetically-first connector.
    #
    # Using all connectors as seeds simultaneously causes both input AND
    # output connectors to start at tier 0, which collapses the entire
    # circuit into one column.  By seeding only the first connector we let
    # BFS propagate naturally: any output connector at the far end of the
    # signal chain reaches a high tier via BFS, while parallel input
    # connectors that are NOT reachable from the seed fall through to the
    # "unreached → tier 0" default at the end of the function — correct
    # because they are at the same input stage as the primary seed.
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

    # Unreached refs (isolated or power-only) → tier 0.
    for r in refs:
        if r not in tier:
            tier[r] = 0

    return tier


# ---------------------------------------------------------------------------
# DOT graph builder
# ---------------------------------------------------------------------------


def _is_power_net(name: str) -> bool:
    return bool(_POWER_NET_PATTERN.match(name))


def _safe_id(name: str) -> str:
    """Return a DOT-safe identifier (replace all non-alphanumeric chars with '_')."""
    return re.sub(r"[^A-Za-z0-9]", "_", name)


def _tier_rank_keyword(tier_index: int, n_tiers: int) -> str:
    """Return the Graphviz ``rank=`` keyword for *tier_index* of *n_tiers* tiers.

    * Single-tier graphs → ``same`` (no forced ordering).
    * First tier (index 0) → ``source`` (signal sources at the left edge).
    * Last tier → ``sink`` (signal sinks at the right edge).
    * All intermediate tiers → ``same`` (column grouping without edge constraint).
    """
    if n_tiers == 1:
        return "same"
    if tier_index == 0:
        return "source"
    if tier_index == n_tiers - 1:
        return "sink"
    return "same"


def _compute_net_weights(signal_nets: list) -> dict[str, int]:
    """Return ``{net_name: weight}`` for each signal net.

    Weight is ``5`` when at least one pair of components sharing this net also
    shares **2 or more signal nets** in total.  This indicates a tight
    functional coupling (e.g. two pins of an op-amp feedback network, or
    series/shunt resistor pairs).  Higher weights tell Graphviz to prefer
    short, uncrossed connections between those component pairs.

    All other nets get weight ``1`` (Graphviz default).
    """
    # Map (ref_a, ref_b) → number of signal nets they share.
    pair_net_count: dict[tuple[str, str], int] = {}
    for net in signal_nets:
        refs = sorted({p.ref for p in net.pins})
        for a, b in combinations(refs, 2):
            key = (a, b)
            pair_net_count[key] = pair_net_count.get(key, 0) + 1

    result: dict[str, int] = {}
    for net in signal_nets:
        refs = sorted({p.ref for p in net.pins})
        max_shared = max(
            (pair_net_count.get((a, b), 0) for a, b in combinations(refs, 2)),
            default=0,
        )
        result[net.name] = 5 if max_shared >= 2 else 1
    return result


def _emit_decoupling_constraints(
    lines: list[str],
    decoupling_map: dict[str, str],
) -> None:
    """Append invisible-edge + rank=same lines to *lines* for *decoupling_map*.

    Emitted for each ``{cap_ref: ic_ref}`` pair:

    * An invisible directed edge ``cap → ic [style=invis, weight=10]`` so that
      Graphviz pulls the cap toward the IC without affecting the visible graph.
    * A ``{rank=same; ic; cap}`` subgraph to place both in the same column.
    """
    for cap_ref, ic_ref in sorted(decoupling_map.items()):
        cap_id = _safe_id(cap_ref)
        ic_id = _safe_id(ic_ref)
        lines.append(f"  {cap_id} -> {ic_id} [style=invis, weight=10];")
    for cap_ref, ic_ref in sorted(decoupling_map.items()):
        lines.append("  {")
        lines.append("    rank=same;")
        lines.append(f"    {_safe_id(ic_ref)};")
        lines.append(f"    {_safe_id(cap_ref)};")
        lines.append("  }")


def _build_dot_source(
    ir: CircuitIR,
    *,
    decoupling_map: dict[str, str] | None = None,
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
    """
    lines: list[str] = [
        "digraph sch {",
        "  rankdir=LR;",
        "  nodesep=0.5;",
        "  ranksep=1.5;",
        "  ordering=out;",
        "  node [shape=box, width=0.8, height=0.5, fixedsize=true];",
    ]

    refs = sorted(c.ref for c in ir.components)

    # Collect signal nets (non-power, ≥2 pins).
    signal_nets = [net for net in ir.nets if not _is_power_net(net.name) and len(net.pins) >= 2]

    # Compute per-net edge weights: boost to 5 for tightly-coupled pairs.
    net_weights = _compute_net_weights(signal_nets)

    # Categorise: power-only refs have no signal net connections.
    signal_refs: set[str] = set()
    for net in signal_nets:
        signal_refs.update(p.ref for p in net.pins)
    power_only_refs = [r for r in refs if r not in signal_refs]

    # Longest-path tier assignment — determines left-to-right rank for each component.
    tiers = _assign_tiers(ir)

    # Group signal-connected refs by tier.
    tier_groups: dict[int, list[str]] = {}
    for ref in refs:
        if ref in power_only_refs:
            continue
        tier_groups.setdefault(tiers.get(ref, 0), []).append(ref)

    # Emit component nodes.
    for ref in refs:
        safe = _safe_id(ref)
        lines.append(f'  {safe} [label="{ref}", shape=box];')

    # Emit rank subgraphs: rank=source for tier 0, rank=sink for last tier,
    # rank=same for all intermediate tiers.
    sorted_tier_vals = sorted(tier_groups)
    n_tiers = len(sorted_tier_vals)
    for i, tier_val in enumerate(sorted_tier_vals):
        members = tier_groups[tier_val]
        rank_kw = _tier_rank_keyword(i, n_tiers)
        lines.append("  {")
        lines.append(f"    rank={rank_kw};")
        for ref in sorted(members):
            lines.append(f"    {_safe_id(ref)};")
        lines.append("  }")

    # Emit net nodes + directional edges.
    # For each signal net, sort pins by ascending BFS tier so edges flow
    # left → right through the net hub node.
    for net in signal_nets:
        pin_refs = [p.ref for p in net.pins]
        net_id = "net_" + _safe_id(net.name)
        lines.append(f'  {net_id} [label="{net.name}", shape=ellipse, width=0.6, height=0.4];')
        # Sort by (tier, ref) for a stable, deterministic ordering.
        sorted_pins = sorted(pin_refs, key=lambda r: (tiers.get(r, 0), r))
        upstream = sorted_pins[0]
        w = net_weights.get(net.name, 1)
        weight_attr = f" [weight={w}]" if w > 1 else ""
        lines.append(f"  {_safe_id(upstream)} -> {net_id}{weight_attr};")
        for downstream in sorted_pins[1:]:
            lines.append(f"  {net_id} -> {_safe_id(downstream)}{weight_attr};")

    # Power-only refs in a subgraph at the right so they don't disrupt flow.
    if power_only_refs:
        lines.append("  subgraph cluster_power {")
        lines.append('    label="power";')
        lines.append("    rank=max;")
        for ref in power_only_refs:
            lines.append(f"    {_safe_id(ref)};")
        lines.append("  }")

    # Decoupling cap co-location: invisible edges + rank=same pull each
    # bypass cap into the same column as its associated IC.
    if decoupling_map:
        _emit_decoupling_constraints(lines, decoupling_map)

    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# dot -Tplain parser
# ---------------------------------------------------------------------------


def _parse_plain_positions(plain_output: str) -> dict[str, tuple[float, float]]:
    """Parse ``dot -Tplain`` output and return ``{node_name: (gv_x, gv_y)}``.

    The plain format line structure::

        node <name> <x> <y> <width> <height> <label> …

    We collect only ``node`` lines and only return component nodes (i.e. we
    skip lines whose name starts with ``net_``).
    """
    positions: dict[str, tuple[float, float]] = {}
    for line in plain_output.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] != "node":
            continue
        name = parts[1]
        if name.startswith("net_"):
            continue
        try:
            gv_x = float(parts[2])
            gv_y = float(parts[3])
        except ValueError:
            continue
        positions[name] = (gv_x, gv_y)
    return positions


def _post_snap_decoupling_caps(
    positions: dict[str, tuple[float, float, float | None]],
    decoupling_map: dict[str, str],
) -> dict[str, tuple[float, float, float | None]]:
    """Snap each decoupling cap to sit directly above its associated IC.

    Sets the cap's x-coordinate to match the IC's x-coordinate and offsets
    the cap's y-coordinate by ``-GRID_ROW_MM`` (one KiCad symbol row above
    the IC, given that y increases downward in KiCad coordinates).

    Caps or ICs not present in *positions* are silently skipped (e.g. if the
    cap was not returned by Graphviz because it was isolated).
    """
    result = dict(positions)
    for cap_ref, ic_ref in decoupling_map.items():
        if cap_ref not in result or ic_ref not in result:
            continue
        ic_x, ic_y, _ = result[ic_ref]
        result[cap_ref] = (round(ic_x, 2), round(ic_y - GRID_ROW_MM, 2), None)
    return result


def _gv_to_kicad(
    gv_positions: dict[str, tuple[float, float]],
    *,
    origin_x: float = ORIGIN_X,
    origin_y: float = ORIGIN_Y,
    scale: float = SCALE_MM_PER_GV,
) -> dict[str, tuple[float, float, float | None]]:
    """Map Graphviz node positions to KiCad mm coordinates.

    Graphviz origin is bottom-left; KiCad origin is top-left.  We invert
    the y axis so that higher-ranked nodes appear at the top of the schematic.

    If the scaled positions would exceed the usable A4 area
    (:data:`PAGE_MAX_X` × :data:`PAGE_MAX_Y`) the entire layout is
    proportionally shrunk (preserving relative distances) until it fits.
    """
    if not gv_positions:
        return {}
    max_gv_y = max(y for _, y in gv_positions.values())
    result: dict[str, tuple[float, float, float | None]] = {}
    for node_name, (gv_x, gv_y) in gv_positions.items():
        x_mm = origin_x + gv_x * scale
        y_mm = origin_y + (max_gv_y - gv_y) * scale
        # Snap to 0.01 mm for readability
        result[node_name] = (round(x_mm, 2), round(y_mm, 2), None)

    # --- Page-fit normalisation -------------------------------------------
    # If any position lies outside the usable area, proportionally scale the
    # whole layout down so that every position is within [origin_x..PAGE_MAX_X]
    # × [origin_y..PAGE_MAX_Y].  This keeps relative topology intact.
    max_x = max(pos[0] for pos in result.values())
    max_y = max(pos[1] for pos in result.values())
    avail_x = PAGE_MAX_X - origin_x  # available width after margins
    avail_y = PAGE_MAX_Y - origin_y  # available height after margins
    span_x = max_x - origin_x  # current width of laid-out content
    span_y = max_y - origin_y  # current height of laid-out content
    shrink = 1.0
    if span_x > 0 and span_x > avail_x:
        shrink = min(shrink, avail_x / span_x)
    if span_y > 0 and span_y > avail_y:
        shrink = min(shrink, avail_y / span_y)
    if shrink < 1.0:
        result = {
            name: (
                round(origin_x + (pos[0] - origin_x) * shrink, 2),
                round(origin_y + (pos[1] - origin_y) * shrink, 2),
                None,
            )
            for name, pos in result.items()
        }

    return result


# ---------------------------------------------------------------------------
# GraphvizLayoutEngine
# ---------------------------------------------------------------------------


class GraphvizLayoutEngine:
    """Calls ``dot -Tplain`` to produce left-to-right schematic layouts.

    Parameters
    ----------
    dot_path:
        Absolute (or PATH-resolved) path to the ``dot`` executable.
    scale:
        Scale factor (mm per Graphviz point unit). Default: ``SCALE_MM_PER_GV``.
    timeout:
        Subprocess timeout in seconds. Default: 10.
    """

    def __init__(
        self,
        *,
        dot_path: str,
        scale: float = SCALE_MM_PER_GV,
        timeout: float = 10.0,
        seed: int = 7,
        cache_path: Path | None = None,
    ) -> None:
        self._dot = dot_path
        self._scale = scale
        self._timeout = timeout
        self._seed = seed
        self._cache_path = cache_path

    # ----------------------------------------------------------------
    # LayoutEngine Protocol
    # ----------------------------------------------------------------

    def compute_symbol_positions(
        self, ir: CircuitIR
    ) -> dict[str, tuple[float, float, float | None]]:
        """Run ``dot`` on *ir* and return ``{ref: (x_mm, y_mm, None)}``.

        **Cache:** If *cache_path* was supplied and a cached result exists for
        the current circuit topology (keyed by sha256 of the DOT source), the
        cached positions are returned immediately without invoking ``dot``.
        After a fresh run the result is written back to the cache.

        **Determinism:** ``dot`` is invoked with ``-Gstart=<seed>`` (default
        7) so that repeated runs on identical input produce stable output.

        Raises :class:`RuntimeError` if ``dot`` fails or returns no positions.
        """
        refs = sorted(c.ref for c in ir.components)
        if not refs:
            return {}

        # Detect decoupling caps before building DOT source so the same map
        # can be used both for invisible-edge constraints and post-layout snap.
        decoupling_map = _find_decoupling_caps(ir)

        # Build DOT source up-front so we can derive the cache key.
        dot_source = _build_dot_source(ir, decoupling_map=decoupling_map)
        cache_key = _layout_cache_key(dot_source)

        # --- Cache hit: return immediately without invoking dot. ---
        if self._cache_path is not None:
            cached = _load_layout_cache(self._cache_path, cache_key)
            if cached is not None:
                _log.debug("Layout cache hit (key %s…); skipping dot.", cache_key[:8])
                return cached

        try:
            positions = self._run_dot(dot_source)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Graphviz 'dot' failed: {exc}.  Command: {self._dot} -Tplain -Gstart={self._seed}"
            ) from exc

        if not positions:
            raise RuntimeError(
                "Graphviz 'dot' returned no node positions.  "
                f"Command: {self._dot} -Tplain -Gstart={self._seed}"
            )

        # Verify all refs are covered.
        missing = [r for r in refs if _safe_id(r) not in positions]
        if missing:
            raise RuntimeError(
                f"Graphviz 'dot' did not return positions for refs: {missing!r}.  "
                "Check the DOT graph for isolated nodes or unsupported syntax, "
                "or file a bug with the circuit IR."
            )

        # Re-key from safe_id → original ref
        safe_to_ref = {_safe_id(r): r for r in refs}
        result: dict[str, tuple[float, float, float | None]] = {
            safe_to_ref[sid]: pos for sid, pos in positions.items() if sid in safe_to_ref
        }

        # Post-layout: snap decoupling caps to sit directly above their IC.
        if decoupling_map:
            result = _post_snap_decoupling_caps(result, decoupling_map)

        # --- Cache write: persist for next run. ---
        if self._cache_path is not None:
            _save_layout_cache(self._cache_path, cache_key, result)
            _log.debug("Layout cache written (key %s…).", cache_key[:8])

        return result

    # ----------------------------------------------------------------
    # Private helpers
    # ----------------------------------------------------------------

    def _run_dot(self, dot_source: str) -> dict[str, tuple[float, float, float | None]]:
        """Invoke ``dot`` on *dot_source* and return parsed KiCad positions.

        Passes ``-Gstart=<seed>`` to make layout deterministic across repeated
        runs on the same input.
        """
        _log.debug("DOT source:\n%s", dot_source)

        cmd: list[str] = [self._dot, "-Tplain", f"-Gstart={self._seed}"]
        for attempt in range(_MAX_ATTEMPTS):
            try:
                result = subprocess.run(  # noqa: S603
                    cmd,
                    input=dot_source,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                    check=False,
                )
            except (FileNotFoundError, PermissionError) as exc:
                raise RuntimeError(f"dot binary not executable: {exc}") from exc
            except subprocess.TimeoutExpired as exc:
                if attempt < _MAX_ATTEMPTS - 1:
                    _log.debug("dot timed out (attempt %d), retrying", attempt + 1)
                    continue
                raise RuntimeError(f"dot timed out after {self._timeout}s") from exc

            if result.returncode != 0:
                raise RuntimeError(
                    f"dot exited with code {result.returncode}:\n{result.stderr[:400]}"
                )
            break

        gv_positions = _parse_plain_positions(result.stdout)
        return _gv_to_kicad(
            gv_positions,
            origin_x=ORIGIN_X,
            origin_y=ORIGIN_Y,
            scale=self._scale,
        )

    @property
    def dot_path(self) -> str:
        return self._dot

    def __repr__(self) -> str:
        return f"GraphvizLayoutEngine(dot_path={self._dot!r})"


# ---------------------------------------------------------------------------
# Helpers exported for tests
# ---------------------------------------------------------------------------

__all__ = [
    "assign_bfs_tiers",
    "build_dot_source",
    "compute_net_weights",
    "emit_decoupling_constraints",
    "find_decoupling_caps",
    "find_dot_binary",
    "find_dot_source",
    "GraphvizLayoutEngine",
    "GRID_ROW_MM",
    "is_capacitor",
    "is_connector",
    "layout_cache_key",
    "load_layout_cache",
    "parse_plain_positions",
    "post_snap_decoupling_caps",
    "save_layout_cache",
]

# Expose internals for unit tests under public names
assign_bfs_tiers = _assign_bfs_tiers
build_dot_source = _build_dot_source
compute_net_weights = _compute_net_weights
emit_decoupling_constraints = _emit_decoupling_constraints
find_decoupling_caps = _find_decoupling_caps
is_capacitor = _is_capacitor
is_connector = _is_connector
layout_cache_key = _layout_cache_key
load_layout_cache = _load_layout_cache
parse_plain_positions = _parse_plain_positions
post_snap_decoupling_caps = _post_snap_decoupling_caps
save_layout_cache = _save_layout_cache


def _snap(v: float, grid: float = 0.254) -> float:
    """Snap *v* to the nearest *grid* multiple (KiCad 10 mil snapping)."""
    return round(round(v / grid) * grid, 4)


def snap_positions(
    positions: dict[str, tuple[float, float, float | None]],
    *,
    grid: float = 1.27,
) -> dict[str, tuple[float, float, float | None]]:
    """Snap all (x, y) in *positions* to the nearest *grid* increment (mm).

    KiCad's snap grid is 50 mil (1.27 mm) by default.  Snapping avoids
    off-grid placements that make manual editing awkward.
    """
    return {
        ref: (
            round(round(x / grid) * grid, 4),
            round(round(y / grid) * grid, 4),
            rot,
        )
        for ref, (x, y, rot) in positions.items()
    }

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

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .circuit_ir import CircuitIR

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ORIGIN_X: float = 30.48  # mm — left margin on an A4 page
ORIGIN_Y: float = 50.80  # mm — top margin on an A4 page

# Scale factor: how many mm one Graphviz "point" unit represents.
# Graphviz internal units are points (72 pt/inch). A typical value of ~3.5
# gives comfortable spacing on an A4 sheet.
SCALE_MM_PER_GV: float = 3.5

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
# Binary discovery
# ---------------------------------------------------------------------------


def find_dot_binary() -> str | None:
    """Locate the ``dot`` binary used for Graphviz layout.

    Search order:

    1. :envvar:`GRAPHVIZ_DOT` environment variable.
    2. System :data:`PATH` (``shutil.which``).

    Returns the resolved path string or ``None`` if not found.
    """
    env_val = os.environ.get("GRAPHVIZ_DOT", "").strip()
    if env_val and Path(env_val).is_file() and os.access(env_val, os.X_OK):
        return env_val
    return shutil.which("dot")


# ---------------------------------------------------------------------------
# DOT graph builder (bipartite model)
# ---------------------------------------------------------------------------


def _is_power_net(name: str) -> bool:
    return bool(_POWER_NET_PATTERN.match(name))


def _safe_id(name: str) -> str:
    """Return a DOT-safe identifier (replace all non-alphanumeric chars with '_')."""
    return re.sub(r"[^A-Za-z0-9]", "_", name)


def _build_dot_source(ir: CircuitIR) -> str:
    """Build a Graphviz DOT source string for *ir* using a bipartite model.

    Component nodes are shaped as ``box``.  Net nodes are shaped as
    ``ellipse``.  Power nets are excluded; components only connected via
    power nets are added to a ``power_rails`` cluster at the right edge.
    """
    lines: list[str] = [
        "digraph sch {",
        "  rankdir=LR;",
        "  nodesep=0.5;",
        "  ranksep=1.0;",
        "  node [shape=box, width=0.8, height=0.5, fixedsize=true];",
    ]

    refs = sorted(c.ref for c in ir.components)

    # Build net membership: net_name -> list of refs
    net_members: dict[str, list[str]] = {}
    for net in ir.nets:
        net_members[net.name] = [p.ref for p in net.pins]

    # Categorise each ref: signal-connected (has ≥1 non-power net) or power-only
    signal_refs: set[str] = set()
    for name, members in net_members.items():
        if not _is_power_net(name):
            signal_refs.update(members)

    power_only_refs = [r for r in refs if r not in signal_refs]

    # Emit component nodes
    for ref in refs:
        safe = _safe_id(ref)
        lines.append(f'  {safe} [label="{ref}", shape=box];')

    # Emit net nodes + edges for signal nets
    for net in ir.nets:
        if _is_power_net(net.name):
            continue
        if len(net.pins) < 2:
            continue
        net_id = "net_" + _safe_id(net.name)
        lines.append(f'  {net_id} [label="{net.name}", shape=ellipse, width=0.6, height=0.4];')
        for pin in net.pins:
            ref_id = _safe_id(pin.ref)
            lines.append(f"  {ref_id} -> {net_id};")

    # Power-only refs in a subgraph at the right so they don't disrupt flow
    if power_only_refs:
        lines.append("  subgraph cluster_power {")
        lines.append('    label="power";')
        lines.append("    rank=max;")
        for ref in power_only_refs:
            lines.append(f"    {_safe_id(ref)};")
        lines.append("  }")

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


def _gv_to_kicad(
    gv_positions: dict[str, tuple[float, float]],
    *,
    origin_x: float = ORIGIN_X,
    origin_y: float = ORIGIN_Y,
    scale: float = SCALE_MM_PER_GV,
) -> dict[str, tuple[float, float, None]]:
    """Map Graphviz node positions to KiCad mm coordinates.

    Graphviz origin is bottom-left; KiCad origin is top-left.  We invert
    the y axis so that higher-ranked nodes appear at the top of the schematic.
    """
    if not gv_positions:
        return {}
    max_gv_y = max(y for _, y in gv_positions.values())
    result: dict[str, tuple[float, float, None]] = {}
    for node_name, (gv_x, gv_y) in gv_positions.items():
        x_mm = origin_x + gv_x * scale
        y_mm = origin_y + (max_gv_y - gv_y) * scale
        # Snap to 0.01 mm for readability
        result[node_name] = (round(x_mm, 2), round(y_mm, 2), None)
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
    ) -> None:
        self._dot = dot_path
        self._scale = scale
        self._timeout = timeout

    # ----------------------------------------------------------------
    # LayoutEngine Protocol
    # ----------------------------------------------------------------

    def compute_symbol_positions(
        self, ir: CircuitIR
    ) -> dict[str, tuple[float, float, float | None]]:
        """Run ``dot`` on *ir* and return ``{ref: (x_mm, y_mm, None)}``.

        Falls back to the heuristic engine if ``dot`` fails (e.g. binary
        missing, subprocess error, no nodes returned) so that the pipeline
        always produces *some* layout.
        """
        refs = sorted(c.ref for c in ir.components)
        if not refs:
            return {}

        try:
            positions = self._run_dot(ir)
        except Exception as exc:  # noqa: BLE001
            _log.warning("GraphvizLayoutEngine failed (%s); falling back to heuristic layout.", exc)
            positions = {}

        if not positions:
            _log.debug("Graphviz returned no node positions; using heuristic fallback.")
            from .layout import HeuristicLayoutEngine  # noqa: PLC0415

            return HeuristicLayoutEngine().compute_symbol_positions(ir)

        # Verify all refs are covered; fill any gaps via heuristic.
        missing = [r for r in refs if _safe_id(r) not in positions]
        if missing:
            from .layout import compute_signal_flow_layout  # noqa: PLC0415

            heuristic = compute_signal_flow_layout(ir)
            for ref in missing:
                x, y = heuristic.get(ref, (ORIGIN_X, ORIGIN_Y))
                positions[_safe_id(ref)] = (x, y, None)  # type: ignore[assignment]

        # Re-key from safe_id → original ref
        safe_to_ref = {_safe_id(r): r for r in refs}
        return {safe_to_ref[sid]: pos for sid, pos in positions.items() if sid in safe_to_ref}

    # ----------------------------------------------------------------
    # Private helpers
    # ----------------------------------------------------------------

    def _run_dot(self, ir: CircuitIR) -> dict[str, tuple[float, float, None]]:
        """Build DOT source, run dot, and return parsed positions."""
        dot_source = _build_dot_source(ir)
        _log.debug("DOT source:\n%s", dot_source)

        for attempt in range(_MAX_ATTEMPTS):
            try:
                result = subprocess.run(  # noqa: S603
                    [self._dot, "-Tplain"],
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
    "build_dot_source",
    "find_dot_binary",
    "GraphvizLayoutEngine",
    "parse_plain_positions",
]

# Expose internals for unit tests under public names
build_dot_source = _build_dot_source
parse_plain_positions = _parse_plain_positions


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

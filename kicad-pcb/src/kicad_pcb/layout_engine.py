"""Layout engine Protocol and factory for kicad_pcb.

All layout engines satisfy :class:`LayoutEngine` and return
``{placement_ref: (x_mm, y_mm, rotation_deg | None)}`` placements.

Pipeline overview
-----------------
The full placement pipeline executed by :class:`~kicad_pcb.graphviz_layout.GraphvizLayoutEngine`:

1. **Tier assignment** (:func:`~kicad_pcb.tier.assign_tiers`) — topological
   longest-path layering assigns each component a discrete x-column (tier 0 =
   leftmost, increasing → rightward).  Connectors are pinned to tier 0;
   power-only components are post-processed separately.

2. **DOT graph construction** — a bipartite signal graph (components ↔ signal
   nets, power nets excluded) is serialised to Graphviz DOT format.  Node
   ``rank`` attributes inject the tier assignments so that Graphviz's
   barycentric ordering step minimises edge crossings while respecting the
   computed x-order.

3. **Graphviz rank / coordinate computation** — ``dot -Tplain`` is invoked as a
   subprocess.  The plain-text output provides (x, y) centre coordinates in
   inches, which are scaled to millimetres via ``SCALE_MM_PER_GV``.

4. **KiCad coordinate mapping** (:func:`~kicad_pcb.graphviz_layout.snap_positions`) —
   raw dot coordinates are snapped to the KiCad 200-mil grid
   (``GRID_ROW_MM = 7.62 mm``), page-bounds-checked, and deduplicated
   so no two symbols share the same grid cell.

5. **Orientation computation** (:func:`~kicad_pcb.layout.compute_orientations`) —
   each component is classified as *series* (0°, horizontal in signal path)
   or *shunt* (90°, bridging to a power/ground rail) based on its connected
   nets.

6. **Stereo-split** (optional, :func:`~kicad_pcb.layout.detect_stereo_channels`) —
   when the circuit contains two symmetric stereo channels, components are
   split vertically: left channel in the top half, right channel in the
   bottom half of the A4 page.

The final output maps each placement reference designator to a ``(x_mm, y_mm,
rotation_deg | None)`` triple ready for :func:`~kicad_pcb.sch_writer.write_schematic`.
For multi-unit devices this may be a placed-unit ref such as ``U1A`` or ``U1P``
rather than the parent device ref ``U1``.

Public API
----------
:class:`LayoutEngine`             — structural Protocol every engine must satisfy.
:class:`NoneLayoutEngine`         — no-op engine (places all at a fixed origin).
:func:`make_layout_engine`        — factory; always returns a
                                    :class:`~kicad_pcb.graphviz_layout.GraphvizLayoutEngine`.
                                    Raises :class:`RuntimeError` if ``dot`` is not found.
:func:`make_layout_engine_with_ir` — convenience wrapper that pre-computes tiers from
                                    a :class:`~kicad_pcb.circuit_ir.CircuitIR` so the
                                    engine skips a redundant ``assign_tiers`` call.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from typing_extensions import Protocol, runtime_checkable

if TYPE_CHECKING:
    from .circuit_ir import CircuitIR


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class LayoutEngine(Protocol):
    """Structural protocol satisfied by every concrete layout engine.

    An engine receives a :class:`~kicad_pcb.circuit_ir.CircuitIR` and
    returns a mapping from placement reference designator to placement triple.
    """

    def compute_symbol_positions(
        self, ir: CircuitIR
    ) -> dict[str, tuple[float, float, float | None]]:
        """Return placements for all components in *ir*.

        Returns
        -------
        dict[str, tuple[float, float, float | None]]
            ``{placement_ref: (x_mm, y_mm, rotation_deg)}`` where *rotation_deg* is
            ``None`` when the engine does not supply rotation information.
        """
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# Built-in: NoneLayoutEngine
# ---------------------------------------------------------------------------


class NoneLayoutEngine:
    """No-op engine for testing and baseline comparisons.

    Every component is placed at a fixed origin point.  On a real circuit
    this produces overlapping symbols and will trigger LAY003.
    """

    _ORIGIN: tuple[float, float, float | None] = (50.8, 76.2, None)

    def compute_symbol_positions(
        self, ir: CircuitIR
    ) -> dict[str, tuple[float, float, float | None]]:
        return {c.ref: self._ORIGIN for c in ir.components}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_layout_engine(
    *,
    seed: int = 7,
    cache_path: Path | None = None,
    debug_dump_path: Path | None = None,
    tiers: dict[str, int] | None = None,
    strict: bool = False,
) -> LayoutEngine:
    """Return a :class:`~kicad_pcb.graphviz_layout.GraphvizLayoutEngine`.

    Parameters
    ----------
    seed:
        Passed as ``-Gstart=<seed>`` to ``dot`` so every run on the same
        input graph produces the same layout (default ``7``).
    cache_path:
        If supplied, the Graphviz engine will load/save layout results from
        this JSON file keyed by the SHA-256 of the DOT source.
    tiers:
        Pre-computed tier mapping ``{ref: tier_int}`` produced by
        :func:`~kicad_pcb.tier.assign_tiers`.  When supplied, the engine
        skips its own ``assign_tiers`` call which avoids redundant work when
        the caller already has the tier data.

    Raises
    ------
    RuntimeError
        If the ``dot`` binary cannot be found.
    """
    from .graphviz_layout import GraphvizLayoutEngine, find_dot_binary  # noqa: PLC0415

    dot = find_dot_binary(strict=strict)
    if not dot:
        raise RuntimeError(
            "Graphviz 'dot' binary not found.  "
            "Set the GRAPHVIZ_DOT environment variable or install graphviz, then retry."
        )
    return GraphvizLayoutEngine(
        dot_path=dot,
        seed=seed,
        cache_path=cache_path,
        debug_dump_path=debug_dump_path,
        tiers=tiers,
        strict=strict,
    )


def make_layout_engine_with_ir(
    ir: CircuitIR,
    *,
    seed: int = 7,
    cache_path: Path | None = None,
    debug_dump_path: Path | None = None,
    strict: bool = False,
) -> LayoutEngine:
    """Return a :class:`~kicad_pcb.graphviz_layout.GraphvizLayoutEngine` with pre-computed tiers.

    Convenience wrapper around :func:`make_layout_engine` that calls
    :func:`~kicad_pcb.tier.assign_tiers` on *ir* and passes the result as
    the ``tiers`` argument so the engine never repeats the tier computation
    internally.

    Parameters
    ----------
    ir:
        Circuit IR used to pre-compute the tier mapping.
    seed:
        Forwarded to :func:`make_layout_engine`.
    cache_path:
        Forwarded to :func:`make_layout_engine`.

    Raises
    ------
    RuntimeError
        If the ``dot`` binary cannot be found.
    """
    from .tier import assign_tiers  # noqa: PLC0415

    precomputed_tiers = assign_tiers(ir, strict=strict)
    return make_layout_engine(
        seed=seed,
        cache_path=cache_path,
        debug_dump_path=debug_dump_path,
        tiers=precomputed_tiers,
        strict=strict,
    )

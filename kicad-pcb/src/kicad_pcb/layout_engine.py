"""Layout engine Protocol and factory for kicad_pcb.

All layout engines satisfy :class:`LayoutEngine` and return
``{ref: (x_mm, y_mm, rotation_deg | None)}`` placements.

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
    returns a mapping from reference designator to placement triple.
    """

    def compute_symbol_positions(
        self, ir: CircuitIR
    ) -> dict[str, tuple[float, float, float | None]]:
        """Return placements for all components in *ir*.

        Returns
        -------
        dict[str, tuple[float, float, float | None]]
            ``{ref: (x_mm, y_mm, rotation_deg)}`` where *rotation_deg* is
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
    tiers: dict[str, int] | None = None,
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

    dot = find_dot_binary()
    if not dot:
        raise RuntimeError(
            "Graphviz 'dot' binary not found.  "
            "Set the GRAPHVIZ_DOT environment variable or install graphviz, then retry."
        )
    return GraphvizLayoutEngine(dot_path=dot, seed=seed, cache_path=cache_path, tiers=tiers)


def make_layout_engine_with_ir(
    ir: CircuitIR,
    *,
    seed: int = 7,
    cache_path: Path | None = None,
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

    precomputed_tiers = assign_tiers(ir)
    return make_layout_engine(seed=seed, cache_path=cache_path, tiers=precomputed_tiers)

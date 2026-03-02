"""Layout engine Protocol and auto-selection factory for kicad_pcb.

All layout engines satisfy :class:`LayoutEngine` and return
``{ref: (x_mm, y_mm, rotation_deg | None)}`` placements.

Public API
----------
:class:`LayoutEngine`       — structural Protocol every engine must satisfy.
:class:`NoneLayoutEngine`   — no-op engine (places all at a fixed origin).
:func:`make_layout_engine`  — factory that picks the best available engine.
:data:`LayoutMode`          — ``Literal["auto", "graphviz", "heuristic", "none"]``

Mode semantics
--------------
``"auto"``
    Use Graphviz (``dot`` found via :envvar:`GRAPHVIZ_DOT` or :data:`PATH`).
    Raises :class:`RuntimeError` if ``dot`` is not found.  Equivalent to
    ``"graphviz"`` mode.
``"graphviz"``
    Always use :class:`~kicad_pcb.graphviz_layout.GraphvizLayoutEngine`.
    Raises :class:`RuntimeError` if ``dot`` is not found.
``"heuristic"``
    Always use :class:`~kicad_pcb.layout.HeuristicLayoutEngine`.
``"none"``
    No-op — every component is placed at (50.8, 76.2).  Useful for
    debugging; :data:`~kicad_pcb.lint.LAY_OVERLAP_THRESHOLD_MM` will fire
    on any multi-component schematic.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from typing_extensions import Protocol, runtime_checkable

if TYPE_CHECKING:
    from .circuit_ir import CircuitIR

# ---------------------------------------------------------------------------
# Public type alias
# ---------------------------------------------------------------------------

LayoutMode = Literal["auto", "graphviz", "heuristic", "none"]


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
    mode: LayoutMode,
    *,
    seed: int = 7,
    cache_path: Path | None = None,
) -> LayoutEngine:
    """Return the layout engine appropriate for *mode*.

    Parameters
    ----------
    mode:
        See module-level docstring for mode semantics.
    seed:
        Passed as ``-Gstart=<seed>`` to ``dot`` so every run on the same
        input graph produces the same layout (default ``7``).
    cache_path:
        If supplied, the Graphviz engine will load/save layout results from
        this JSON file keyed by the SHA-256 of the DOT source.  Ignored for
        non-Graphviz engines.

    Raises
    ------
    RuntimeError
        If *mode* is ``"graphviz"`` and the ``dot`` binary cannot be found.
    """
    if mode == "none":
        return NoneLayoutEngine()

    if mode == "heuristic":
        from .layout import HeuristicLayoutEngine  # noqa: PLC0415

        return HeuristicLayoutEngine()

    if mode == "graphviz":
        from .graphviz_layout import GraphvizLayoutEngine, find_dot_binary  # noqa: PLC0415

        dot = find_dot_binary()
        if not dot:
            raise RuntimeError(
                "Graphviz 'dot' binary not found.  "
                "Set the GRAPHVIZ_DOT environment variable or install graphviz, then retry."
            )
        return GraphvizLayoutEngine(dot_path=dot, seed=seed, cache_path=cache_path)

    # mode == "auto"
    from .graphviz_layout import GraphvizLayoutEngine, find_dot_binary  # noqa: PLC0415

    dot = find_dot_binary()
    if not dot:
        raise RuntimeError(
            "Graphviz 'dot' binary not found.  "
            "Set the GRAPHVIZ_DOT environment variable or install graphviz, then retry."
        )
    return GraphvizLayoutEngine(dot_path=dot, seed=seed, cache_path=cache_path)

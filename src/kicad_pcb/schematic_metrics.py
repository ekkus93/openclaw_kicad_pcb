"""Schematic readability metrics.

Provides lightweight, read-only analysis functions over a parsed
:class:`~kicad_pcb.sch_doc.SchematicDoc` (or its root ``ListNode``).  All
functions are pure — they never mutate the document.

Typical usage::

    from pathlib import Path
    from kicad_pcb.sch_doc import SchematicDoc
    from kicad_pcb.schematic_metrics import (
        average_symbol_spacing,
        count_distinct_x_columns,
        count_global_labels,
        count_power_symbols,
        count_short_wire_segments,
        page_region_density,
        run_layout_lints,
        wire_stub_ratio,
    )

    doc = SchematicDoc.load(Path("OpenClaw_Managed.kicad_sch"))
    print(count_distinct_x_columns(doc))
    print(count_global_labels(doc, text="GND"))
    print(count_power_symbols(doc))
    print(count_short_wire_segments(doc))
    print(average_symbol_spacing(doc))
    print(page_region_density(doc))
    print(run_layout_lints(doc))
    print(wire_stub_ratio(doc))

These helpers are used by acceptance tests (Phase 6 golden tests) to enforce
readability criteria on generated schematics.
"""

from __future__ import annotations

from ._schematic_metrics_base import (  # noqa: F401
    _anchor_x_coordinate,
    _resolve_placed_refs,
    _symbol_ref_positions,
    count_distinct_x_columns,
    count_global_labels,
    count_non_power_symbols_in_same_x_column_as,
    count_refs_in_same_x_column_as,
)
from ._schematic_metrics_density import (  # noqa: F401
    compute_block_role_spread,
    compute_block_separation,
    compute_local_density,
    detect_dense_clusters,
)
from ._schematic_metrics_wires import (  # noqa: F401
    average_symbol_spacing,
    count_power_symbols,
    count_short_wire_segments,
    page_region_density,
    run_layout_lints,
    wire_stub_ratio,
)

__all__ = [
    "average_symbol_spacing",
    "compute_block_separation",
    "compute_block_role_spread",
    "compute_local_density",
    "count_distinct_x_columns",
    "count_global_labels",
    "count_non_power_symbols_in_same_x_column_as",
    "count_power_symbols",
    "count_refs_in_same_x_column_as",
    "count_short_wire_segments",
    "detect_dense_clusters",
    "page_region_density",
    "run_layout_lints",
    "wire_stub_ratio",
]

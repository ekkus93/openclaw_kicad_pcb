"""Page composition lint rules (LAY012–LAY013)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..block_detection import is_core_like_role, is_input_like_role, is_output_like_role
from .defs import _WARN, LintIssue

if TYPE_CHECKING:
    from ..block_detection import BlockLayout
    from ..sch_doc import SchematicDoc

# LAY012: page composition imbalance threshold. Uses the Phase 8.1 page-quadrant
# metric where 0 means perfectly balanced and 1 means all symbols in one region.
_LAY_PAGE_IMBALANCE_THRESHOLD: float = 0.55


def _extract_symbol_positions(doc: SchematicDoc) -> dict[str, tuple[float, float]]:
    comp_positions: dict[str, tuple[float, float]] = {}
    for sym_meta in doc.list_symbols():
        ref_val = sym_meta.get("ref")
        if not isinstance(ref_val, str):
            continue
        x_val = sym_meta.get("x")
        if not isinstance(x_val, int | float):
            continue
        y_val = sym_meta.get("y")
        if not isinstance(y_val, int | float):
            continue
        ref = ref_val
        x = float(x_val)
        y = float(y_val)
        comp_positions[ref] = (x, y)
    return comp_positions


def lint_layout_composition(
    doc: SchematicDoc,
    block_layout: BlockLayout | None = None,
) -> list[LintIssue]:
    """LAY012 & LAY013: warn about poor page balance and awkward composition.

    LAY012 uses the Phase 8.1 quadrant-utilization metric to detect when one
    page region is much denser than another. LAY013 covers the Phase 8.2
    central-composition concerns: title-block encroachment, op-amp stages that
    sit too high or too low, and vertically collapsed signal-path layouts.

    Parameters
    ----------
    doc:
        Schematic document :class:`~kicad_pcb.kicad_sch.SchematicDoc`.
    block_layout:
        Optional functional block classification from
        :func:`~kicad_pcb.block_detection.classify_circuit`. When supplied,
        OPAMP_CORE and signal-path role checks become more precise.

    Returns
    -------
    list[LintIssue]
        LAY012 and/or LAY013 WARNINGs for poor page balance or awkward block
        composition, otherwise an empty list.
    """
    from ..graphviz_layout.snap import (  # noqa: PLC0415
        _MIN_CIRCUIT_SPAN_FRACTION,
        _OPAMP_LOWER_LIMIT_FRACTION,
        _OPAMP_UPPER_LIMIT_FRACTION,
        _TITLE_BLOCK_CLEARANCE_MM,
        ORIGIN_Y,
        PAGE_MAX_Y,
        _compute_page_quadrant_utilization,
    )

    issues: list[LintIssue] = []
    comp_positions = _extract_symbol_positions(doc)
    if not comp_positions:
        return issues

    refs_for_balance = [ref for ref in comp_positions if not ref.startswith("#")]
    if len(refs_for_balance) >= 4:  # noqa: PLR2004
        quadrant_positions = {
            ref: (x, y, None) for ref, (x, y) in comp_positions.items() if ref in refs_for_balance
        }
        utilization = _compute_page_quadrant_utilization(quadrant_positions)
        imbalance = float(utilization["imbalance"])
        if imbalance >= _LAY_PAGE_IMBALANCE_THRESHOLD:
            dense_quadrant = str(utilization["dense_quadrant"])
            sparse_quadrant = str(utilization["sparse_quadrant"])
            issues.append(
                LintIssue(
                    _WARN,
                    "LAY012",
                    "Page composition is unbalanced: "
                    f"{dense_quadrant} is dense while {sparse_quadrant} is sparse "
                    f"(imbalance {imbalance:.0%}, threshold {_LAY_PAGE_IMBALANCE_THRESHOLD:.0%}); "
                    "redistribute blocks to use the page more evenly.",
                    path="layout/positions",
                )
            )

    signal_refs = [ref for ref in comp_positions if not ref.startswith("#")]
    opamp_refs: list[str] = []
    if block_layout is not None:
        from ..block_detection import BlockRole  # noqa: PLC0415

        role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}
        signal_refs = [
            ref
            for ref in comp_positions
            if (
                (role := role_by_ref.get(ref)) is not None
                and (
                    is_input_like_role(role)
                    or is_core_like_role(role)
                    or is_output_like_role(role)
                    or role == BlockRole.DECOUPLING
                )
                and not ref.startswith("#")
            )
        ]
        opamp_refs = [ref for ref in signal_refs if role_by_ref.get(ref) == BlockRole.OPAMP_CORE]

    if not signal_refs:
        return issues

    safe_max_y = PAGE_MAX_Y - _TITLE_BLOCK_CLEARANCE_MM
    title_block_refs = sorted(ref for ref in signal_refs if comp_positions[ref][1] > safe_max_y)
    if title_block_refs:
        shown = ", ".join(title_block_refs[:4])
        extra = "" if len(title_block_refs) <= 4 else f" (+{len(title_block_refs) - 4} more)"
        issues.append(
            LintIssue(
                _WARN,
                "LAY013",
                "Signal components encroach on the title-block clearance band "
                f"below y={safe_max_y:.1f}mm: {shown}{extra}; move the affected "
                "stage upward.",
                path="kicad_sch/symbol",
            )
        )

    if opamp_refs:
        page_height = PAGE_MAX_Y - ORIGIN_Y
        upper_limit_y = ORIGIN_Y + (_OPAMP_UPPER_LIMIT_FRACTION * page_height)
        lower_limit_y = ORIGIN_Y + (_OPAMP_LOWER_LIMIT_FRACTION * page_height)
        opamp_avg_y = sum(comp_positions[ref][1] for ref in opamp_refs) / len(opamp_refs)
        if opamp_avg_y < upper_limit_y:
            issues.append(
                LintIssue(
                    _WARN,
                    "LAY013",
                    "Op-amp stage is too high on the page "
                    f"(avg y={opamp_avg_y:.1f}mm, lower bound {upper_limit_y:.1f}mm); "
                    "shift the core stage downward for better central composition.",
                    path="kicad_sch/symbol",
                )
            )
        elif opamp_avg_y > lower_limit_y:
            issues.append(
                LintIssue(
                    _WARN,
                    "LAY013",
                    "Op-amp stage is too low on the page "
                    f"(avg y={opamp_avg_y:.1f}mm, upper bound {lower_limit_y:.1f}mm); "
                    "shift the core stage upward for better central composition.",
                    path="kicad_sch/symbol",
                )
            )

    signal_ys = [comp_positions[ref][1] for ref in signal_refs]
    span = max(signal_ys) - min(signal_ys)
    available_height = PAGE_MAX_Y - ORIGIN_Y
    min_span = _MIN_CIRCUIT_SPAN_FRACTION * available_height
    if span < min_span:
        issues.append(
            LintIssue(
                _WARN,
                "LAY013",
                f"Signal-path vertical span is too small ({span:.1f}mm, minimum {min_span:.1f}mm); "
                "the schematic looks vertically collapsed rather than intentionally composed.",
                path="kicad_sch/symbol",
            )
        )

    return issues

"""Fail-closed schematic page geometry for refinement."""

from __future__ import annotations

from dataclasses import dataclass

from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.nodes import ListNode, StringNode


@dataclass(frozen=True)
class PageBounds:
    width_mm: float
    height_mm: float
    paper: str


_PAPER_MM: dict[str, tuple[float, float]] = {
    "A5": (210.0, 148.0),
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
    "Letter": (279.4, 215.9),
    "Legal": (355.6, 215.9),
    "Ledger": (431.8, 279.4),
}


def schematic_page_bounds(doc: SchematicDoc) -> PageBounds:
    """Return declared paper bounds or fail closed for unsupported/missing paper."""

    paper = next(
        (
            node
            for node in doc.root.items
            if isinstance(node, ListNode) and node.key == "paper"
        ),
        None,
    )
    if paper is None or len(paper.items) < 2 or not isinstance(paper.items[1], StringNode):
        raise UserError(
            "Schematic refinement requires an explicit supported (paper ...) declaration.",
            code="REFINEMENT_UNSUPPORTED_PAGE",
        )
    name = paper.items[1].value
    dimensions = _PAPER_MM.get(name)
    if dimensions is None:
        raise UserError(
            f"Unsupported schematic paper format for refinement: {name}",
            code="REFINEMENT_UNSUPPORTED_PAGE",
            details={"paper": name, "supported": sorted(_PAPER_MM)},
        )
    return PageBounds(width_mm=dimensions[0], height_mm=dimensions[1], paper=name)

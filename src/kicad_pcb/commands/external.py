"""External service commands: pcbway-quote."""

from __future__ import annotations

from ..config import get_current_project
from ..results import PcbwayQuoteResult


def cmd_pcbway_quote(args) -> PcbwayQuoteResult:
    """Get PCBWay instant quote."""
    project = get_current_project()

    quantity = args.quantity or 5
    layers = args.layers or 2
    thickness = args.thickness or 1.6

    # Rough estimate based on typical pricing.
    # Real implementation would call PCBWay API.
    base_price = 5.0  # $5 base for small boards
    layer_mult = 1.0 if layers <= 2 else 2.0 if layers <= 4 else 4.0
    qty_mult = 1.0 if quantity <= 10 else 0.8  # volume discount

    board_cost = base_price * layer_mult * qty_mult
    shipping = 18.0  # DHL estimate

    gerber_zip = None
    if project:
        candidate = project.path / f"{project.name}_fab.zip"
        if candidate.exists():
            gerber_zip = candidate

    return PcbwayQuoteResult(
        quantity=quantity,
        layers=layers,
        thickness=thickness,
        board_cost=board_cost,
        shipping=shipping,
        total=board_cost + shipping,
        gerber_zip=gerber_zip,
    )

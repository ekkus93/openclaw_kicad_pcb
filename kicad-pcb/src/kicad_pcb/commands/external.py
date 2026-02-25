"""External service commands: pcbway-quote."""
from __future__ import annotations

from ..config import get_current_project


def cmd_pcbway_quote(args) -> None:
    """Get PCBWay instant quote."""
    project = get_current_project()

    print("╭─────────────────────────────────────╮")
    print("│       💰 PCBWAY QUOTE ESTIMATE      │")
    print("├─────────────────────────────────────┤")

    # Parse options
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

    print(f"│  Quantity:    {quantity:>4} pcs              │")
    print(f"│  Layers:      {layers:>4}                   │")
    print(f"│  Thickness:   {thickness:>4} mm              │")
    print("├─────────────────────────────────────┤")
    print(f"│  Board cost:  ${board_cost:>7.2f}              │")
    print(f"│  Shipping:    ${shipping:>7.2f} (DHL est.)   │")
    print("│  ─────────────────────────          │")
    print(f"│  TOTAL:       ${board_cost + shipping:>7.2f}              │")
    print("╰─────────────────────────────────────╯")

    print("\n⚠️  This is an estimate. Actual price may vary.")
    print("📤 To order: Upload Gerbers at pcbway.com/orderonline.aspx")

    if project:
        gerber_zip = project.path / f"{project.name}_fab.zip"
        if gerber_zip.exists():
            print(f"\n✅ Gerber package ready: {gerber_zip}")
        else:
            print("\n💡 Run `package-for-fab` first to create Gerber ZIP")

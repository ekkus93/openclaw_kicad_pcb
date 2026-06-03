"""Synthetic placeholder symbol synthesis for unknown KiCad library symbols.

When the Circuit IR references a component whose symbol is not present in any
searched symbol library, this module builds a generic rectangular placeholder
that can be embedded directly into the generated schematic.

The placeholder:
- Uses only the pin numbers referenced in the Circuit IR (never invented).
- Places pins in a two-column layout: numerically-lower half on the left,
  higher half on the right.
- Marks the symbol with a dashed bounding-box stroke and a "[PLACEHOLDER]"
  value suffix so designers can identify which components need real symbols.
- Returns pin-at geometry compatible with SymbolIndex.get_unit_pin_at so the
  router can connect wires correctly without any special-casing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .sexpr.builder import L, atom, fnum, string
from .sexpr.nodes import ListNode

# ---------------------------------------------------------------------------
# Geometry constants (all in mm, on 0.254 mm snap grid)
# ---------------------------------------------------------------------------

_BOX_HALF_WIDTH: float = 5.08  # half of 400-mil box width
_STUB_LENGTH: float = 2.54  # standard KiCad pin stub (100 mil)
_PIN_PITCH: float = 2.54  # vertical pin spacing (100 mil)
_FONT_SIZE: float = 1.27  # standard KiCad label font (50 mil)
_SINGLE_UNIT: str = "1"


@dataclass(frozen=True)
class PlaceholderSymbol:
    """All data the pipeline needs to use a synthesised stand-in symbol."""

    symbol_id: str
    """Fully-qualified KiCad symbol id, e.g. '74xx:74HC4017'."""

    pin_numbers: frozenset[str]
    """Pin numbers taken from the Circuit IR nets — never invented."""

    definition: ListNode
    """Ready-to-embed (symbol "lib:name" ...) S-expression node."""

    pin_at: dict[str, dict[str, tuple[float, float, float]]]
    """unit -> {pin_number -> (x_mm, y_mm, angle_deg)}.

    Matches the return format of SymbolIndex.get_unit_pin_at so the router
    receives placeholder geometry transparently.  Placeholder symbols are
    always single-unit, so the outer dict has a single key ``"1"``.
    """


def build(symbol_id: str, pin_numbers: frozenset[str]) -> PlaceholderSymbol:
    """Synthesise a placeholder symbol for *symbol_id* with *pin_numbers*.

    Parameters
    ----------
    symbol_id:
        Fully-qualified KiCad symbol id, e.g. ``"74xx:74HC4017"``.
    pin_numbers:
        Set of pin number strings derived from the Circuit IR.  Must be
        non-empty.
    """
    if not pin_numbers:
        raise ValueError(f"Cannot build a placeholder with no pins: {symbol_id!r}")

    sym_name = symbol_id.split(":", 1)[-1]

    sorted_pins = sorted(pin_numbers, key=_pin_sort_key)
    left_pins, right_pins = _split_pins(sorted_pins)

    box_height_half = _box_half_height(max(len(left_pins), len(right_pins)))
    stub_x_left = -(_BOX_HALF_WIDTH + _STUB_LENGTH)
    stub_x_right = _BOX_HALF_WIDTH + _STUB_LENGTH

    pin_at_map: dict[str, tuple[float, float, float]] = {}
    pin_nodes: list[ListNode] = []

    for col_index, col_pins in enumerate((left_pins, right_pins)):
        if not col_pins:
            continue
        is_right = col_index == 1
        # angle: 0 = stub points left  (left-side pin connects from outside-left)
        #       180 = stub points right (right-side pin connects from outside-right)
        pin_angle = 180.0 if is_right else 0.0
        x_at = stub_x_right if is_right else stub_x_left
        y_coords = _column_y_coords(len(col_pins), box_height_half)
        for pin_num, y in zip(col_pins, y_coords):
            pin_at_map[pin_num] = (x_at, y, pin_angle)
            pin_nodes.append(_make_pin_node(pin_num, x_at, y, pin_angle))

    box = _make_box_node(-_BOX_HALF_WIDTH, -box_height_half, _BOX_HALF_WIDTH, box_height_half)
    sub_sym = L(atom("symbol"), string(f"{sym_name}_0_1"), box, *pin_nodes)

    ref_prefix = _infer_ref_prefix(sym_name)
    value_text = f"{sym_name} [PLACEHOLDER]"
    ref_y = box_height_half + 1.27
    val_y = -(box_height_half + 1.27)

    definition = L(
        atom("symbol"),
        string(symbol_id),
        _make_property("Reference", ref_prefix, 0.0, ref_y),
        _make_property("Value", value_text, 0.0, val_y),
        _make_property("Footprint", "", 0.0, 0.0),
        _make_property("Datasheet", "", 0.0, 0.0),
        sub_sym,
    )

    return PlaceholderSymbol(
        symbol_id=symbol_id,
        pin_numbers=pin_numbers,
        definition=definition,
        pin_at={_SINGLE_UNIT: pin_at_map},
    )


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _pin_sort_key(pin: str) -> tuple[int, str]:
    """Sort pins numerically when possible, lexicographically otherwise."""
    try:
        return (int(pin), "")
    except ValueError:
        return (10**9, pin)


def _split_pins(sorted_pins: list[str]) -> tuple[list[str], list[str]]:
    """Split into (left_column, right_column) with the lower half on the left."""
    n = len(sorted_pins)
    left_count = math.ceil(n / 2)
    return sorted_pins[:left_count], sorted_pins[left_count:]


def _box_half_height(max_col_pins: int) -> float:
    """Half-height of the bounding box so pins are centred with padding."""
    return max(max_col_pins, 1) * _PIN_PITCH / 2.0


def _column_y_coords(n: int, box_height_half: float) -> list[float]:
    """Return y-coordinates for *n* pins centred inside the box."""
    if n == 0:
        return []
    total_span = (n - 1) * _PIN_PITCH
    top_y = total_span / 2.0
    return [top_y - i * _PIN_PITCH for i in range(n)]


def _make_pin_node(pin_num: str, x: float, y: float, angle: float) -> ListNode:
    font = L(atom("font"), L(atom("size"), fnum(_FONT_SIZE, 2), fnum(_FONT_SIZE, 2)))
    effects = L(atom("effects"), font)
    return L(
        atom("pin"),
        atom("passive"),
        atom("line"),
        L(atom("at"), fnum(x, 2), fnum(y, 2), atom(str(int(angle)))),
        L(atom("length"), fnum(_STUB_LENGTH, 2)),
        L(atom("name"), string("~"), effects),
        L(atom("number"), string(pin_num), effects),
    )


def _make_box_node(x1: float, y1: float, x2: float, y2: float) -> ListNode:
    return L(
        atom("rectangle"),
        L(atom("start"), fnum(x1, 2), fnum(y1, 2)),
        L(atom("end"), fnum(x2, 2), fnum(y2, 2)),
        L(atom("stroke"), L(atom("width"), fnum(0.0, 0)), L(atom("type"), atom("dash"))),
        L(atom("fill"), L(atom("type"), atom("none"))),
    )


def _make_property(name: str, value: str, x: float, y: float) -> ListNode:
    return L(
        atom("property"),
        string(name),
        string(value),
        L(atom("at"), fnum(x, 2), fnum(y, 2), atom("0")),
        L(
            atom("effects"),
            L(atom("font"), L(atom("size"), fnum(_FONT_SIZE, 2), fnum(_FONT_SIZE, 2))),
        ),
    )


def _infer_ref_prefix(sym_name: str) -> str:
    """Return a plausible reference designator prefix from the symbol name."""
    name_upper = sym_name.upper()
    if name_upper.startswith("R"):
        return "R"
    if name_upper.startswith("C"):
        return "C"
    if name_upper.startswith("D") or name_upper.startswith("LED"):
        return "D"
    if name_upper.startswith("Q"):
        return "Q"
    if name_upper.startswith("J") or "CONN" in name_upper:
        return "J"
    return "U"

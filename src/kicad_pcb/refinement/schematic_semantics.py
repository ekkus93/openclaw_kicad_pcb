"""Schematic-level semantic extraction required by visual refinement."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from kicad_pcb.electrical_equivalence import ElectricalTerminal
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.nodes import AtomNode, ListNode, Node, StringNode


@dataclass(frozen=True, order=True)
class PlacedSchematicComponent:
    """Electrical/presentation metadata for one placed symbol instance."""

    ref: str
    symbol_id: str
    value: str
    footprint: str
    unit: str
    x: float
    y: float
    rotation: int
    uuid: str
    in_bom: str
    on_board: str


@dataclass(frozen=True)
class SchematicSemanticSnapshot:
    """Accepted/candidate schematic details not fully represented by XML netlist."""

    components: tuple[PlacedSchematicComponent, ...]
    helper_refs: tuple[str, ...]
    no_connect_terminals: tuple[ElectricalTerminal, ...]

    @property
    def footprints_by_ref(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for component in self.components:
            if component.ref in self.helper_refs:
                continue
            existing = result.get(component.ref)
            if existing is None:
                result[component.ref] = component.footprint
            elif existing != component.footprint:
                raise UserError(
                    f"Placed units for {component.ref} disagree on footprint.",
                    code=ErrorCode.VALIDATION_FAILED,
                    details={
                        "ref": component.ref,
                        "first_footprint": existing,
                        "other_footprint": component.footprint,
                    },
                )
        return result


def extract_schematic_semantics(
    path: Path,
    *,
    no_connect_tolerance_mm: float = 0.05,
) -> SchematicSemanticSnapshot:
    """Parse a schematic and extract strict refinement semantics."""

    doc = SchematicDoc.load(path)
    components = tuple(_placed_component(node) for node in _placed_symbol_nodes(doc))
    helper_refs = tuple(sorted({c.ref for c in components if _is_explicit_power_helper(c)}))
    no_connects = _extract_no_connect_terminals(
        doc,
        components=components,
        tolerance_mm=no_connect_tolerance_mm,
    )
    return SchematicSemanticSnapshot(
        components=tuple(sorted(components)),
        helper_refs=helper_refs,
        no_connect_terminals=no_connects,
    )


def _placed_symbol_nodes(doc: SchematicDoc) -> list[ListNode]:
    return [node for node in doc.root.items if isinstance(node, ListNode) and node.key == "symbol"]


def _placed_component(node: ListNode) -> PlacedSchematicComponent:
    symbol_id = _list_string(node, "lib_id")
    uuid = _list_string(node, "uuid")
    unit = _list_atom(node, "unit") or "1"
    x, y, rotation = _at(node)
    properties = _properties(node)
    ref = properties.get("Reference", "")
    if not ref or not symbol_id:
        raise UserError(
            "Placed schematic symbol is missing Reference or lib_id.",
            code=ErrorCode.VALIDATION_FAILED,
            details={"ref": ref, "symbol_id": symbol_id, "uuid": uuid},
        )
    return PlacedSchematicComponent(
        ref=ref,
        symbol_id=symbol_id,
        value=properties.get("Value", ""),
        footprint=properties.get("Footprint", ""),
        unit=unit,
        x=x,
        y=y,
        rotation=rotation,
        uuid=uuid,
        in_bom=_list_atom(node, "in_bom"),
        on_board=_list_atom(node, "on_board"),
    )


def _is_explicit_power_helper(component: PlacedSchematicComponent) -> bool:
    """Narrow helper policy: all power-specific signals must agree."""

    return (
        component.ref.startswith("#PWR")
        and component.symbol_id.startswith("power:")
        and component.in_bom == "no"
        and component.on_board == "no"
    )


def _extract_no_connect_terminals(
    doc: SchematicDoc,
    *,
    components: tuple[PlacedSchematicComponent, ...],
    tolerance_mm: float,
) -> tuple[ElectricalTerminal, ...]:
    points = _no_connect_points(doc)
    if not points:
        return ()

    library_symbols = _embedded_library_symbols(doc)
    terminal_positions: list[tuple[ElectricalTerminal, float, float]] = []
    for component in components:
        if _is_explicit_power_helper(component):
            continue
        lib_symbol = library_symbols.get(component.symbol_id)
        if lib_symbol is None:
            raise UserError(
                (
                    f"Cannot verify no-connect state for {component.ref}: "
                    "embedded symbol definition missing."
                ),
                code=ErrorCode.VALIDATION_FAILED,
                details={"ref": component.ref, "symbol_id": component.symbol_id},
            )
        for pin, local_x, local_y in _library_pin_points(lib_symbol, unit=component.unit):
            global_x, global_y = _transform_point(
                local_x,
                local_y,
                origin_x=component.x,
                origin_y=component.y,
                rotation=component.rotation,
            )
            terminal_positions.append(
                (
                    ElectricalTerminal(ref=component.ref, pin=pin, unit=component.unit),
                    global_x,
                    global_y,
                )
            )

    resolved: list[ElectricalTerminal] = []
    for x, y in points:
        matches = [
            terminal
            for terminal, px, py in terminal_positions
            if math.hypot(px - x, py - y) <= tolerance_mm
        ]
        unique_matches = sorted(set(matches))
        if len(unique_matches) != 1:
            raise UserError(
                "Cannot unambiguously map schematic no-connect marker to one logical terminal.",
                code=ErrorCode.VALIDATION_FAILED,
                details={
                    "x": x,
                    "y": y,
                    "tolerance_mm": tolerance_mm,
                    "matches": [
                        {"ref": match.ref, "pin": match.pin, "unit": match.unit}
                        for match in unique_matches
                    ],
                },
            )
        resolved.append(unique_matches[0])
    return tuple(sorted(set(resolved)))


def _no_connect_points(doc: SchematicDoc) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for node in doc.root.items:
        if not isinstance(node, ListNode) or node.key != "no_connect":
            continue
        x, y, _rotation = _at(node, require_rotation=False)
        points.append((x, y))
    return sorted(points)


def _embedded_library_symbols(doc: SchematicDoc) -> dict[str, ListNode]:
    section = next(
        (
            node
            for node in doc.root.items
            if isinstance(node, ListNode) and node.key == "lib_symbols"
        ),
        None,
    )
    if section is None:
        return {}
    result: dict[str, ListNode] = {}
    for node in section.items:
        if (
            isinstance(node, ListNode)
            and node.key == "symbol"
            and len(node.items) >= 2
            and isinstance(node.items[1], StringNode)
        ):
            result[node.items[1].value] = node
    return result


def _library_pin_points(symbol: ListNode, *, unit: str) -> list[tuple[str, float, float]]:
    direct = _direct_pin_points(symbol)
    nested: list[tuple[str, float, float]] = []
    for child in symbol.items:
        if not (
            isinstance(child, ListNode)
            and child.key == "symbol"
            and len(child.items) >= 2
            and isinstance(child.items[1], StringNode)
        ):
            continue
        nested_name = child.items[1].value
        nested_unit = _nested_symbol_unit(nested_name)
        if nested_unit in {"0", unit}:
            nested.extend(_direct_pin_points(child))

    combined = direct + nested
    by_pin: dict[str, tuple[float, float]] = {}
    for pin, x, y in combined:
        existing = by_pin.get(pin)
        if existing is None:
            by_pin[pin] = (x, y)
            continue
        if existing != (x, y):
            raise UserError(
                "Embedded symbol exposes ambiguous pin geometry for selected unit.",
                code=ErrorCode.VALIDATION_FAILED,
                details={"pin": pin, "unit": unit, "positions": [existing, (x, y)]},
            )
    return [(pin, *coords) for pin, coords in sorted(by_pin.items())]


def _nested_symbol_unit(name: str) -> str | None:
    parts = name.rsplit("_", 2)
    if len(parts) != 3:
        return None
    unit = parts[-2]
    return unit if unit.isdigit() else None


def _direct_pin_points(node: ListNode) -> list[tuple[str, float, float]]:
    result: list[tuple[str, float, float]] = []
    for child in node.items:
        if not isinstance(child, ListNode) or child.key != "pin":
            continue
        number = ""
        x = y = None
        for pin_child in child.items:
            if not isinstance(pin_child, ListNode):
                continue
            if (
                pin_child.key == "number"
                and len(pin_child.items) >= 2
                and isinstance(pin_child.items[1], StringNode)
            ):
                number = pin_child.items[1].value
            elif pin_child.key == "at" and len(pin_child.items) >= 3:
                x = _numeric(pin_child.items[1])
                y = _numeric(pin_child.items[2])
        if number and x is not None and y is not None:
            result.append((number, x, y))
    return result


def _transform_point(
    x: float,
    y: float,
    *,
    origin_x: float,
    origin_y: float,
    rotation: int,
) -> tuple[float, float]:
    normalized = rotation % 360
    if normalized == 0:
        rx, ry = x, y
    elif normalized == 90:
        rx, ry = -y, x
    elif normalized == 180:
        rx, ry = -x, -y
    elif normalized == 270:
        rx, ry = y, -x
    else:
        raise UserError(
            "Refinement no-connect verification supports only cardinal symbol rotations.",
            code=ErrorCode.VALIDATION_FAILED,
            details={"rotation": rotation},
        )
    return origin_x + rx, origin_y + ry


def _properties(node: ListNode) -> dict[str, str]:
    result: dict[str, str] = {}
    for child in node.items:
        if (
            isinstance(child, ListNode)
            and child.key == "property"
            and len(child.items) >= 3
            and isinstance(child.items[1], StringNode)
            and isinstance(child.items[2], StringNode)
        ):
            result[child.items[1].value] = child.items[2].value
    return result


def _list_string(node: ListNode, key: str) -> str:
    for child in node.items:
        if (
            isinstance(child, ListNode)
            and child.key == key
            and len(child.items) >= 2
            and isinstance(child.items[1], StringNode)
        ):
            return child.items[1].value
    return ""


def _list_atom(node: ListNode, key: str) -> str:
    for child in node.items:
        if (
            isinstance(child, ListNode)
            and child.key == key
            and len(child.items) >= 2
            and isinstance(child.items[1], AtomNode)
        ):
            return child.items[1].value
    return ""


def _at(node: ListNode, *, require_rotation: bool = True) -> tuple[float, float, int]:
    for child in node.items:
        if isinstance(child, ListNode) and child.key == "at" and len(child.items) >= 3:
            x = _numeric(child.items[1])
            y = _numeric(child.items[2])
            rotation = 0
            if len(child.items) >= 4:
                rotation_value = _numeric(child.items[3])
                if not rotation_value.is_integer():
                    raise UserError(
                        "Non-integral schematic rotation is unsupported by refinement.",
                        code=ErrorCode.VALIDATION_FAILED,
                        details={"rotation": rotation_value},
                    )
                rotation = int(rotation_value)
            elif require_rotation:
                rotation = 0
            return x, y, rotation
    raise UserError(
        f"Schematic node {node.key!r} is missing required (at ...) coordinates.",
        code=ErrorCode.VALIDATION_FAILED,
    )


def _numeric(node: Node) -> float:
    if not isinstance(node, AtomNode):
        raise UserError(
            "Expected numeric atom while extracting schematic semantics.",
            code=ErrorCode.VALIDATION_FAILED,
        )
    try:
        value = float(node.value)
    except ValueError as exc:
        raise UserError(
            "Invalid numeric atom while extracting schematic semantics.",
            code=ErrorCode.VALIDATION_FAILED,
            details={"value": node.value},
        ) from exc
    if not math.isfinite(value):
        raise UserError(
            "Non-finite schematic coordinate is not allowed.",
            code=ErrorCode.VALIDATION_FAILED,
            details={"value": node.value},
        )
    return value

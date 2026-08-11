"""Schematic-level semantic extraction required by visual refinement."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from kicad_pcb.electrical_equivalence import ElectricalTerminal
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.sch_doc import (
    SchematicDoc,
    read_lib_symbol_pin_at,
    read_lib_symbol_unit_pin_at,
)
from kicad_pcb.sexpr.nodes import AtomNode, ListNode, Node, StringNode
from kicad_pcb.symbol_index import resolve_symbol_dirs

PlacementOverride = tuple[float, float, int]


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

    return extract_schematic_semantics_from_doc(
        SchematicDoc.load(path),
        no_connect_tolerance_mm=no_connect_tolerance_mm,
    )


def extract_schematic_semantics_from_doc(
    doc: SchematicDoc,
    *,
    no_connect_tolerance_mm: float = 0.05,
    placement_overrides: Mapping[tuple[str, str], PlacementOverride] | None = None,
) -> SchematicSemanticSnapshot:
    """Extract semantics from an already parsed document.

    ``placement_overrides`` is intentionally keyed by exact ``(ref, unit)`` and
    exists only for deterministic candidate geometry calculations.  It never
    changes symbol/value/footprint/connectivity semantics.
    """

    components: list[PlacedSchematicComponent] = []
    for node in _placed_symbol_nodes(doc):
        component = _placed_component(node)
        if placement_overrides is not None:
            override = placement_overrides.get((component.ref, component.unit))
            if override is not None:
                x, y, rotation = override
                if not all(math.isfinite(v) for v in (x, y, float(rotation))):
                    raise UserError(
                        "Non-finite placement override is not allowed.",
                        code=ErrorCode.VALIDATION_FAILED,
                        details={"ref": component.ref, "unit": component.unit},
                    )
                component = replace(component, x=x, y=y, rotation=int(rotation))
        components.append(component)
    component_tuple = tuple(components)
    helper_refs = tuple(sorted({c.ref for c in component_tuple if _is_explicit_power_helper(c)}))
    no_connects = _extract_no_connect_terminals(
        doc,
        components=component_tuple,
        tolerance_mm=no_connect_tolerance_mm,
    )
    return SchematicSemanticSnapshot(
        components=tuple(sorted(component_tuple)),
        helper_refs=helper_refs,
        no_connect_terminals=no_connects,
    )


def resolve_component_pin_position_candidates(
    doc: SchematicDoc,
    component: PlacedSchematicComponent,
) -> dict[ElectricalTerminal, tuple[tuple[float, float], ...]]:
    """Return every supported schematic-space position for each logical pin.

    Accepted schematics may contain flattened embedded symbol geometry while
    routing/no-connect placement was produced using source-library geometry.
    Verification therefore retains all distinct positions for the *same*
    logical terminal.  Ambiguity between different logical terminals is still
    rejected by the caller.
    """

    library_symbols = _embedded_library_symbols(doc)
    points: list[tuple[str, float, float]] = []
    embedded = library_symbols.get(component.symbol_id)
    if embedded is not None:
        points.extend(_library_pin_points(embedded, unit=component.unit))
    points.extend(_external_library_pin_points(component))
    if not points:
        raise UserError(
            f"Cannot resolve pin geometry for {component.ref}.",
            code=ErrorCode.VALIDATION_FAILED,
            details={"ref": component.ref, "symbol_id": component.symbol_id},
        )

    result: dict[ElectricalTerminal, set[tuple[float, float]]] = {}
    for pin, local_x, local_y in points:
        terminal = ElectricalTerminal(ref=component.ref, pin=pin, unit=component.unit)
        global_position = _transform_point(
            local_x,
            local_y,
            origin_x=component.x,
            origin_y=component.y,
            rotation=component.rotation,
        )
        rounded = (round(global_position[0], 9), round(global_position[1], 9))
        result.setdefault(terminal, set()).add(rounded)
    return {terminal: tuple(sorted(positions)) for terminal, positions in sorted(result.items())}


def resolve_component_pin_positions(
    doc: SchematicDoc,
    component: PlacedSchematicComponent,
) -> dict[ElectricalTerminal, tuple[float, float]]:
    """Resolve one exact position per terminal for mutation.

    Unlike verification, mutation is not allowed to guess between multiple
    geometry sources.  A component with more than one supported position for a
    logical terminal is therefore ineligible for deterministic movement.
    """

    candidates = resolve_component_pin_position_candidates(doc, component)
    ambiguous = {
        terminal: positions for terminal, positions in candidates.items() if len(positions) != 1
    }
    if ambiguous:
        raise UserError(
            "Placed symbol exposes ambiguous pin geometry for deterministic mutation.",
            code="REFINEMENT_AMBIGUOUS_TARGET",
            details={
                "ref": component.ref,
                "unit": component.unit,
                "pins": {
                    terminal.pin: [list(position) for position in positions]
                    for terminal, positions in sorted(ambiguous.items())
                },
            },
        )
    return {terminal: positions[0] for terminal, positions in candidates.items()}


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

    terminal_positions: list[tuple[ElectricalTerminal, float, float]] = []
    for component in components:
        if _is_explicit_power_helper(component):
            continue
        candidates = resolve_component_pin_position_candidates(doc, component)
        for terminal, positions in candidates.items():
            for px, py in positions:
                terminal_positions.append((terminal, px, py))

    resolved: list[ElectricalTerminal] = []
    for x, y in points:
        matches = {
            terminal
            for terminal, px, py in terminal_positions
            if math.hypot(px - x, py - y) <= tolerance_mm
        }
        unique_matches = sorted(matches)
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

    result: list[tuple[str, float, float]] = []
    seen: set[tuple[str, float, float]] = set()
    for item in direct + nested:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return sorted(result)


def _external_library_pin_points(
    component: PlacedSchematicComponent,
) -> list[tuple[str, float, float]]:
    """Resolve all available source-library pin geometry candidates."""

    if ":" not in component.symbol_id:
        return []
    lib_name, symbol_name = component.symbol_id.split(":", 1)
    result: list[tuple[str, float, float]] = []
    for directory in resolve_symbol_dirs().dirs:
        unit_pin_at = read_lib_symbol_unit_pin_at(lib_name, symbol_name, symbols_dir=directory)
        if unit_pin_at:
            selected = unit_pin_at.get(component.unit)
            if selected:
                result.extend(
                    (pin, coords[0], coords[1]) for pin, coords in sorted(selected.items())
                )
            continue
        pin_at = read_lib_symbol_pin_at(lib_name, symbol_name, symbols_dir=directory)
        if pin_at:
            result.extend((pin, coords[0], coords[1]) for pin, coords in sorted(pin_at.items()))
    return sorted(set(result))


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
    """Transform library-space pin coordinates to schematic coordinates."""

    theta = math.radians(rotation)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    return (
        origin_x + cos_t * x - sin_t * y,
        origin_y - (sin_t * x + cos_t * y),
    )


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

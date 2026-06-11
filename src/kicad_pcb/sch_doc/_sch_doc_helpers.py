"""SchematicDoc module-level helper functions."""

from __future__ import annotations

import json

from ..errors import ParseError
from ..sexpr.nodes import AtomNode, ListNode, Node, StringNode

_BIND_PREFIXES = ("kicad-pcb:bind=", "OpenClaw:bind=")


def _get_sheet_uuid(sheet_node: ListNode) -> str | None:
    """Extract ``(uuid "value")`` from a ``(sheet ...)`` node."""
    for item in sheet_node.items:
        if (
            isinstance(item, ListNode)
            and item.key == "uuid"
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
        ):
            return item.items[1].value
    return None


def _sheet_property_value(sheet_node: ListNode, prop_name: str) -> str | None:
    """Return the value of a named ``(property ...)`` inside a ``(sheet ...)`` node."""
    for child in sheet_node.items:
        if not isinstance(child, ListNode) or child.key != "property":
            continue
        if len(child.items) < 3:
            continue
        name_node = child.items[1]
        value_node = child.items[2]
        if (
            isinstance(name_node, StringNode)
            and isinstance(value_node, StringNode)
            and name_node.value == prop_name
        ):
            return value_node.value
    return None


def _parse_float_atom(node: Node) -> float:
    """Parse a float from an :class:`AtomNode`.

    Raises :class:`ParseError` when the node is not an atom or the atom value
    is not numeric.
    """
    if not isinstance(node, AtomNode):
        raise ParseError("Malformed symbol (at ...) coordinate: expected numeric atom")
    try:
        return float(node.value)
    except ValueError as exc:
        raise ParseError(
            f"Malformed symbol (at ...) coordinate: expected numeric atom, got {node.value!r}"
        ) from exc


def _symbol_metadata(symbol_node: ListNode) -> dict[str, object]:
    """Extract placement metadata from a placed symbol ``(symbol ...)`` AST node.

    Returns a dict with keys ``ref``, ``symbol_id``, ``value``, ``uuid``,
    ``unit`` (all ``str``) and ``x``, ``y`` (both ``float``).
    """
    symbol_id = ""
    ref = ""
    value = ""
    sym_uuid = ""
    unit = ""
    x = 0.0
    y = 0.0
    rotation = 0.0

    for child in symbol_node.items:
        if not isinstance(child, ListNode):
            continue
        if (
            child.key == "lib_id"
            and len(child.items) >= 2
            and isinstance(child.items[1], StringNode)
        ):
            symbol_id = child.items[1].value
        elif (
            child.key == "uuid" and len(child.items) >= 2 and isinstance(child.items[1], StringNode)
        ):
            sym_uuid = child.items[1].value
        elif child.key == "unit" and len(child.items) >= 2 and isinstance(child.items[1], AtomNode):
            unit = child.items[1].value
        elif child.key == "at" and len(child.items) >= 3:
            x = _parse_float_atom(child.items[1])
            y = _parse_float_atom(child.items[2])
            if len(child.items) >= 4:
                rotation = _parse_float_atom(child.items[3])
        elif child.key == "property" and len(child.items) >= 3:
            name_node = child.items[1]
            value_node = child.items[2]
            if isinstance(name_node, StringNode) and isinstance(value_node, StringNode):
                if name_node.value == "Reference":
                    ref = value_node.value
                elif name_node.value == "Value":
                    value = value_node.value

    return {
        "ref": ref,
        "symbol_id": symbol_id,
        "value": value,
        "uuid": sym_uuid,
        "x": x,
        "y": y,
        "rotation": rotation,
        "unit": unit,
    }


def _parse_binding_marker(marker: str) -> dict[str, str] | None:
    """Parse a ``kicad-pcb:bind=<JSON>`` marker string into a ``{ref, pin, net_name}`` dict.

    Also accepts the legacy ``OpenClaw:bind=`` prefix for backward compatibility.
    Returns ``None`` when the marker is malformed or any required field is absent.
    """
    payload: str | None = next(
        (marker[len(p) :] for p in _BIND_PREFIXES if marker.startswith(p)), None
    )
    if payload is None:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    ref = data.get("ref")
    pin = data.get("pin")
    net_name = data.get("net_name")
    if not (
        isinstance(ref, str)
        and ref
        and isinstance(pin, str)
        and pin
        and isinstance(net_name, str)
        and net_name
    ):
        return None
    return {"ref": ref, "pin": pin, "net_name": net_name}

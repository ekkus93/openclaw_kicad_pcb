"""Schematic apply: symbol embedding, pin geometry resolution, no-connect markers."""

from __future__ import annotations

from .. import placeholder_symbol as _placeholder_mod
from ..circuit_ir import CircuitIR
from ..component_types import component_type
from ..fs import _new_uuid
from ..sch_doc import SchematicDoc, read_lib_symbol_def_flat
from ..symbol_index import SymbolIndex
from ._sch_apply_types import _PlacedSymbolSpec


def _resolve_placed_symbol_pin_at(
    symbol: str,
    placed_symbol: _PlacedSymbolSpec,
    symbol_index: SymbolIndex,
) -> dict[str, tuple[float, float, float]]:
    """Return unit-local pin geometry for a placed symbol when available."""
    unit_pin_at = symbol_index.get_unit_pin_at(symbol)
    if unit_pin_at:
        pin_at = unit_pin_at.get(str(placed_symbol.unit), {})
        if pin_at:
            return {
                pin_num: coords
                for pin_num, coords in pin_at.items()
                if pin_num in placed_symbol.pin_nums
            }

    if ":" not in symbol:
        return {}
    lib_name, sym_name = symbol.split(":", 1)
    for directory in symbol_index.directories:
        from ..sch_doc import read_lib_symbol_pin_at  # noqa: PLC0415

        pin_at = read_lib_symbol_pin_at(lib_name, sym_name, symbols_dir=directory)
        if pin_at:
            return {
                pin_num: coords
                for pin_num, coords in pin_at.items()
                if pin_num in placed_symbol.pin_nums
            }
    return {}


def _embed_symbol_if_found(
    *,
    doc: SchematicDoc,
    symbol: str,
    symbol_index: SymbolIndex,
    placeholder: _placeholder_mod.PlaceholderSymbol | None = None,
) -> bool:
    if ":" in symbol:
        lib_name, sym_name = symbol.split(":", 1)
        for directory in symbol_index.directories:
            sym_def = read_lib_symbol_def_flat(lib_name, sym_name, symbols_dir=directory)
            if sym_def is not None:
                doc.embed_lib_symbol(sym_def)
                return True
    if placeholder is not None:
        doc.embed_lib_symbol(placeholder.definition)
        return True
    return False


def _write_unused_connector_no_connects(
    *,
    doc: SchematicDoc,
    ir: CircuitIR,
    symbol_index: SymbolIndex,
    pin_endpoints: dict[tuple[str, str], tuple[float, float, float]],
) -> None:
    """Place KiCad no-connect markers on unused connector pins."""
    used_pins_by_ref: dict[str, set[str]] = {}
    for net in ir.nets:
        for pin_ref in net.pins:
            used_pins_by_ref.setdefault(pin_ref.ref, set()).add(pin_ref.pin)

    for component in ir.components:
        if component_type(component.ref) != "connector":
            continue

        all_pins = sorted(symbol_index.get_pins(component.symbol))
        used_pins = used_pins_by_ref.get(component.ref, set())
        for pin_num in all_pins:
            if pin_num in used_pins:
                continue
            endpoint = pin_endpoints.get((component.ref, pin_num))
            if endpoint is None:
                continue
            doc.add_no_connect(endpoint[0], endpoint[1], _new_uuid())

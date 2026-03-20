"""Semantic validation helpers for :mod:`kicad_pcb.circuit_ir`."""

from __future__ import annotations

from collections import Counter, defaultdict

from ..circuit_ir import CircuitIR
from ..errors import ErrorCode, UserError
from ..symbol_index import SymbolIndex


def validate_circuit_ir(ir: CircuitIR) -> None:
    """Validate semantic constraints that are not expressible via schema alone."""
    refs = [component.ref for component in ir.components]
    dup_refs = sorted([ref for ref, count in Counter(refs).items() if count > 1])
    if dup_refs:
        raise UserError(
            "Duplicate component references in Circuit IR",
            code=ErrorCode.IR_SEMANTIC_INVALID,
            details={"duplicate_refs": dup_refs},
        )

    net_names = [net.name for net in ir.nets]
    dup_nets = sorted([name for name, count in Counter(net_names).items() if count > 1])
    if dup_nets:
        raise UserError(
            "Duplicate net names in Circuit IR",
            code=ErrorCode.IR_SEMANTIC_INVALID,
            details={"duplicate_nets": dup_nets},
        )

    ref_set = {component.ref for component in ir.components}
    missing_refs: list[dict[str, str]] = []
    pin_to_net: dict[tuple[str, str], list[str]] = defaultdict(list)

    for net in ir.nets:
        if not net.pins:
            raise UserError(
                f"Net '{net.name}' has no pin members",
                code=ErrorCode.IR_SEMANTIC_INVALID,
                details={"net": net.name},
            )
        for pin_ref in net.pins:
            if pin_ref.ref not in ref_set:
                missing_refs.append({"net": net.name, "ref": pin_ref.ref, "pin": pin_ref.pin})
            pin_to_net[(pin_ref.ref, pin_ref.pin)].append(net.name)

    if missing_refs:
        raise UserError(
            "Pin references unknown component refs",
            code=ErrorCode.IR_SEMANTIC_INVALID,
            details={"missing_component_refs": missing_refs},
        )

    collisions = [
        {"ref": ref, "pin": pin, "nets": sorted(nets)}
        for (ref, pin), nets in pin_to_net.items()
        if len(nets) > 1
    ]
    if collisions:
        raise UserError(
            "A pin appears in multiple nets",
            code=ErrorCode.IR_SEMANTIC_INVALID,
            details={"pin_collisions": collisions},
        )


def validate_ir_symbols(ir: CircuitIR, symbol_index: SymbolIndex) -> None:
    """Validate symbol existence, pin existence, and explicit unit selection.

    Rules enforced:
    - Every component symbol must resolve in ``symbol_index``.
    - Every pin reference must be valid for its component symbol.
    - ``PinRefIR.unit`` may be used only when the symbol exposes KiCad unit metadata.
    - When ``PinRefIR.unit`` is present, the selected unit must exist and own the pin.
    """
    component_symbol_by_ref = {component.ref: component.symbol for component in ir.components}

    symbol_pins: dict[str, set[str]] = {}
    symbol_unit_pins: dict[str, dict[str, tuple[str, ...]]] = {}
    for sym_id in sorted(set(component_symbol_by_ref.values())):
        symbol_pins[sym_id] = symbol_index.get_pins(sym_id)
        symbol_unit_pins[sym_id] = symbol_index.get_unit_pins(sym_id)

    for net in ir.nets:
        for pin_ref in net.pins:
            symbol_id = component_symbol_by_ref.get(pin_ref.ref)
            if symbol_id is None:
                continue

            valid_pins = symbol_pins[symbol_id]
            if pin_ref.pin not in valid_pins:
                raise UserError(
                    (
                        f"Net {net.name} references {pin_ref.ref} pin {pin_ref.pin}, "
                        f"but {symbol_id} valid pins are {sorted(valid_pins)}"
                    ),
                    code=ErrorCode.PIN_INVALID,
                    details={
                        "net": net.name,
                        "ref": pin_ref.ref,
                        "pin": pin_ref.pin,
                        "symbol": symbol_id,
                        "valid_pins": sorted(valid_pins),
                    },
                )

            if pin_ref.unit is None:
                continue

            unit_pins = symbol_unit_pins[symbol_id]
            if not unit_pins:
                raise UserError(
                    "PinRef.unit was provided for a symbol without KiCad unit metadata",
                    code=ErrorCode.MULTI_UNIT_UNSUPPORTED,
                    details={
                        "net": net.name,
                        "ref": pin_ref.ref,
                        "pin": pin_ref.pin,
                        "unit": pin_ref.unit,
                        "symbol": symbol_id,
                    },
                )

            if pin_ref.unit not in unit_pins:
                raise UserError(
                    (
                        f"Net {net.name} references {pin_ref.ref} unit {pin_ref.unit}, "
                        f"but {symbol_id} valid units are {sorted(unit_pins)}"
                    ),
                    code=ErrorCode.IR_SEMANTIC_INVALID,
                    details={
                        "net": net.name,
                        "ref": pin_ref.ref,
                        "pin": pin_ref.pin,
                        "unit": pin_ref.unit,
                        "symbol": symbol_id,
                        "valid_units": sorted(unit_pins),
                    },
                )

            unit_valid_pins = unit_pins[pin_ref.unit]
            if pin_ref.pin not in unit_valid_pins:
                raise UserError(
                    (
                        f"Net {net.name} references {pin_ref.ref} pin {pin_ref.pin} on unit "
                        f"{pin_ref.unit}, but {symbol_id} unit {pin_ref.unit} valid pins are "
                        f"{sorted(unit_valid_pins)}"
                    ),
                    code=ErrorCode.PIN_INVALID,
                    details={
                        "net": net.name,
                        "ref": pin_ref.ref,
                        "pin": pin_ref.pin,
                        "unit": pin_ref.unit,
                        "symbol": symbol_id,
                        "valid_unit_pins": sorted(unit_valid_pins),
                    },
                )

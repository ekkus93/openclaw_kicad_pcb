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
    """Validate symbol existence, pin existence, and MVP unit constraints.

    Rules enforced:
    - Every component symbol must resolve in ``symbol_index``.
    - Every pin reference must be valid for its component symbol.
    - ``PinRefIR.unit`` must be ``None`` in MVP.
    """
    component_symbol_by_ref = {component.ref: component.symbol for component in ir.components}

    symbol_pins: dict[str, set[str]] = {}
    for sym_id in sorted(set(component_symbol_by_ref.values())):
        symbol_pins[sym_id] = symbol_index.get_pins(sym_id)

    for net in ir.nets:
        for pin_ref in net.pins:
            if pin_ref.unit is not None:
                raise UserError(
                    "PinRef.unit is not supported in v1",
                    code=ErrorCode.MULTI_UNIT_UNSUPPORTED,
                    details={
                        "net": net.name,
                        "ref": pin_ref.ref,
                        "pin": pin_ref.pin,
                        "unit": pin_ref.unit,
                    },
                )

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

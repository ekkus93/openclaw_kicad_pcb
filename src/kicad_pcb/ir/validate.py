"""Semantic validation helpers for :mod:`kicad_pcb.circuit_ir`."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import TypeAlias

from ..circuit_ir import CircuitIR
from ..errors import ErrorCode, UserError
from ..symbol_index import SymbolIndex

PinMembershipKey: TypeAlias = tuple[str, str]


@dataclass(frozen=True)
class PinMembershipAssignment:
    """One traceable `(ref, pin)` assignment within a canonical IR net."""

    net_name: str
    ref: str
    pin: str
    unit: str | None
    net_index: int
    pin_index: int


PinMembershipIndex: TypeAlias = dict[
    PinMembershipKey,
    tuple[PinMembershipAssignment, ...],
]


def build_pin_membership_index(ir: CircuitIR) -> PinMembershipIndex:
    """Return a canonical `(ref, pin)` membership index for *ir*."""
    assignments: dict[PinMembershipKey, list[PinMembershipAssignment]] = defaultdict(list)
    for net_index, net in enumerate(ir.nets):
        for pin_index, pin_ref in enumerate(net.pins):
            assignments[(pin_ref.ref, pin_ref.pin)].append(
                PinMembershipAssignment(
                    net_name=net.name,
                    ref=pin_ref.ref,
                    pin=pin_ref.pin,
                    unit=pin_ref.unit,
                    net_index=net_index,
                    pin_index=pin_index,
                )
            )
    return {key: tuple(value) for key, value in assignments.items()}


def find_pin_membership_collisions(index: PinMembershipIndex) -> list[dict[str, object]]:
    """Return traceable collisions for pins assigned more than once."""
    collisions: list[dict[str, object]] = []
    for (ref, pin), assignments in sorted(index.items()):
        if len(assignments) < 2:
            continue
        collisions.append(
            {
                "ref": ref,
                "pin": pin,
                "nets": sorted({assignment.net_name for assignment in assignments}),
                "assignments": [
                    {
                        "net": assignment.net_name,
                        "unit": assignment.unit,
                        "net_index": assignment.net_index,
                        "pin_index": assignment.pin_index,
                    }
                    for assignment in assignments
                ],
            }
        )
    return collisions


def validate_circuit_ir(ir: CircuitIR) -> None:
    """Validate semantic constraints that are not expressible via schema alone."""
    # Symbol IDs must be fully qualified as "LibraryName:PartName"
    unqualified = sorted(
        [{"ref": c.ref, "symbol": c.symbol} for c in ir.components if ":" not in c.symbol],
        key=lambda d: d["ref"],
    )
    if unqualified:
        raise UserError(
            "Component symbols must use 'LibraryName:PartName' format",
            code=ErrorCode.IR_SEMANTIC_INVALID,
            details={"unqualified_symbols": unqualified},
        )

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

    for net in ir.nets:
        if not net.pins:
            raise UserError(
                f"Net '{net.name}' has no pin members",
                code=ErrorCode.IR_SEMANTIC_INVALID,
                details={"net": net.name},
            )

    membership_index = build_pin_membership_index(ir)
    missing_refs = [
        {
            "net": assignment.net_name,
            "ref": assignment.ref,
            "pin": assignment.pin,
        }
        for assignments in membership_index.values()
        for assignment in assignments
        if assignment.ref not in ref_set
    ]

    if missing_refs:
        raise UserError(
            "Pin references unknown component refs",
            code=ErrorCode.IR_SEMANTIC_INVALID,
            details={"missing_component_refs": missing_refs},
        )

    collisions = find_pin_membership_collisions(membership_index)
    if collisions:
        raise UserError(
            "A pin appears in multiple nets",
            code=ErrorCode.IR_SEMANTIC_INVALID,
            details={"pin_collisions": collisions},
        )


@dataclass(frozen=True)
class IrSymbolValidationResult:
    """Result of validate_ir_symbols, separating hard errors from missing symbols.

    Generation can continue for unknown symbols by registering a placeholder
    with SymbolIndex and emitting a SYMBOL_PLACEHOLDER_USED advisory warning.
    """

    unknown_symbols: dict[str, frozenset[str]] = field(default_factory=dict)
    """symbol_id -> frozenset of pin numbers derived from the IR for every
    symbol not found in any library.  The pin set is exactly the set of pins
    referenced in the IR nets — never invented."""


def validate_ir_symbols(
    ir: CircuitIR,
    symbol_index: SymbolIndex,
) -> IrSymbolValidationResult:
    """Validate symbol existence, pin existence, and explicit unit selection.

    Rules enforced:
    - Every component symbol must resolve in ``symbol_index`` OR be recorded
      as an unknown symbol in the returned result (downgraded from hard error).
    - Every pin reference must be valid for its component symbol.
    - ``PinRefIR.unit`` may be used only when the symbol exposes KiCad unit metadata.
    - When ``PinRefIR.unit`` is present, the selected unit must exist and own the pin.

    Returns
    -------
    IrSymbolValidationResult
        Contains ``unknown_symbols`` mapping each symbol not found in any
        library to the pin set derived from the IR nets.  Callers should
        build a :class:`~kicad_pcb.placeholder_symbol.PlaceholderSymbol` for
        each entry and register it with the SymbolIndex before generation.
    """
    component_symbol_by_ref = {component.ref: component.symbol for component in ir.components}

    symbol_pins: dict[str, set[str]] = {}
    symbol_unit_pins: dict[str, dict[str, tuple[str, ...]]] = {}
    unknown_symbols: dict[str, frozenset[str]] = {}

    for sym_id in sorted(set(component_symbol_by_ref.values())):
        try:
            symbol_pins[sym_id] = symbol_index.get_pins(sym_id)
            symbol_unit_pins[sym_id] = symbol_index.get_unit_pins(sym_id)
        except UserError as exc:
            if exc.code != ErrorCode.SYMBOL_NOT_FOUND:
                raise
            # Derive the usable pin set directly from the IR nets so that
            # the pin-validity checks below still run against what the IR
            # declared, and callers can build a placeholder with exactly
            # the right pins.
            ir_pins: frozenset[str] = frozenset(
                pr.pin
                for net in ir.nets
                for pr in net.pins
                if component_symbol_by_ref.get(pr.ref) == sym_id
            )
            unknown_symbols[sym_id] = ir_pins
            symbol_pins[sym_id] = set(ir_pins)
            symbol_unit_pins[sym_id] = {}  # single-unit placeholder

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

    return IrSymbolValidationResult(unknown_symbols=unknown_symbols)

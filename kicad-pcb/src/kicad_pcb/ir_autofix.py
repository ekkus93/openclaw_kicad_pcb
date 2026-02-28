"""Deterministic auto-fixer for common Circuit IR problems.

Fixes are applied in layers without any LLM calls:

1. Schema layer — top-level structure, version type, forbidden keys, wrappers.
2. Component layer — remove forbidden fields (``type``, inline ``pins``).
3. Net layer — integer pin values coerced to strings.
4. Alias layer — wrong pin names mapped to correct ones via alias table and
   library lookup (requires a :class:`~kicad_pcb.symbol_index.SymbolIndex`).

All functions return the fixed data alongside a list of human-readable fix
descriptions so callers can report exactly what changed.  Errors that
*cannot* be auto-fixed are collected in ``remaining_errors``.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .symbol_index import SymbolIndex

# ---------------------------------------------------------------------------
# Alias table
# ---------------------------------------------------------------------------
# Each entry is (WRONG_PIN_UPPER, CORRECT_PIN).  The correct pin is only
# applied when it actually exists in the symbol's valid-pin set.

_PIN_ALIAS_TABLE: list[tuple[str, str]] = [
    # Polarity / math notation (polarised caps, electrolytics, etc.)
    ("+", "1"),
    ("-", "2"),
    ("PLUS", "1"),
    ("MINUS", "2"),
    ("POSITIVE", "1"),
    ("NEGATIVE", "2"),
    ("POS", "1"),
    ("NEG", "2"),
    # Audio jack full-word → KiCad single-letter identifiers
    ("TIP", "T"),
    ("RING", "R"),
    ("SLEEVE", "S"),
    # Common labelling mistakes for power symbols (single pin = "1")
    ("PWR", "1"),
    ("VCC", "1"),
    ("GND", "1"),
    ("~", "1"),
]

# Top-level keys that are valid in CircuitIR (including the optional "options")
_ALLOWED_TOP_LEVEL: frozenset[str] = frozenset({"version", "components", "nets", "options"})

# Component fields that CircuitIR accepts
_ALLOWED_COMPONENT_FIELDS: frozenset[str] = frozenset(
    {"ref", "symbol", "value", "footprint", "fields"}
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _try_resolve_alias(wrong_pin: str, valid_pins: set[str]) -> str | None:
    """Return a corrected pin name for *wrong_pin*, or ``None`` if unresolvable.

    Resolution order:
    1. Exact match (shouldn't reach here, but defensive).
    2. Case-insensitive match against valid pins.
    3. Alias table lookup (case-insensitive on both sides).
    """
    if wrong_pin in valid_pins:
        return wrong_pin  # already correct

    upper = wrong_pin.upper()

    # Case-insensitive
    for vp in valid_pins:
        if vp.upper() == upper:
            return vp

    # Alias table
    for alias_from, alias_to in _PIN_ALIAS_TABLE:
        if alias_from.upper() == upper and alias_to in valid_pins:
            return alias_to

    return None


# ---------------------------------------------------------------------------
# Layer 1: schema fixes
# ---------------------------------------------------------------------------


def _fix_schema(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Fix common top-level structural errors."""
    fixes: list[str] = []
    result: dict[str, Any] = copy.deepcopy(raw)

    # Unwrap outer metadata shells that LLMs tend to generate
    for wrapper_key in ("metadata", "meta", "circuit", "data", "header"):
        if wrapper_key in result and isinstance(result[wrapper_key], dict):
            wrapper = result.pop(wrapper_key)
            fixes.append(f'removed forbidden wrapper key "{wrapper_key}"')
            for k in ("components", "nets", "version", "options"):
                if k in wrapper and k not in result:
                    result[k] = wrapper[k]
                    fixes.append(f"promoted {wrapper_key}.{k} → top-level {k}")

    # Fix version: integer 1 → string "1", or missing → "1"
    if result.get("version") != "1":
        fixes.append(f'version: changed {result.get("version")!r} → "1"')
        result["version"] = "1"

    # Remove unknown top-level keys
    for k in list(result.keys()):
        if k not in _ALLOWED_TOP_LEVEL:
            del result[k]
            fixes.append(f'removed unknown top-level key "{k}"')

    # Ensure arrays exist (avoid KeyError in later layers)
    for key in ("components", "nets"):
        if key not in result or not isinstance(result[key], list):
            result[key] = result.get(key) or []
            fixes.append(f'ensured "{key}" is an array')

    return result, fixes


# ---------------------------------------------------------------------------
# Layer 2: component field fixes
# ---------------------------------------------------------------------------


def _fix_components(
    raw_components: list[Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Remove forbidden fields from component objects."""
    fixes: list[str] = []
    fixed: list[dict[str, Any]] = []

    for comp in raw_components:
        if not isinstance(comp, dict):
            continue
        ref = comp.get("ref", "?")
        fc: dict[str, Any] = {}
        for k, v in comp.items():
            if k in _ALLOWED_COMPONENT_FIELDS:
                fc[k] = v
            else:
                fixes.append(f'{ref}: removed forbidden component field "{k}"')
        fixed.append(fc)

    return fixed, fixes


# ---------------------------------------------------------------------------
# Layer 3: net pin type fixes
# ---------------------------------------------------------------------------


def _fix_net_pin_types(
    raw_nets: list[Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Coerce integer pin values inside nets to strings."""
    fixes: list[str] = []
    fixed: list[dict[str, Any]] = []

    for net in raw_nets:
        if not isinstance(net, dict):
            continue
        net_name = net.get("name", "?")
        fn: dict[str, Any] = {k: v for k, v in net.items() if k != "pins"}
        fixed_pins: list[dict[str, Any]] = []
        for pin_ref in net.get("pins") or []:
            if not isinstance(pin_ref, dict):
                continue
            fpr = dict(pin_ref)
            if isinstance(fpr.get("pin"), int):
                old = fpr["pin"]
                fpr["pin"] = str(old)
                fixes.append(
                    f'net "{net_name}" {pin_ref.get("ref", "?")} '
                    f'pin {old} (int) → "{fpr["pin"]}" (str)'
                )
            fixed_pins.append(fpr)
        fn["pins"] = fixed_pins
        fixed.append(fn)

    return fixed, fixes


# ---------------------------------------------------------------------------
# Layer 4: pin alias fixes
# ---------------------------------------------------------------------------


def _fix_pin_aliases(
    ir_dict: dict[str, Any],
    symbol_index: SymbolIndex,
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Fix wrong pin names using alias table + library lookup.

    Returns ``(fixed_dict, fixes_applied, unresolvable_errors)``.
    """
    fixes: list[str] = []
    errors: list[str] = []
    result = copy.deepcopy(ir_dict)

    # Build component → symbol map
    sym_by_ref: dict[str, str] = {
        c["ref"]: c["symbol"]
        for c in result.get("components", [])
        if isinstance(c, dict) and "ref" in c and "symbol" in c
    }

    # Cache valid pins per symbol
    pin_cache: dict[str, set[str]] = {}
    for _sid in set(sym_by_ref.values()):
        try:
            pin_cache[_sid] = symbol_index.get_pins(_sid)
        except Exception:
            pin_cache[_sid] = set()  # unknown symbol; skip alias fix for it

    # Walk nets and fix wrong pins in-place
    for net in result.get("nets", []):
        if not isinstance(net, dict):
            continue
        net_name = net.get("name", "?")
        for pin_ref in net.get("pins") or []:
            if not isinstance(pin_ref, dict):
                continue
            ref = pin_ref.get("ref", "")
            pin = pin_ref.get("pin", "")
            sym_id = sym_by_ref.get(ref)
            if sym_id is None:
                continue
            valid_pins = pin_cache.get(sym_id, set())
            if not valid_pins or pin in valid_pins:
                continue  # unknown or already correct

            corrected = _try_resolve_alias(str(pin), valid_pins)
            if corrected is not None:
                fixes.append(
                    f'net "{net_name}" {ref} pin "{pin}" → "{corrected}" '
                    f"(valid pins for {sym_id}: {sorted(valid_pins)})"
                )
                pin_ref["pin"] = corrected
            else:
                errors.append(
                    f'net "{net_name}" {ref} pin "{pin}" is invalid for {sym_id} '
                    f"(valid pins: {sorted(valid_pins)}) — cannot auto-fix"
                )

    return result, fixes, errors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AutofixOutcome:
    """Internal result of the auto-fix pipeline."""

    #: The (potentially fixed) dict ready for :class:`~kicad_pcb.circuit_ir.CircuitIR` parsing.
    ir_dict: dict[str, Any]
    #: Human-readable descriptions of every change applied.
    fixes_applied: tuple[str, ...] = field(default_factory=tuple)
    #: Errors that could not be resolved deterministically.
    remaining_errors: tuple[str, ...] = field(default_factory=tuple)


def autofix_circuit_ir(
    raw: dict[str, Any],
    *,
    symbol_index: SymbolIndex | None = None,
) -> AutofixOutcome:
    """Apply all deterministic fix layers to a raw Circuit IR dict.

    Layers run unconditionally and in order.  Errors that cannot be resolved
    deterministically are collected in :attr:`AutofixOutcome.remaining_errors`
    rather than raised — callers decide how to report them.

    Parameters
    ----------
    raw:
        Parsed (but potentially malformed) JSON dict.
    symbol_index:
        When provided, layer 4 (pin alias resolution) is also run.
        Pass ``None`` to skip alias fixes (e.g. when no library is available).
    """
    all_fixes: list[str] = []
    all_errors: list[str] = []

    # -- Layer 1: schema --
    data, schema_fixes = _fix_schema(raw)
    all_fixes.extend(schema_fixes)

    # -- Layer 2: component fields --
    if isinstance(data.get("components"), list):
        data["components"], comp_fixes = _fix_components(data["components"])
        all_fixes.extend(comp_fixes)

    # -- Layer 3: net pin types --
    if isinstance(data.get("nets"), list):
        data["nets"], net_fixes = _fix_net_pin_types(data["nets"])
        all_fixes.extend(net_fixes)

    # -- Layer 4: pin aliases (library-dependent) --
    if symbol_index is not None:
        data, alias_fixes, alias_errors = _fix_pin_aliases(data, symbol_index)
        all_fixes.extend(alias_fixes)
        all_errors.extend(alias_errors)

    return AutofixOutcome(
        ir_dict=data,
        fixes_applied=tuple(all_fixes),
        remaining_errors=tuple(all_errors),
    )

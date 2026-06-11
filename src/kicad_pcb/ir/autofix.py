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

from dataclasses import dataclass, field
from typing import Any

from ._autofix_layers import (  # noqa: F401
    _fix_components,
    _fix_net_membership_keys,
    _fix_net_pin_types,
    _fix_options,
    _fix_pin_aliases,
    _fix_power_net_pin_duplicates,
    _fix_schema,
    _is_power_net,
    _parse_pin_membership_token,
    _SymbolLookupProtocol,
)
from ._autofix_legacy import _convert_legacy_connection_format  # noqa: F401


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
    symbol_index: _SymbolLookupProtocol | None = None,
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

    # -- Layer 1b: legacy side-format conversion --
    data, legacy_fixes, legacy_errors = _convert_legacy_connection_format(data)
    all_fixes.extend(legacy_fixes)
    all_errors.extend(legacy_errors)

    # -- Layer 2: component fields --
    if isinstance(data.get("components"), list):
        data["components"], comp_fixes = _fix_components(data["components"])
        all_fixes.extend(comp_fixes)

    # -- Layer 3: net pin types --
    if isinstance(data.get("nets"), list):
        data["nets"], membership_fixes = _fix_net_membership_keys(data["nets"])
        all_fixes.extend(membership_fixes)
        data["nets"], net_fixes = _fix_net_pin_types(data["nets"])
        all_fixes.extend(net_fixes)

    # -- Layer 3b: power-net pin deduplication --
    if isinstance(data.get("nets"), list):
        data["nets"], power_dedup_fixes = _fix_power_net_pin_duplicates(data["nets"])
        all_fixes.extend(power_dedup_fixes)

    fixed_options, option_fixes = _fix_options(data.get("options"))
    all_fixes.extend(option_fixes)
    if fixed_options is None:
        data.pop("options", None)
    else:
        data["options"] = fixed_options

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

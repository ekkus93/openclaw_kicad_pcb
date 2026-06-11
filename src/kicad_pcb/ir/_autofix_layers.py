"""Deterministic fix layers: schema, components, nets, options, power dedup, pin aliases."""

from __future__ import annotations

import copy
from typing import Any, Protocol

from ..errors import UserError
from ._autofix_legacy import _infer_component_symbol, _try_resolve_alias

# Top-level keys that are valid in CircuitIR (including the optional "options")
_ALLOWED_TOP_LEVEL: frozenset[str] = frozenset({"version", "components", "nets", "options"})

# Component fields that CircuitIR accepts
_ALLOWED_COMPONENT_FIELDS: frozenset[str] = frozenset(
    {"ref", "symbol", "value", "footprint", "fields"}
)

_ALLOWED_OPTION_FIELDS: frozenset[str] = frozenset(
    {"tech", "default_res_package", "default_cap_package", "power_net_names"}
)

_POWER_NET_EXACT: frozenset[str] = frozenset(
    {
        "gnd",
        "vcc",
        "vss",
        "vdd",
        "pwr",
        "power",
        "agnd",
        "dgnd",
        "pgnd",
        "avcc",
        "dvcc",
        "avdd",
        "dvdd",
    }
)


class _SymbolLookupProtocol(Protocol):
    """Structural interface for symbol pin lookups used by autofix."""

    def get_pins(self, symbol_id: str) -> set[str]: ...


def _fix_schema(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Fix common top-level structural errors."""
    fixes: list[str] = []
    result: dict[str, Any] = copy.deepcopy(raw)

    for wrapper_key in ("metadata", "meta", "circuit", "data", "header"):
        if wrapper_key in result and isinstance(result[wrapper_key], dict):
            wrapper = result.pop(wrapper_key)
            fixes.append(f'removed forbidden wrapper key "{wrapper_key}"')
            for k in ("components", "nets", "version", "options"):
                if k in wrapper and k not in result:
                    result[k] = wrapper[k]
                    fixes.append(f"promoted {wrapper_key}.{k} → top-level {k}")

    if result.get("version") != "1":
        fixes.append(f'version: changed {result.get("version")!r} → "1"')
        result["version"] = "1"

    for k in list(result.keys()):
        if k not in _ALLOWED_TOP_LEVEL:
            del result[k]
            fixes.append(f'removed unknown top-level key "{k}"')

    for key in ("components", "nets"):
        if key not in result or not isinstance(result[key], list):
            result[key] = result.get(key) or []
            fixes.append(f'ensured "{key}" is an array')

    return result, fixes


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
        inferred_symbol = _infer_component_symbol(fc)
        if inferred_symbol is not None and fc.get("symbol") != inferred_symbol:
            fc["symbol"] = inferred_symbol
            fixes.append(f'{ref}: inferred missing symbol as "{inferred_symbol}"')
        fixed.append(fc)

    return fixed, fixes


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
            if isinstance(pin_ref, str):
                parsed = _parse_pin_membership_token(pin_ref)
                if parsed is None:
                    continue
                fpr = parsed
                fixes.append(
                    f'net "{net_name}": converted compact pin token "{pin_ref}" into ref/pin object'
                )
            elif isinstance(pin_ref, dict):
                fpr = dict(pin_ref)
            else:
                continue
            if isinstance(fpr.get("pin"), int):
                old = fpr["pin"]
                fpr["pin"] = str(old)
                fixes.append(
                    f'net "{net_name}" {fpr.get("ref", "?")} pin {old} (int) → "{fpr["pin"]}" (str)'
                )
            fixed_pins.append(fpr)
        fn["pins"] = fixed_pins
        fixed.append(fn)

    return fixed, fixes


def _parse_pin_membership_token(token: str) -> dict[str, str] | None:
    """Parse compact membership tokens like ``U1.8`` into ref/pin objects."""
    compact_token = token.strip()
    if not compact_token:
        return None

    for separator in (".", ":", "-"):
        if separator not in compact_token:
            continue
        ref, pin = compact_token.rsplit(separator, 1)
        ref = ref.strip()
        pin = pin.strip()
        if ref and pin:
            return {"ref": ref, "pin": pin}

    return None


def _fix_net_membership_keys(
    raw_nets: list[Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalize common net membership key mistakes such as ``nodes`` -> ``pins``."""
    fixes: list[str] = []
    fixed: list[dict[str, Any]] = []

    for net in raw_nets:
        if not isinstance(net, dict):
            continue
        net_name = str(net.get("name", "?"))
        fixed_net = copy.deepcopy(net)
        pins = fixed_net.get("pins")
        nodes = fixed_net.get("nodes")
        if not isinstance(pins, list) and isinstance(nodes, list):
            fixed_net["pins"] = nodes
            fixes.append(f'net "{net_name}": renamed membership key "nodes" -> "pins"')
        if "nodes" in fixed_net:
            del fixed_net["nodes"]
            if isinstance(pins, list):
                fixes.append(f'net "{net_name}": removed forbidden membership key "nodes"')
        fixed.append(fixed_net)

    return fixed, fixes


def _fix_options(options: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """Remove unsupported option keys and drop empty option objects."""
    if not isinstance(options, dict):
        return None, []

    fixes: list[str] = []
    fixed_options: dict[str, Any] = {}
    for key, value in options.items():
        if key in _ALLOWED_OPTION_FIELDS:
            fixed_options[key] = value
        else:
            fixes.append(f'options: removed unsupported key "{key}"')

    return (fixed_options or None), fixes


def _is_power_net(name: str) -> bool:
    """Return True if *name* looks like a power/ground rail net."""
    n = name.strip().lower()
    if n in _POWER_NET_EXACT:
        return True
    return len(n) >= 3 and n[0] in ("+", "-") and n[-1] == "v"


def _fix_power_net_pin_duplicates(
    raw_nets: list[Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Remove pins from power nets when the same pin also appears in a signal net.

    When the LLM generates IR that places a component pin in both a power rail
    (e.g. GND) and a separate signal net (e.g. RED_LED_NODE), the correct
    interpretation is that the pin belongs to the signal net — the power
    connection is made through the circuit path, not by direct power-net
    membership.  This is a systematic LLM mistake for LED cathodes,
    resistor/capacitor ends on power rails, and similar nodes.

    The fix: when a (ref, pin) pair appears in at least one power net AND at
    least one non-power (signal) net, remove the pair from every power net it
    appears in.  If a power net becomes empty as a result, it is also dropped.
    """
    fixes: list[str] = []

    membership: dict[tuple[str, str], list[str]] = {}
    for net in raw_nets:
        if not isinstance(net, dict):
            continue
        net_name = str(net.get("name", ""))
        for pin_ref in net.get("pins") or []:
            if not isinstance(pin_ref, dict):
                continue
            ref = str(pin_ref.get("ref", ""))
            pin = str(pin_ref.get("pin", ""))
            if ref and pin:
                membership.setdefault((ref, pin), []).append(net_name)

    to_remove: dict[str, set[tuple[str, str]]] = {}
    for (ref, pin), net_names in membership.items():
        power = [n for n in net_names if _is_power_net(n)]
        signal = [n for n in net_names if not _is_power_net(n)]
        if not (power and signal):
            continue
        for pnet in power:
            to_remove.setdefault(pnet, set()).add((ref, pin))
        for pnet in power:
            kept = signal[0] if len(signal) == 1 else f"[{', '.join(signal)}]"
            fixes.append(
                f'{ref} pin {pin}: removed from power net "{pnet}" '
                f'(pin belongs to signal net "{kept}")'
            )

    if not to_remove:
        return [net for net in raw_nets if isinstance(net, dict)], []

    result: list[dict[str, Any]] = []
    for net in raw_nets:
        if not isinstance(net, dict):
            continue
        net_name = str(net.get("name", ""))
        removals = to_remove.get(net_name)
        if removals is None:
            result.append(net)
            continue
        kept_pins = [
            pr
            for pr in (net.get("pins") or [])
            if isinstance(pr, dict)
            and (str(pr.get("ref", "")), str(pr.get("pin", ""))) not in removals
        ]
        if not kept_pins:
            fixes.append(f'net "{net_name}": dropped (became empty after deduplication)')
            continue
        result.append({**net, "pins": kept_pins})

    return result, fixes


def _fix_pin_aliases(
    ir_dict: dict[str, Any],
    symbol_index: _SymbolLookupProtocol,
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Fix wrong pin names using alias table + library lookup.

    Returns ``(fixed_dict, fixes_applied, unresolvable_errors)``.
    """
    fixes: list[str] = []
    errors: list[str] = []
    result = copy.deepcopy(ir_dict)

    sym_by_ref: dict[str, str] = {
        c["ref"]: c["symbol"]
        for c in result.get("components", [])
        if isinstance(c, dict) and "ref" in c and "symbol" in c
    }

    pin_cache: dict[str, set[str]] = {}
    for _sid in set(sym_by_ref.values()):
        try:
            pin_cache[_sid] = symbol_index.get_pins(_sid)
        except UserError as exc:
            pin_cache[_sid] = set()
            errors.append(f'symbol "{_sid}" pin lookup failed: {exc}')

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
                continue

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

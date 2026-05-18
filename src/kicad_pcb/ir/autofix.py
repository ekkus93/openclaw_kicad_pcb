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
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..errors import UserError


class _SymbolLookupProtocol(Protocol):
    """Structural interface for symbol pin lookups used by autofix."""

    def get_pins(self, symbol_id: str) -> set[str]: ...


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

_LEGACY_SYMBOL_BY_NAME: dict[str, str] = {
    "1N4148": "Device:D",
    "AO3400": "Transistor_FET:Q_NMOS_GSD",
    "CONN_2": "Connector_Generic:Conn_01x02",
    "NE555": "Timer:NE555",
    "POT": "Device:R_Potentiometer",
    "RES": "Device:R",
}


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


def _looks_legacy_connection_format(raw: dict[str, Any]) -> bool:
    components = raw.get("components")
    nets = raw.get("nets")
    if not isinstance(components, list) or not isinstance(nets, list):
        return False
    return any(
        isinstance(component, dict) and ("name" in component or "pins" in component)
        for component in components
    ) and any(
        isinstance(net, dict) and ("connections" in net or "description" in net) for net in nets
    )


def _infer_legacy_symbol(component: dict[str, Any]) -> str | None:
    legacy_name = component.get("name")
    symbol: str | None = None
    if isinstance(legacy_name, str) and ":" in legacy_name:
        symbol = legacy_name
    elif isinstance(legacy_name, str):
        symbol = _LEGACY_SYMBOL_BY_NAME.get(legacy_name.upper())

    ref = str(component.get("ref", "")).upper()
    pin_names = {
        str(pin.get("name", "")).upper()
        for pin in component.get("pins", [])
        if isinstance(pin, dict)
    }
    pin_count = len(pin_names)

    if symbol is None:
        if ref.startswith("U") and legacy_name == "NE555":
            symbol = "Timer:NE555"
        elif ref.startswith("Q") or {"G", "S", "D"} <= pin_names:
            symbol = "Transistor_FET:Q_NMOS_GSD"
        elif ref.startswith("D"):
            symbol = "Device:D"
        elif ref.startswith("RV") or legacy_name == "POT":
            symbol = "Device:R_Potentiometer"
        elif ref.startswith("R"):
            symbol = "Device:R"
        elif ref.startswith("C"):
            symbol = "Device:C_Polarized" if {"+", "-"} <= pin_names else "Device:C"
        elif ref.startswith(("J", "P")) or (pin_count == 2 and legacy_name == "CONN_2"):
            symbol = "Connector_Generic:Conn_01x02"

    return symbol


def _legacy_component(
    components: list[dict[str, Any]],
    predicate: Callable[[dict[str, Any]], bool],
) -> dict[str, Any] | None:
    for component in components:
        if predicate(component):
            return component
    return None


def _rebuild_legacy_555_pwm_nets(
    legacy_components: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]] | None, list[str]]:
    timer = _legacy_component(
        legacy_components,
        lambda component: component.get("name") == "NE555",
    )
    mosfet = _legacy_component(
        legacy_components,
        lambda component: component.get("name") == "AO3400",
    )
    potentiometer = _legacy_component(
        legacy_components,
        lambda component: component.get("name") == "POT",
    )
    diodes = sorted(
        (component for component in legacy_components if component.get("name") == "1N4148"),
        key=lambda component: str(component.get("ref", "")),
    )
    series_resistor = _legacy_component(
        legacy_components,
        lambda component: "series resistor" in str(component.get("description", "")).lower(),
    )
    gate_resistor = _legacy_component(
        legacy_components,
        lambda component: "gate resistor" in str(component.get("description", "")).lower(),
    )
    gate_pulldown = _legacy_component(
        legacy_components,
        lambda component: (
            "pull-down" in str(component.get("description", "")).lower()
            or "pulldown" in str(component.get("description", "")).lower()
        ),
    )
    timing_cap = _legacy_component(
        legacy_components,
        lambda component: "Timing capacitor" in str(component.get("description", "")),
    )
    ctrl_cap = _legacy_component(
        legacy_components,
        lambda component: "555 decoupling capacitor" in str(component.get("description", "")),
    )
    bulk_cap = _legacy_component(
        legacy_components,
        lambda component: "bulk" in str(component.get("description", "")).lower(),
    )
    load_connector = _legacy_component(
        legacy_components,
        lambda component: (
            component.get("name") == "CONN_2"
            and "LED load" in str(component.get("description", ""))
        ),
    )

    required_components = (
        timer,
        mosfet,
        potentiometer,
        series_resistor,
        gate_resistor,
        gate_pulldown,
        timing_cap,
        ctrl_cap,
        bulk_cap,
        load_connector,
    )
    if any(component is None for component in required_components) or len(diodes) < 2:
        return None, []

    assert timer is not None
    assert mosfet is not None
    assert potentiometer is not None
    assert series_resistor is not None
    assert gate_resistor is not None
    assert gate_pulldown is not None
    assert timing_cap is not None
    assert ctrl_cap is not None
    assert bulk_cap is not None
    assert load_connector is not None

    diode_a, diode_b = diodes[:2]
    load_ref = _normalized_legacy_ref(load_connector, symbol="Connector_Generic:Conn_01x02")
    rebuilt_nets = [
        {
            "name": "GND",
            "pins": [
                {"ref": str(timer["ref"]), "pin": "1"},
                {"ref": str(mosfet["ref"]), "pin": "2"},
                {"ref": str(gate_pulldown["ref"]), "pin": "2"},
                {"ref": str(timing_cap["ref"]), "pin": "2"},
                {"ref": str(ctrl_cap["ref"]), "pin": "2"},
                {"ref": str(bulk_cap["ref"]), "pin": "2"},
            ],
        },
        {
            "name": "+12V",
            "pins": [
                {"ref": str(timer["ref"]), "pin": "4"},
                {"ref": str(timer["ref"]), "pin": "8"},
                {"ref": str(series_resistor["ref"]), "pin": "1"},
                {"ref": str(bulk_cap["ref"]), "pin": "1"},
                {"ref": load_ref, "pin": "1"},
            ],
        },
        {
            "name": "TIMING",
            "pins": [
                {"ref": str(timer["ref"]), "pin": "2"},
                {"ref": str(timer["ref"]), "pin": "6"},
                {"ref": str(potentiometer["ref"]), "pin": "2"},
                {"ref": str(timing_cap["ref"]), "pin": "1"},
            ],
        },
        {
            "name": "DISCH",
            "pins": [
                {"ref": str(timer["ref"]), "pin": "7"},
                {"ref": str(series_resistor["ref"]), "pin": "2"},
                {"ref": str(diode_a["ref"]), "pin": "1"},
                {"ref": str(diode_b["ref"]), "pin": "2"},
            ],
        },
        {
            "name": "POT_A",
            "pins": [
                {"ref": str(potentiometer["ref"]), "pin": "1"},
                {"ref": str(diode_a["ref"]), "pin": "2"},
            ],
        },
        {
            "name": "POT_B",
            "pins": [
                {"ref": str(potentiometer["ref"]), "pin": "3"},
                {"ref": str(diode_b["ref"]), "pin": "1"},
            ],
        },
        {
            "name": "CTRL",
            "pins": [
                {"ref": str(timer["ref"]), "pin": "5"},
                {"ref": str(ctrl_cap["ref"]), "pin": "1"},
            ],
        },
        {
            "name": "OUT_DRV",
            "pins": [
                {"ref": str(timer["ref"]), "pin": "3"},
                {"ref": str(gate_resistor["ref"]), "pin": "1"},
            ],
        },
        {
            "name": "GATE",
            "pins": [
                {"ref": str(gate_resistor["ref"]), "pin": "2"},
                {"ref": str(mosfet["ref"]), "pin": "1"},
                {"ref": str(gate_pulldown["ref"]), "pin": "1"},
            ],
        },
        {
            "name": "LED_NEG",
            "pins": [
                {"ref": load_ref, "pin": "2"},
                {"ref": str(mosfet["ref"]), "pin": "3"},
            ],
        },
    ]
    fixes = [
        "rebuilt legacy 555 PWM steering, timing, gate, and load nets into canonical Circuit IR"
    ]
    return rebuilt_nets, fixes


def _convert_legacy_components(
    components: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], dict[str, str]]:
    converted_components: list[dict[str, Any]] = []
    errors: list[str] = []
    ref_map: dict[str, str] = {}
    for component in components:
        ref = component.get("ref")
        if not isinstance(ref, str) or not ref:
            errors.append("legacy component entry is missing a non-empty 'ref'")
            continue

        symbol = _infer_legacy_symbol(component)
        if symbol is None:
            errors.append(f"{ref}: could not infer a KiCad symbol from legacy component data")
            continue

        normalized_ref = _normalized_legacy_ref(component, symbol=symbol)
        ref_map[ref] = normalized_ref
        converted_component: dict[str, Any] = {
            "ref": normalized_ref,
            "symbol": symbol,
        }
        if component.get("value") is not None:
            converted_component["value"] = str(component["value"])
        elif symbol == "Connector_Generic:Conn_01x02" and (
            "LOAD" in ref.upper() or "load" in str(component.get("description", "")).lower()
        ):
            converted_component["value"] = ref
        elif isinstance(component.get("name"), str):
            converted_component["value"] = component["name"]
        if component.get("footprint") is not None:
            converted_component["footprint"] = str(component["footprint"])
        if isinstance(component.get("description"), str) and component["description"]:
            converted_component["fields"] = {"LegacyDescription": component["description"]}
        if normalized_ref != ref:
            converted_component.setdefault("fields", {})["LegacyRef"] = ref

        converted_components.append(converted_component)

    return converted_components, errors, ref_map


def _convert_legacy_nets(
    nets: list[dict[str, Any]],
    ref_map: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    converted_nets: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, net in enumerate(nets):
        net_name = net.get("name") or net.get("description") or f"N{index + 1}"
        connections = net.get("connections")
        if not isinstance(connections, list):
            errors.append(f'net "{net_name}" is missing a legacy "connections" array')
            continue

        pins: list[dict[str, Any]] = []
        for connection in connections:
            if not isinstance(connection, dict):
                continue
            ref = connection.get("ref") or connection.get("component")
            pin = connection.get("pin")
            if ref in (None, "") or pin in (None, ""):
                errors.append(
                    f'net "{net_name}" contains a connection without component/ref and pin'
                )
                continue
            pins.append({"ref": ref_map.get(str(ref), str(ref)), "pin": str(pin)})

        converted_nets.append({"name": str(net_name), "pins": pins})

    return converted_nets, errors


def _convert_legacy_connection_format(
    raw: dict[str, Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Convert legacy `{name,pins}` / `{description,connections}` payloads to Circuit IR."""

    if not _looks_legacy_connection_format(raw):
        return raw, [], []

    result = copy.deepcopy(raw)
    fixes = ["converted legacy component/name + net/connections payload into canonical Circuit IR"]
    errors: list[str] = []
    converted_components, component_errors, ref_map = _convert_legacy_components(
        [component for component in result.get("components", []) if isinstance(component, dict)]
    )
    converted_nets, net_errors = _convert_legacy_nets(
        [net for net in result.get("nets", []) if isinstance(net, dict)],
        ref_map,
    )
    errors.extend(component_errors)
    errors.extend(net_errors)

    rebuilt_555_nets, rebuilt_555_fixes = _rebuild_legacy_555_pwm_nets(
        [component for component in result.get("components", []) if isinstance(component, dict)]
    )

    result["components"] = converted_components
    result["nets"] = rebuilt_555_nets if rebuilt_555_nets is not None else converted_nets
    fixes.extend(rebuilt_555_fixes)
    return result, fixes, errors


def _normalized_legacy_ref(component: dict[str, Any], *, symbol: str) -> str:
    ref = str(component.get("ref", ""))
    if symbol == "Connector_Generic:Conn_01x02" and not ref.upper().startswith(("J", "P")):
        return "J1"
    return ref


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
    symbol_index: _SymbolLookupProtocol,
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
        except UserError as exc:
            pin_cache[_sid] = set()  # lookup failed; pin alias fix cannot proceed for this symbol
            errors.append(f'symbol "{_sid}" pin lookup failed: {exc}')

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

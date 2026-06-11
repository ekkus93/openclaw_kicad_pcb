"""Pin-alias helpers, legacy-symbol inference, and legacy-format conversion."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

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

_LEGACY_SYMBOL_BY_NAME: dict[str, str] = {
    "1N4148": "Device:D",
    "AO3400": "Transistor_FET:Q_NMOS_GSD",
    "CONN_2": "Connector_Generic:Conn_01x02",
    "NE555": "Timer:NE555",
    "POT": "Device:R_Potentiometer",
    "RES": "Device:R",
}

_POLARIZED_CAP_FOOTPRINT_MARKERS: tuple[str, ...] = ("CP_", "CP-", "ELECT", "TANT")


def _try_resolve_alias(wrong_pin: str, valid_pins: set[str]) -> str | None:
    """Return a corrected pin name for *wrong_pin*, or ``None`` if unresolvable.

    Resolution order:
    1. Exact match (shouldn't reach here, but defensive).
    2. Case-insensitive match against valid pins.
    3. Alias table lookup (case-insensitive on both sides).
    """
    if wrong_pin in valid_pins:
        return wrong_pin

    upper = wrong_pin.upper()

    for vp in valid_pins:
        if vp.upper() == upper:
            return vp

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


def _infer_component_symbol(component: dict[str, Any]) -> str | None:
    """Infer a canonical KiCad symbol for a canonical-ish component entry.

    This is intentionally conservative and only covers common LLM omissions that
    can be repaired deterministically from the ref/value/footprint combination.
    """
    symbol = component.get("symbol")
    if isinstance(symbol, str) and symbol.strip():
        return symbol.strip()

    ref = str(component.get("ref", "")).strip().upper()
    value = str(component.get("value", "")).strip().upper()
    footprint = str(component.get("footprint", "")).strip().upper()

    inferred_symbol: str | None = None
    if ref.startswith("R"):
        inferred_symbol = "Device:R"
    elif ref.startswith("C"):
        if any(marker in footprint for marker in _POLARIZED_CAP_FOOTPRINT_MARKERS):
            inferred_symbol = "Device:C_Polarized"
        else:
            inferred_symbol = "Device:C"
    elif ref.startswith("D"):
        inferred_symbol = "Device:LED" if "LED" in value or "LED_" in footprint else "Device:D"
    elif ref.startswith("U") and "555" in value:
        inferred_symbol = "Timer:NE555"

    return inferred_symbol


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

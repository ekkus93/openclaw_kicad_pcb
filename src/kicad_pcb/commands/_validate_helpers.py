"""Shared low-level helpers for all _validate sub-modules.

Covers: component-kind predicates, footprint classification, net-name
predicates, pin-role resolution, and IR collection helpers.  All symbols
here are private to the commands package.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from ..circuit_ir import CircuitIR, ComponentIR
from ..component_types import is_power_net, normalize_gnd_net_name, power_rail_polarity
from ..errors import ParseError, UserError
from ..lib_symbol import read_lib_symbol_def_flat
from ..sexpr.nodes import ListNode, StringNode
from ..sexpr.utils import walk
from ..symbol_index import SymbolIndex

# ---------------------------------------------------------------------------
# Pin-role constants
# ---------------------------------------------------------------------------

_PIN_ROLE_INVERTING_INPUT = "inverting_input"
_PIN_ROLE_NONINVERTING_INPUT = "noninverting_input"
_PIN_ROLE_OUTPUT = "output"

# ---------------------------------------------------------------------------
# Footprint classification constants
# ---------------------------------------------------------------------------

_SYMBOL_LIKE_FOOTPRINT_LIBRARIES = frozenset(
    {
        "amplifier_operational",
        "connector",
        "connector_generic",
        "device",
        "testlib",
        "timer",
        "transistor_fet",
    }
)

_IC_FOOTPRINT_TOKENS = (
    "package_",
    "dip",
    "soic",
    "ssop",
    "tssop",
    "msop",
    "qfn",
    "qfp",
    "dfn",
    "bga",
    "lga",
    "sot",
)

# ---------------------------------------------------------------------------
# Component-kind helpers
# ---------------------------------------------------------------------------


def _component_kind(ref: str, symbol: str | None) -> str:
    ref_upper = ref.upper()
    symbol_tail = (symbol or "").rsplit(":", 1)[-1].lower()

    if ref_upper.startswith("C") or "capacitor" in symbol_tail or symbol_tail.startswith("c"):
        return "capacitor"
    if ref_upper.startswith("R") or "resistor" in symbol_tail or symbol_tail.startswith("r"):
        return "resistor"
    return "other"


def _symbol_tail(symbol: str | None) -> str:
    return (symbol or "").rsplit(":", 1)[-1].lower()


def _looks_opamp_symbol(symbol: str | None) -> bool:
    symbol_full = (symbol or "").lower()
    symbol_tail = _symbol_tail(symbol)
    return (
        "amplifier_operational" in symbol_full
        or "opamp" in symbol_full
        or symbol_tail.endswith("opamp")
    )


def _looks_timer_555(component_ref: str, symbol: str | None, value: str | None = None) -> bool:
    symbol_tail = _symbol_tail(symbol)
    value_text = (value or "").lower()
    has_555_token = re.search(r"(?<!\d)555(?!\d)", symbol_tail) or re.search(
        r"(?<!\d)555(?!\d)",
        value_text,
    )
    return bool(has_555_token) or component_ref.upper().startswith("U555")


def _looks_nmos(component_ref: str, symbol: str | None) -> bool:
    symbol_tail = _symbol_tail(symbol)
    ref_upper = component_ref.upper()
    return ref_upper.startswith("Q") and ("nmos" in symbol_tail or "mos" in symbol_tail)


def _looks_connector(component_ref: str, symbol: str | None) -> bool:
    ref_upper = component_ref.upper()
    symbol_tail = (symbol or "").rsplit(":", 1)[-1].lower()
    symbol_full = (symbol or "").lower()
    return (
        ref_upper.startswith(("J", "P", "CON"))
        or "connector" in symbol_full
        or "jack" in symbol_tail
        or symbol_tail.startswith("conn_")
    )


def _is_audio_jack(component: ComponentIR) -> bool:
    symbol_full = (component.symbol or "").lower()
    value_text = (component.value or "").lower()
    return "audiojack" in symbol_full or "trs" in value_text or "headphone" in value_text


# ---------------------------------------------------------------------------
# Footprint helpers
# ---------------------------------------------------------------------------


def _footprint_parts(footprint: str) -> tuple[str, str, str]:
    if ":" in footprint:
        library, package = footprint.split(":", 1)
    else:
        library, package = "", footprint
    normalized = f"{library}:{package}".lower() if library else package.lower()
    return library, package, normalized


def _expected_footprint_category(component: ComponentIR) -> str | None:
    kind = _component_kind(component.ref, component.symbol)
    symbol_tail = _symbol_tail(component.symbol)

    category_checks: tuple[tuple[bool, str], ...] = (
        (_is_audio_jack(component), "audio_jack"),
        (_looks_connector(component.ref, component.symbol), "connector"),
        (symbol_tail == "r_potentiometer", "potentiometer"),
        (_looks_timer_555(component.ref, component.symbol, component.value), "ic"),
        (_looks_nmos(component.ref, component.symbol), "transistor"),
        (kind in {"resistor", "capacitor"}, kind),
        (component.ref.upper().startswith("D") or symbol_tail == "d", "diode"),
        (component.ref.upper().startswith("U") or _looks_opamp_symbol(component.symbol), "ic"),
    )
    return next((category for matches, category in category_checks if matches), None)


def _footprint_looks_placeholder_or_symbol_id(component: ComponentIR) -> bool:
    if not component.footprint:
        return False

    library, package, normalized = _footprint_parts(component.footprint)
    symbol_id = (component.symbol or "").lower()
    if normalized == symbol_id:
        return True
    if not library:
        return True
    if library.lower() in _SYMBOL_LIKE_FOOTPRINT_LIBRARIES:
        return True

    lowered_package = package.lower()
    return any(token in normalized for token in ("placeholder", "testlib")) or any(
        token in lowered_package for token in ("generic", "symbol")
    )


def _footprint_matches_category(footprint: str, category: str) -> bool:
    _library, package, normalized = _footprint_parts(footprint)
    lowered_package = package.lower()

    category_matchers: dict[str, bool] = {
        "resistor": "resistor" in normalized or lowered_package.startswith("r_"),
        "capacitor": "capacitor" in normalized or lowered_package.startswith(("c_", "cp_")),
        "diode": "diode" in normalized or lowered_package.startswith("d_"),
        "potentiometer": "potentiometer" in normalized,
        "connector": any(
            token in normalized for token in ("connector", "pinheader", "terminalblock")
        ),
        "audio_jack": any(token in normalized for token in ("audio", "jack")),
        "transistor": any(token in normalized for token in ("package_to_sot", "sot", "to-", "to_")),
        "ic": any(token in normalized for token in _IC_FOOTPRINT_TOKENS),
    }
    return category_matchers.get(category, True)


# ---------------------------------------------------------------------------
# Net-name predicates
# ---------------------------------------------------------------------------


def _is_ground_net(net_name: str | None) -> bool:
    return net_name is not None and normalize_gnd_net_name(net_name) == "GND"


def _is_positive_supply_net(net_name: str | None) -> bool:
    if net_name is None:
        return False
    polarity = power_rail_polarity(net_name)
    return polarity == "positive" or net_name.upper().startswith(("VCC", "VDD", "+"))


def _looks_input_net(net_name: str) -> bool:
    name = net_name.upper()
    return "INPUT" in name or "_IN" in name or name.startswith("IN_") or name.endswith("_IN")


def _looks_output_net(net_name: str) -> bool:
    name = net_name.upper()
    return (
        "OUTPUT" in name
        or "_OUT" in name
        or name.startswith("OUT_")
        or name.endswith("_OUT")
        or name.startswith("HP_")
        or name.endswith("_RAW")
    )


def _looks_power_like_net(net_name: str) -> bool:
    name = net_name.upper()
    return (
        is_power_net(net_name)
        or power_rail_polarity(net_name) is not None
        or normalize_gnd_net_name(name) == "GND"
        or "GND" in name
        or "0V" in name
    )


# ---------------------------------------------------------------------------
# Bridge / net-pair helpers
# ---------------------------------------------------------------------------


def _sorted_net_pair(net_a: str, net_b: str) -> tuple[str, str]:
    return (net_a, net_b) if net_a <= net_b else (net_b, net_a)


def _net_pair_bridge_refs(
    bridges: dict[tuple[str, str], list[dict[str, str]]],
    net_a: str,
    net_b: str,
    *,
    kind: str | None = None,
) -> list[str]:
    refs = [
        item["ref"]
        for item in bridges.get(_sorted_net_pair(net_a, net_b), [])
        if kind is None or item["kind"] == kind
    ]
    return sorted(refs)


def _parse_scalar_value(raw_value: str | None) -> float | None:
    if raw_value is None:
        return None
    text = raw_value.strip().replace("Ω", "").replace("ohm", "")
    match = re.match(r"^(?P<num>\d+(?:\.\d+)?)(?P<unit>[a-zA-Z]+)?$", text)
    if match is None:
        return None
    number = float(match.group("num"))
    unit = (match.group("unit") or "").lower()
    unit = unit.removesuffix("f")
    multipliers = {
        "": 1.0,
        "p": 1e-12,
        "n": 1e-9,
        "u": 1e-6,
        "m": 1e-3,
        "k": 1e3,
        "meg": 1e6,
        "g": 1e9,
    }
    if unit not in multipliers:
        return None
    return number * multipliers[unit]


# ---------------------------------------------------------------------------
# Pin-role resolution
# ---------------------------------------------------------------------------


def _looks_supply_pin_name(pin_name: str) -> bool:
    name = pin_name.upper().replace(" ", "")
    return (
        name
        in {
            "V+",
            "V-",
            "VCC+",
            "VCC-",
            "VDD+",
            "VDD-",
        }
        or power_rail_polarity(name) is not None
        or normalize_gnd_net_name(name) == "GND"
        or name.startswith("VSS")
    )


def _classify_pin_role(pin_name: str, electrical_type: str) -> str | None:
    normalized_name = pin_name.upper().replace(" ", "")
    normalized_type = electrical_type.upper()

    if _looks_supply_pin_name(normalized_name):
        return None
    if "OUT" in normalized_name or normalized_type == "OUTPUT":
        return _PIN_ROLE_OUTPUT
    if (
        normalized_name in {"+", "IN+", "NONINV", "NONINVERTING", "NONINVERTINGINPUT"}
        or normalized_name.startswith("IN+")
        or normalized_name.endswith("+")
        or "NONINV" in normalized_name
        or "NONINVERT" in normalized_name
    ):
        return _PIN_ROLE_NONINVERTING_INPUT
    if (
        normalized_name in {"-", "IN-", "INV", "INVERTING", "INVERTINGINPUT"}
        or normalized_name.startswith("IN-")
        or normalized_name.startswith("-")
        or normalized_name.endswith("-")
        or "INV" in normalized_name
        or "INVERT" in normalized_name
    ):
        return _PIN_ROLE_INVERTING_INPUT
    return None


@lru_cache(maxsize=128)
def _symbol_pin_roles(
    symbol_id: str,
    directories: tuple[Path, ...],
) -> tuple[tuple[str, str], ...]:
    if ":" not in symbol_id:
        return ()

    lib_name, sym_name = symbol_id.split(":", 1)
    for directory in directories:
        try:
            symbol_def = read_lib_symbol_def_flat(lib_name, sym_name, symbols_dir=directory)
        except (OSError, ParseError, UserError):
            continue
        if symbol_def is None:
            continue

        roles: dict[str, str] = {}
        for node in walk(symbol_def):
            if not (isinstance(node, ListNode) and node.key == "pin"):
                continue

            electrical_type = (
                str(getattr(node.items[1], "value", "")) if len(node.items) >= 2 else ""
            )
            pin_name = ""
            pin_number: str | None = None
            for child in node.items:
                if not isinstance(child, ListNode):
                    continue
                if (
                    child.key == "name"
                    and len(child.items) >= 2
                    and isinstance(child.items[1], StringNode)
                ):
                    pin_name = child.items[1].value
                if (
                    child.key == "number"
                    and len(child.items) >= 2
                    and isinstance(child.items[1], StringNode)
                ):
                    pin_number = child.items[1].value

            if pin_number is None:
                continue

            role = _classify_pin_role(pin_name, electrical_type)
            if role is not None:
                roles.setdefault(pin_number, role)

        if roles:
            return tuple(sorted(roles.items()))

    return ()


def _component_pin_roles(component_symbol: str, symbol_index: SymbolIndex | None) -> dict[str, str]:
    if symbol_index is None:
        return {}
    if not _looks_opamp_symbol(component_symbol):
        return {}
    return dict(_symbol_pin_roles(component_symbol, symbol_index.directories))


# ---------------------------------------------------------------------------
# IR collection helpers
# ---------------------------------------------------------------------------


def _collect_pin_to_net(ir: CircuitIR) -> dict[tuple[str, str], str]:
    return {(pin_ref.ref, pin_ref.pin): net.name for net in ir.nets for pin_ref in net.pins}


def _collect_component_nets(ir: CircuitIR) -> dict[str, set[str]]:
    component_nets: dict[str, set[str]] = {}
    for net in ir.nets:
        for pin_ref in net.pins:
            component_nets.setdefault(pin_ref.ref, set()).add(net.name)
    return component_nets


def _collect_two_pin_component_bridges(
    ir: CircuitIR,
) -> dict[tuple[str, str], list[dict[str, str]]]:
    component_map = {component.ref: component for component in ir.components}
    pin_to_net: dict[tuple[str, str], str] = {}
    component_pin_memberships: dict[str, set[str]] = {}

    for net in ir.nets:
        for pin_ref in net.pins:
            pin_key = (pin_ref.ref, pin_ref.pin)
            pin_to_net[pin_key] = net.name
            component_pin_memberships.setdefault(pin_ref.ref, set()).add(pin_ref.pin)

    bridges: dict[tuple[str, str], list[dict[str, str]]] = {}
    for ref, pins in component_pin_memberships.items():
        if len(pins) != 2:
            continue

        nets_for_component = sorted(
            {pin_to_net[(ref, pin)] for pin in pins if (ref, pin) in pin_to_net}
        )
        if len(nets_for_component) != 2:
            continue

        component = component_map.get(ref)
        if component is None:
            continue

        pair = (nets_for_component[0], nets_for_component[1])
        bridges.setdefault(pair, []).append(
            {
                "ref": ref,
                "symbol": component.symbol,
                "kind": _component_kind(ref, component.symbol),
            }
        )

    return bridges

"""Shared validation helpers for netlist commands.

Two public functions are provided:

:func:`full_validate`       — run all three IR validation layers and return
                              the parsed :class:`~kicad_pcb.circuit_ir.CircuitIR`.
:func:`advisory_warnings`   — return non-blocking advisory warnings for
                              common IR mistakes (floating components, single-pin
                              nets).

These are extracted from ``netlist.py`` to avoid duplicating the 3-layer
validation sequence across ``cmd_validate_netlist``, ``cmd_fix_netlist``, and
``cmd_new_from_netlist``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..circuit_ir import CircuitIR, ComponentIR
from ..component_types import is_power_net, normalize_gnd_net_name, power_rail_polarity
from ..errors import ErrorCode, ParseError, UserError
from ..ir.validate import validate_circuit_ir, validate_ir_symbols
from ..lib_symbol import read_lib_symbol_def_flat
from ..sexpr.nodes import ListNode, StringNode
from ..sexpr.utils import walk
from ..symbol_index import SymbolIndex

_PIN_ROLE_INVERTING_INPUT = "inverting_input"
_PIN_ROLE_NONINVERTING_INPUT = "noninverting_input"
_PIN_ROLE_OUTPUT = "output"


@dataclass(frozen=True)
class _Timer555Context:
    timer: ComponentIR
    ground_net: str | None
    trigger_net: str | None
    output_net: str | None
    reset_net: str | None
    ctrl_net: str | None
    threshold_net: str | None
    discharge_net: str | None
    vcc_net: str | None
    timing_net: str | None
    pot: ComponentIR | None
    mosfet: ComponentIR | None
    connector: ComponentIR | None


@dataclass(frozen=True)
class AdvisoryFinding:
    family: str
    code: str
    message: str
    details: dict[str, object]
    severity: str = "warning"

    def as_dict(self) -> dict[str, object]:
        return {
            "family": self.family,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True)
class _CircuitLintRule:
    family: str
    checker: Callable[[CircuitIR, SymbolIndex | None], list[dict[str, object]]]
    default_severity: str = "warning"
    severity_overrides: tuple[tuple[str, str], ...] = ()

    def severity_for(self, code: str) -> str:
        for overridden_code, severity in self.severity_overrides:
            if overridden_code == code:
                return severity
        return self.default_severity


_TIMER555_HARD_FAIL_CODES = frozenset(
    {
        "TIMER555_GROUND_PIN_INVALID",
        "TIMER555_VCC_PIN_INVALID",
        "TIMER555_RESET_NOT_TIED_HIGH",
        "TIMER555_TIMING_NODE_SPLIT",
        "TIMER555_TIMING_CAP_NOT_TO_GROUND",
        "TIMER555_TIMING_CAP_ACROSS_SUPPLY",
        "TIMER555_CTRL_CAP_MISSING_TO_GROUND",
        "TIMER555_CTRL_CAP_WRONG_TARGET",
        "TIMER555_STEERING_NETWORK_INVALID",
        "TIMER555_GATE_RESISTOR_MISSING",
        "TIMER555_GATE_PULLDOWN_MISSING",
        "TIMER555_GATE_PULLDOWN_TOUCHES_TIMING_NODE",
        "TIMER555_LOW_SIDE_LOAD_TOPOLOGY_INVALID",
    }
)


def _component_kind(ref: str, symbol: str | None) -> str:
    ref_upper = ref.upper()
    symbol_tail = (symbol or "").rsplit(":", 1)[-1].lower()

    if ref_upper.startswith("C") or "capacitor" in symbol_tail or symbol_tail.startswith("c"):
        return "capacitor"
    if ref_upper.startswith("R") or "resistor" in symbol_tail or symbol_tail.startswith("r"):
        return "resistor"
    return "other"


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


def _footprint_quality_warnings(ir: CircuitIR) -> list[dict[str, object]]:
    warnings: list[dict[str, object]] = []

    for component in ir.components:
        footprint = component.footprint
        if footprint is None or not footprint.strip():
            continue

        expected_category = _expected_footprint_category(component)
        if _footprint_looks_placeholder_or_symbol_id(component):
            warnings.append(
                {
                    "code": "FOOTPRINT_LOOKS_PLACEHOLDER_OR_SYMBOL_ID",
                    "message": (
                        f"Component {component.ref} uses footprint {footprint}, which looks like "
                        "a symbol id or placeholder footprint rather than a concrete package."
                    ),
                    "details": {
                        "ref": component.ref,
                        "symbol": component.symbol,
                        "footprint": footprint,
                        "expected_category": expected_category,
                    },
                }
            )
            continue

        if expected_category is None or _footprint_matches_category(footprint, expected_category):
            continue

        warnings.append(
            {
                "code": "FOOTPRINT_CLASS_MISMATCH",
                "message": (
                    f"Component {component.ref} uses footprint {footprint}, which does not look "
                    f"compatible with its {expected_category.replace('_', ' ')} symbol class."
                ),
                "details": {
                    "ref": component.ref,
                    "symbol": component.symbol,
                    "footprint": footprint,
                    "expected_category": expected_category,
                },
            }
        )

    return warnings


def _generic_connectivity_warnings(
    ir: CircuitIR,
    _symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    warnings: list[dict[str, object]] = []

    refs_in_nets: set[str] = {pin_ref.ref for net in ir.nets for pin_ref in net.pins}
    component_refs = {component.ref for component in ir.components}
    unreferenced = sorted(component_refs - refs_in_nets)
    if unreferenced:
        warnings.append(
            {
                "code": "COMPONENT_NOT_IN_ANY_NET",
                "message": (
                    f"{len(unreferenced)} component(s) are not referenced in any net "
                    "and will be floating in the schematic."
                ),
                "details": {"refs": unreferenced},
            }
        )

    single_pin_nets = [net.name for net in ir.nets if len(net.pins) == 1]
    if single_pin_nets:
        warnings.append(
            {
                "code": "SINGLE_PIN_NET",
                "message": (
                    f"{len(single_pin_nets)} net(s) have only one connected pin. "
                    "This is usually a wiring mistake."
                ),
                "details": {"nets": single_pin_nets},
            }
        )

    warnings.extend(_footprint_quality_warnings(ir))

    return warnings


def _audio_connector_warnings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    warnings = _ambiguous_connector_usage_warnings(ir, symbol_index)
    warnings.extend(_incomplete_stereo_trs_warnings(ir))
    return warnings


def _audio_opamp_warnings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    warnings = _input_coupling_bypass_warnings(ir)
    warnings.extend(_output_coupling_bypass_warnings(ir))
    warnings.extend(_opamp_feedback_warnings(ir, symbol_index))
    warnings.extend(_opamp_output_sanity_warnings(ir, symbol_index))
    warnings.extend(_opamp_ac_coupled_output_load_warnings(ir, symbol_index))
    warnings.extend(_headphone_output_impedance_warnings(ir, symbol_index))
    warnings.extend(_speaker_driver_advisories(ir, symbol_index))
    warnings.extend(_split_rail_interstage_coupling_warnings(ir, symbol_index))
    warnings.extend(_opamp_stage_topology_warnings(ir, symbol_index))
    return warnings


def _timer555_warnings(
    ir: CircuitIR,
    _symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    return _timer555_pwm_warnings(ir)


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
    return "555" in symbol_tail or "555" in value_text or component_ref.upper().startswith("U555")


def _looks_nmos(component_ref: str, symbol: str | None) -> bool:
    symbol_tail = _symbol_tail(symbol)
    ref_upper = component_ref.upper()
    return ref_upper.startswith("Q") and ("nmos" in symbol_tail or "mos" in symbol_tail)


def _is_ground_net(net_name: str | None) -> bool:
    return net_name is not None and normalize_gnd_net_name(net_name) == "GND"


def _is_positive_supply_net(net_name: str | None) -> bool:
    if net_name is None:
        return False
    polarity = power_rail_polarity(net_name)
    return polarity == "positive" or net_name.upper().startswith(("VCC", "VDD", "+"))


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


def _connector_output_intent(net_name: str, component: ComponentIR) -> bool:
    value_text = (component.value or "").lower()
    return _looks_output_net(net_name) or "out" in value_text or "headphone" in value_text


def _looks_speaker_output(net_name: str, component: ComponentIR) -> bool:
    name = net_name.upper()
    value_text = (component.value or "").lower()
    return "SPK" in name or "SPEAKER" in name or "speaker" in value_text


def _is_supported_mono_trs_usage(
    component: ComponentIR,
    used_pins: set[str],
    unused_pins: list[str],
) -> bool:
    if component.symbol != "Connector:AudioJack3":
        return False
    if len(unused_pins) != 1:
        return False
    supported_unused = {"R": {"T", "S"}, "T": {"R", "S"}}
    return used_pins == supported_unused.get(unused_pins[0], set())


def _component_has_split_supply(
    component: ComponentIR,
    component_nets: dict[str, set[str]],
) -> bool:
    nets = component_nets.get(component.ref, set())
    has_positive = any(power_rail_polarity(net_name) == "positive" for net_name in nets)
    has_negative = any(power_rail_polarity(net_name) == "negative" for net_name in nets)
    return has_positive and has_negative


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


def _sorted_net_pair(net_a: str, net_b: str) -> tuple[str, str]:
    return (net_a, net_b) if net_a <= net_b else (net_b, net_a)


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


def _input_coupling_bypass_warnings(ir: CircuitIR) -> list[dict[str, object]]:
    warnings: list[dict[str, object]] = []

    for net_pair, bridges in sorted(_collect_two_pin_component_bridges(ir).items()):
        if not any(_looks_input_net(net_name) for net_name in net_pair):
            continue

        capacitor_refs = sorted(item["ref"] for item in bridges if item["kind"] == "capacitor")
        resistor_refs = sorted(item["ref"] for item in bridges if item["kind"] == "resistor")
        if not capacitor_refs or not resistor_refs:
            continue

        warnings.append(
            {
                "code": "INPUT_COUPLING_BYPASSED_BY_RESISTOR",
                "message": (
                    "Input-path coupling capacitor appears to be paralleled by resistor(s) "
                    f"between {net_pair[0]} and {net_pair[1]}."
                ),
                "details": {
                    "nets": list(net_pair),
                    "capacitor_refs": capacitor_refs,
                    "resistor_refs": resistor_refs,
                    "bridge_component_refs": sorted(capacitor_refs + resistor_refs),
                },
            }
        )

    return warnings


def _output_coupling_bypass_warnings(ir: CircuitIR) -> list[dict[str, object]]:
    warnings: list[dict[str, object]] = []

    for net_pair, bridges in sorted(_collect_two_pin_component_bridges(ir).items()):
        if not any(_looks_output_net(net_name) for net_name in net_pair):
            continue

        capacitor_refs = sorted(item["ref"] for item in bridges if item["kind"] == "capacitor")
        resistor_refs = sorted(item["ref"] for item in bridges if item["kind"] == "resistor")
        if not capacitor_refs or not resistor_refs:
            continue

        warnings.append(
            {
                "code": "OUTPUT_COUPLING_BYPASSED_BY_RESISTOR",
                "message": (
                    "Output-path coupling capacitor appears to be paralleled by resistor(s) "
                    f"between {net_pair[0]} and {net_pair[1]}."
                ),
                "details": {
                    "nets": list(net_pair),
                    "capacitor_refs": capacitor_refs,
                    "resistor_refs": resistor_refs,
                    "bridge_component_refs": sorted(capacitor_refs + resistor_refs),
                },
            }
        )

    return warnings


def _ambiguous_connector_usage_warnings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    if symbol_index is None:
        return []

    pins_in_nets: dict[str, set[str]] = {}
    for net in ir.nets:
        for pin_ref in net.pins:
            pins_in_nets.setdefault(pin_ref.ref, set()).add(pin_ref.pin)

    warnings: list[dict[str, object]] = []
    for component in ir.components:
        if not _looks_connector(component.ref, component.symbol):
            continue

        try:
            valid_pins = symbol_index.get_pins(component.symbol)
        except UserError:
            continue

        used_pins = pins_in_nets.get(component.ref, set())
        if not used_pins:
            continue

        unused_pins = sorted(valid_pins - used_pins)
        if not unused_pins:
            continue
        if len(valid_pins) <= 1:
            continue
        if _is_supported_mono_trs_usage(component, used_pins, unused_pins):
            continue

        warnings.append(
            {
                "code": "CONNECTOR_UNUSED_PINS_AMBIGUOUS",
                "message": (
                    f"Connector {component.ref} leaves symbol pin(s) {', '.join(unused_pins)} "
                    "unconnected without explicit no-connect handling."
                ),
                "details": {
                    "ref": component.ref,
                    "symbol": component.symbol,
                    "used_pins": sorted(used_pins),
                    "unused_pins": unused_pins,
                },
            }
        )

    return warnings


def _incomplete_stereo_trs_warnings(ir: CircuitIR) -> list[dict[str, object]]:
    warnings: list[dict[str, object]] = []

    for component in ir.components:
        if component.symbol != "Connector:AudioJack3":
            continue

        used_nets_by_pin: dict[str, str] = {}
        for net in ir.nets:
            for pin_ref in net.pins:
                if pin_ref.ref == component.ref and pin_ref.pin in {"T", "R"}:
                    used_nets_by_pin[pin_ref.pin] = net.name

        if set(used_nets_by_pin) != {"T", "R"}:
            continue

        leftish = any(token in used_nets_by_pin["T"].upper() for token in ("LEFT", "_L", "L_"))
        rightish = any(token in used_nets_by_pin["R"].upper() for token in ("RIGHT", "_R", "R_"))
        if leftish and rightish:
            continue

        warnings.append(
            {
                "code": "TRS_STEREO_IMPLEMENTATION_INCOMPLETE",
                "message": (
                    f"TRS connector {component.ref} uses both tip and ring, but the connected "
                    "nets do not read as a complete left/right stereo pair."
                ),
                "details": {
                    "ref": component.ref,
                    "symbol": component.symbol,
                    "value": component.value,
                    "tip_net": used_nets_by_pin["T"],
                    "ring_net": used_nets_by_pin["R"],
                    "used_signal_pins": sorted(used_nets_by_pin),
                },
            }
        )

    return warnings


def _opamp_feedback_warnings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    if symbol_index is None:
        return []

    pin_to_net = _collect_pin_to_net(ir)
    bridges = _collect_two_pin_component_bridges(ir)
    warnings: list[dict[str, object]] = []

    for component in ir.components:
        pin_roles = _component_pin_roles(component.symbol, symbol_index)
        if not pin_roles:
            continue

        output_nets = sorted(
            {
                pin_to_net[(component.ref, pin_number)]
                for pin_number, role in pin_roles.items()
                if role == _PIN_ROLE_OUTPUT and (component.ref, pin_number) in pin_to_net
            }
        )
        if not output_nets:
            continue

        for pin_number, role in sorted(pin_roles.items()):
            if role != _PIN_ROLE_INVERTING_INPUT:
                continue

            inverting_net = pin_to_net.get((component.ref, pin_number))
            if inverting_net is None or _looks_power_like_net(inverting_net):
                continue
            if inverting_net in output_nets:
                continue

            feedback_refs: list[str] = []
            for output_net in output_nets:
                bridge_pair = _sorted_net_pair(inverting_net, output_net)
                for bridge in bridges.get(bridge_pair, []):
                    if bridge["kind"] in {"resistor", "capacitor"}:
                        feedback_refs.append(bridge["ref"])

            if feedback_refs:
                continue

            warnings.append(
                {
                    "code": "OPAMP_FEEDBACK_MISSING_OR_NONLOCAL",
                    "message": (
                        f"Op-amp {component.ref} inverting input pin {pin_number} on "
                        f"{inverting_net} does not appear to have a local feedback path "
                        "from any output net."
                    ),
                    "details": {
                        "ref": component.ref,
                        "symbol": component.symbol,
                        "inverting_input_pin": pin_number,
                        "inverting_input_net": inverting_net,
                        "output_nets": output_nets,
                    },
                }
            )

    return warnings


def _opamp_output_sanity_warnings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    if symbol_index is None:
        return []

    pin_to_net = _collect_pin_to_net(ir)
    nets_by_name = {net.name: net for net in ir.nets}
    warnings: list[dict[str, object]] = []

    for component in ir.components:
        pin_roles = _component_pin_roles(component.symbol, symbol_index)
        if not pin_roles:
            continue

        for pin_number, role in sorted(pin_roles.items()):
            if role != _PIN_ROLE_OUTPUT:
                continue

            output_net = pin_to_net.get((component.ref, pin_number))
            if output_net is None:
                warnings.append(
                    {
                        "code": "OPAMP_OUTPUT_FLOATING",
                        "message": (
                            f"Op-amp {component.ref} output pin {pin_number} is not connected "
                            "to any net."
                        ),
                        "details": {
                            "ref": component.ref,
                            "symbol": component.symbol,
                            "output_pin": pin_number,
                        },
                    }
                )
                continue

            if _looks_power_like_net(output_net):
                warnings.append(
                    {
                        "code": "OPAMP_OUTPUT_SHORTED_TO_RAIL",
                        "message": (
                            f"Op-amp {component.ref} output pin {pin_number} is connected "
                            f"directly to rail-like net {output_net}."
                        ),
                        "details": {
                            "ref": component.ref,
                            "symbol": component.symbol,
                            "output_pin": pin_number,
                            "output_net": output_net,
                        },
                    }
                )
                continue

            external_connections = sorted(
                {
                    f"{pin.ref}:{pin.pin}"
                    for pin in nets_by_name[output_net].pins
                    if pin.ref != component.ref
                }
            )
            if external_connections:
                continue

            warnings.append(
                {
                    "code": "OPAMP_OUTPUT_FLOATING",
                    "message": (
                        f"Op-amp {component.ref} output pin {pin_number} only connects on "
                        f"{output_net} to pins on the same package."
                    ),
                    "details": {
                        "ref": component.ref,
                        "symbol": component.symbol,
                        "output_pin": pin_number,
                        "output_net": output_net,
                    },
                }
            )

    return warnings


def _opamp_ac_coupled_output_load_warnings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    if symbol_index is None:
        return []

    pin_to_net = _collect_pin_to_net(ir)
    bridges = _collect_two_pin_component_bridges(ir)
    component_map = {component.ref: component for component in ir.components}
    nets_by_name = {net.name: net for net in ir.nets}
    warnings: list[dict[str, object]] = []
    warned_paths: set[tuple[str, str, str]] = set()

    for component in ir.components:
        pin_roles = _component_pin_roles(component.symbol, symbol_index)
        if not pin_roles:
            continue

        for pin_number, role in sorted(pin_roles.items()):
            if role != _PIN_ROLE_OUTPUT:
                continue

            output_net = pin_to_net.get((component.ref, pin_number))
            if output_net is None or _looks_power_like_net(output_net):
                continue

            for net_pair, bridge_members in sorted(bridges.items()):
                if output_net not in net_pair:
                    continue

                capacitor_refs = sorted(
                    item["ref"] for item in bridge_members if item["kind"] == "capacitor"
                )
                if not capacitor_refs:
                    continue

                coupled_net = net_pair[1] if net_pair[0] == output_net else net_pair[0]
                if _looks_power_like_net(coupled_net):
                    continue
                if not _looks_output_net(coupled_net):
                    coupled_net_record = nets_by_name.get(coupled_net)
                    if coupled_net_record is None:
                        continue
                    coupled_components = [
                        component_map.get(pin.ref) for pin in coupled_net_record.pins
                    ]
                    if not any(
                        part is not None and _looks_connector(part.ref, part.symbol)
                        for part in coupled_components
                    ):
                        continue

                bleed_resistor_refs: set[str] = set()
                for other_pair, other_members in bridges.items():
                    if coupled_net not in other_pair:
                        continue
                    reference_net = other_pair[1] if other_pair[0] == coupled_net else other_pair[0]
                    if not _looks_power_like_net(reference_net):
                        continue
                    bleed_resistor_refs.update(
                        item["ref"] for item in other_members if item["kind"] == "resistor"
                    )

                if bleed_resistor_refs:
                    continue

                warning_key = (component.ref, output_net, coupled_net)
                if warning_key in warned_paths:
                    continue
                warned_paths.add(warning_key)

                warnings.append(
                    {
                        "code": "OUTPUT_CAP_NO_DEFINED_LOAD_OR_BLEED",
                        "message": (
                            f"Op-amp {component.ref} output pin {pin_number} is AC-coupled from "
                            f"{output_net} to {coupled_net} without a resistor-defined bleed or "
                            "load path on the output side."
                        ),
                        "details": {
                            "ref": component.ref,
                            "symbol": component.symbol,
                            "output_pin": pin_number,
                            "output_net": output_net,
                            "coupled_output_net": coupled_net,
                            "capacitor_refs": capacitor_refs,
                        },
                    }
                )

    return warnings


def _headphone_output_impedance_warnings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    if symbol_index is None:
        return []

    pin_to_net = _collect_pin_to_net(ir)
    bridges = _collect_two_pin_component_bridges(ir)
    component_map = {component.ref: component for component in ir.components}
    warnings: list[dict[str, object]] = []
    warned_paths: set[tuple[str, str, str]] = set()

    connector_output_nets = {
        net.name
        for net in ir.nets
        if any(
            (component := component_map.get(pin.ref)) is not None
            and _is_audio_jack(component)
            and _connector_output_intent(net.name, component)
            for pin in net.pins
        )
    }

    for component in ir.components:
        pin_roles = _component_pin_roles(component.symbol, symbol_index)
        if not pin_roles:
            continue

        for pin_number, role in sorted(pin_roles.items()):
            if role != _PIN_ROLE_OUTPUT:
                continue

            output_net = pin_to_net.get((component.ref, pin_number))
            if output_net is None or _looks_power_like_net(output_net):
                continue

            for net_pair, bridge_members in sorted(bridges.items()):
                if output_net not in net_pair:
                    continue

                resistor_refs = sorted(
                    item["ref"] for item in bridge_members if item["kind"] == "resistor"
                )
                if not resistor_refs:
                    continue

                downstream_net = net_pair[1] if net_pair[0] == output_net else net_pair[0]
                candidate_output_nets = {downstream_net}
                for other_pair, other_members in bridges.items():
                    if downstream_net not in other_pair:
                        continue
                    if not any(item["kind"] == "capacitor" for item in other_members):
                        continue
                    candidate_output_nets.add(
                        other_pair[1] if other_pair[0] == downstream_net else other_pair[0]
                    )

                if candidate_output_nets.isdisjoint(connector_output_nets):
                    continue

                resolved_values = [
                    _parse_scalar_value(component_map[ref].value)
                    for ref in resistor_refs
                    if ref in component_map
                ]
                numeric_values = [value for value in resolved_values if value is not None]
                if not numeric_values:
                    continue

                series_ohms = max(numeric_values)
                if series_ohms < 22.0:
                    continue

                warning_key = (component.ref, output_net, downstream_net)
                if warning_key in warned_paths:
                    continue
                warned_paths.add(warning_key)

                warnings.append(
                    {
                        "code": "HEADPHONE_OUTPUT_IMPEDANCE_HIGH",
                        "message": (
                            f"Op-amp {component.ref} drives headphone/output path {output_net} "
                            f"through {series_ohms:.0f} ohm series resistance, which is high "
                            "for many low-impedance headphone loads."
                        ),
                        "details": {
                            "ref": component.ref,
                            "symbol": component.symbol,
                            "output_pin": pin_number,
                            "output_net": output_net,
                            "downstream_net": downstream_net,
                            "connector_output_nets": sorted(
                                candidate_output_nets & connector_output_nets
                            ),
                            "resistor_refs": resistor_refs,
                            "series_ohms": series_ohms,
                        },
                    }
                )

    return warnings


def _speaker_driver_advisories(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    if symbol_index is None:
        return []

    pin_to_net = _collect_pin_to_net(ir)
    component_map = {component.ref: component for component in ir.components}
    warnings: list[dict[str, object]] = []

    speaker_nets = {
        net.name
        for net in ir.nets
        if any(
            (component := component_map.get(pin.ref)) is not None
            and _looks_connector(component.ref, component.symbol)
            and _looks_speaker_output(net.name, component)
            for pin in net.pins
        )
    }
    if not speaker_nets:
        return []

    for component in ir.components:
        pin_roles = _component_pin_roles(component.symbol, symbol_index)
        if not pin_roles:
            continue

        output_nets = sorted(
            {
                pin_to_net[(component.ref, pin_number)]
                for pin_number, role in pin_roles.items()
                if role == _PIN_ROLE_OUTPUT and (component.ref, pin_number) in pin_to_net
            }
        )
        for output_net in output_nets:
            if output_net not in speaker_nets:
                continue
            warnings.append(
                {
                    "code": "OPAMP_PRESENTED_AS_SPEAKER_POWER_STAGE",
                    "message": (
                        f"Op-amp {component.ref} appears to drive speaker-designated net "
                        f"{output_net}. Treat small-signal op-amps as preamp or light-load "
                        "drivers, not speaker power stages."
                    ),
                    "details": {
                        "ref": component.ref,
                        "symbol": component.symbol,
                        "output_net": output_net,
                    },
                }
            )

    return warnings


def _split_rail_interstage_coupling_warnings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    if symbol_index is None:
        return []

    pin_to_net = _collect_pin_to_net(ir)
    bridges = _collect_two_pin_component_bridges(ir)
    component_nets = _collect_component_nets(ir)
    warnings: list[dict[str, object]] = []
    warned_paths: set[tuple[str, str, str]] = set()

    input_targets_by_net: dict[str, list[tuple[str, str]]] = {}
    for component in ir.components:
        pin_roles = _component_pin_roles(component.symbol, symbol_index)
        if not pin_roles:
            continue
        for pin_number, role in pin_roles.items():
            if role not in {_PIN_ROLE_INVERTING_INPUT, _PIN_ROLE_NONINVERTING_INPUT}:
                continue
            net_name = pin_to_net.get((component.ref, pin_number))
            if net_name is None or _looks_power_like_net(net_name):
                continue
            input_targets_by_net.setdefault(net_name, []).append((component.ref, pin_number))

    for component in ir.components:
        pin_roles = _component_pin_roles(component.symbol, symbol_index)
        if not pin_roles or not _component_has_split_supply(component, component_nets):
            continue

        for pin_number, role in sorted(pin_roles.items()):
            if role != _PIN_ROLE_OUTPUT:
                continue

            output_net = pin_to_net.get((component.ref, pin_number))
            if output_net is None or _looks_power_like_net(output_net):
                continue

            for net_pair, bridge_members in sorted(bridges.items()):
                if output_net not in net_pair:
                    continue

                capacitor_refs = sorted(
                    item["ref"] for item in bridge_members if item["kind"] == "capacitor"
                )
                if not capacitor_refs:
                    continue

                coupled_net = net_pair[1] if net_pair[0] == output_net else net_pair[0]
                target_inputs = input_targets_by_net.get(coupled_net, [])
                if not target_inputs:
                    continue

                warning_key = (component.ref, output_net, coupled_net)
                if warning_key in warned_paths:
                    continue
                warned_paths.add(warning_key)

                warnings.append(
                    {
                        "code": "SPLIT_RAIL_INTERSTAGE_AC_COUPLING_PRESENT",
                        "message": (
                            f"Split-rail op-amp stage {component.ref} AC-couples {output_net} "
                            f"into downstream stage net {coupled_net}. This is a design choice, "
                            "not a split-rail requirement."
                        ),
                        "details": {
                            "ref": component.ref,
                            "symbol": component.symbol,
                            "output_pin": pin_number,
                            "output_net": output_net,
                            "coupled_net": coupled_net,
                            "capacitor_refs": capacitor_refs,
                            "target_inputs": [
                                f"{ref}:{input_pin}" for ref, input_pin in target_inputs
                            ],
                        },
                    }
                )

    return warnings


def _opamp_stage_topology_warnings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    if symbol_index is None:
        return []

    pin_to_net = _collect_pin_to_net(ir)
    bridges = _collect_two_pin_component_bridges(ir)
    warnings: list[dict[str, object]] = []

    for component in ir.components:
        pin_roles = _component_pin_roles(component.symbol, symbol_index)
        if not pin_roles:
            continue

        output_nets = sorted(
            {
                pin_to_net[(component.ref, pin_number)]
                for pin_number, role in pin_roles.items()
                if role == _PIN_ROLE_OUTPUT and (component.ref, pin_number) in pin_to_net
            }
        )
        noninverting_signal_nets = sorted(
            {
                pin_to_net[(component.ref, pin_number)]
                for pin_number, role in pin_roles.items()
                if role == _PIN_ROLE_NONINVERTING_INPUT
                and (component.ref, pin_number) in pin_to_net
                and not _looks_power_like_net(pin_to_net[(component.ref, pin_number)])
                and pin_to_net[(component.ref, pin_number)] not in output_nets
            }
        )
        if not output_nets or not noninverting_signal_nets:
            continue

        for pin_number, role in sorted(pin_roles.items()):
            if role != _PIN_ROLE_INVERTING_INPUT:
                continue

            inverting_net = pin_to_net.get((component.ref, pin_number))
            if inverting_net is None or _looks_power_like_net(inverting_net):
                continue
            if inverting_net in output_nets:
                continue

            feedback_resistor_refs: set[str] = set()
            shunt_resistor_refs: set[str] = set()
            other_stage_resistor_refs: set[str] = set()
            for net_pair, bridge_members in bridges.items():
                if inverting_net not in net_pair:
                    continue

                other_net = net_pair[1] if net_pair[0] == inverting_net else net_pair[0]
                resistor_refs = {
                    item["ref"] for item in bridge_members if item["kind"] == "resistor"
                }
                if not resistor_refs:
                    continue

                if other_net in output_nets:
                    feedback_resistor_refs.update(resistor_refs)
                    continue
                if _looks_power_like_net(other_net):
                    shunt_resistor_refs.update(resistor_refs)
                    continue
                other_stage_resistor_refs.update(resistor_refs)

            if not feedback_resistor_refs or shunt_resistor_refs or other_stage_resistor_refs:
                continue

            warnings.append(
                {
                    "code": "OPAMP_STAGE_TOPOLOGY_LIKELY_MISTAKEN",
                    "message": (
                        f"Op-amp {component.ref} looks like a non-inverting stage because its "
                        "non-inverting input carries signal net(s) "
                        f"{', '.join(noninverting_signal_nets)}, but inverting input pin "
                        f"{pin_number} on {inverting_net} has feedback resistor(s) "
                        f"{', '.join(sorted(feedback_resistor_refs))} and no "
                        "resistor-defined shunt/reference path."
                    ),
                    "details": {
                        "ref": component.ref,
                        "symbol": component.symbol,
                        "inverting_input_pin": pin_number,
                        "inverting_input_net": inverting_net,
                        "noninverting_signal_nets": noninverting_signal_nets,
                        "output_nets": output_nets,
                        "feedback_resistor_refs": sorted(feedback_resistor_refs),
                    },
                }
            )

    return warnings


def _timer555_pwm_warnings(ir: CircuitIR) -> list[dict[str, object]]:
    pin_to_net = _collect_pin_to_net(ir)
    component_nets = _collect_component_nets(ir)
    bridges = _collect_two_pin_component_bridges(ir)
    component_map = {component.ref: component for component in ir.components}
    warnings: list[dict[str, object]] = []

    for timer in ir.components:
        if not _looks_timer_555(timer.ref, timer.symbol, timer.value):
            continue

        context = _build_timer555_context(timer, ir, pin_to_net)
        _append_timer555_pin_role_warnings(context, warnings)
        _append_timer555_control_and_timing_warnings(
            context,
            bridges,
            component_map,
            component_nets,
            warnings,
        )
        _append_timer555_steering_warnings(context, ir, pin_to_net, warnings)
        _append_timer555_gate_and_load_warnings(context, pin_to_net, bridges, warnings)
        _append_timer555_frequency_warning(
            context,
            bridges,
            component_map,
            component_nets,
            warnings,
        )

    return warnings


def _build_timer555_context(
    timer: ComponentIR,
    ir: CircuitIR,
    pin_to_net: dict[tuple[str, str], str],
) -> _Timer555Context:
    ground_net = pin_to_net.get((timer.ref, "1"))
    trigger_net = pin_to_net.get((timer.ref, "2"))
    threshold_net = pin_to_net.get((timer.ref, "6"))
    timing_net = trigger_net if trigger_net == threshold_net else None
    if timing_net is not None and not _is_ground_net(ground_net):
        timing_net = None

    return _Timer555Context(
        timer=timer,
        ground_net=ground_net,
        trigger_net=trigger_net,
        output_net=pin_to_net.get((timer.ref, "3")),
        reset_net=pin_to_net.get((timer.ref, "4")),
        ctrl_net=pin_to_net.get((timer.ref, "5")),
        threshold_net=threshold_net,
        discharge_net=pin_to_net.get((timer.ref, "7")),
        vcc_net=pin_to_net.get((timer.ref, "8")),
        timing_net=timing_net,
        pot=next(
            (
                component
                for component in ir.components
                if _symbol_tail(component.symbol) == "r_potentiometer"
            ),
            None,
        ),
        mosfet=next(
            (
                component
                for component in ir.components
                if _looks_nmos(component.ref, component.symbol)
            ),
            None,
        ),
        connector=next(
            (
                component
                for component in ir.components
                if _symbol_tail(component.symbol) == "conn_01x02"
            ),
            None,
        ),
    )


def _append_timer555_pin_role_warnings(
    context: _Timer555Context,
    warnings: list[dict[str, object]],
) -> None:
    if not _is_ground_net(context.ground_net):
        warnings.append(
            {
                "code": "TIMER555_GROUND_PIN_INVALID",
                "message": f"555 timer {context.timer.ref} pin 1 must connect to GND.",
                "details": {"ref": context.timer.ref, "pin": "1", "net": context.ground_net},
            }
        )

    if not _is_positive_supply_net(context.vcc_net):
        warnings.append(
            {
                "code": "TIMER555_VCC_PIN_INVALID",
                "message": (
                    f"555 timer {context.timer.ref} pin 8 must connect to a positive supply."
                ),
                "details": {"ref": context.timer.ref, "pin": "8", "net": context.vcc_net},
            }
        )

    if context.reset_net != context.vcc_net:
        warnings.append(
            {
                "code": "TIMER555_RESET_NOT_TIED_HIGH",
                "message": (
                    f"555 timer {context.timer.ref} pin 4 should tie to the same supply as pin 8."
                ),
                "details": {
                    "ref": context.timer.ref,
                    "reset_net": context.reset_net,
                    "vcc_net": context.vcc_net,
                },
            }
        )

    if context.timing_net is None:
        warnings.append(
            {
                "code": "TIMER555_TIMING_NODE_SPLIT",
                "message": (
                    f"555 timer {context.timer.ref} pins 2 and 6 must share a single timing node."
                ),
                "details": {
                    "ref": context.timer.ref,
                    "trigger_net": context.trigger_net,
                    "threshold_net": context.threshold_net,
                },
            }
        )


def _append_timer555_control_and_timing_warnings(
    context: _Timer555Context,
    bridges: dict[tuple[str, str], list[dict[str, str]]],
    component_map: dict[str, ComponentIR],
    component_nets: dict[str, set[str]],
    warnings: list[dict[str, object]],
) -> None:
    if context.ctrl_net is not None:
        ctrl_ground_caps = _net_pair_bridge_refs(
            bridges,
            context.ctrl_net,
            context.ground_net or "",
            kind="capacitor",
        )
        ctrl_non_ground_caps = _timer555_non_ground_cap_targets(
            context.ctrl_net,
            component_map,
            component_nets,
        )
        if not ctrl_ground_caps:
            warnings.append(
                {
                    "code": "TIMER555_CTRL_CAP_MISSING_TO_GROUND",
                    "message": (
                        f"555 timer {context.timer.ref} control pin 5 should have "
                        "a small capacitor to GND."
                    ),
                    "details": {"ref": context.timer.ref, "ctrl_net": context.ctrl_net},
                }
            )
        if ctrl_non_ground_caps:
            warnings.append(
                {
                    "code": "TIMER555_CTRL_CAP_WRONG_TARGET",
                    "message": (
                        f"555 timer {context.timer.ref} control pin 5 capacitor "
                        "must connect only to GND."
                    ),
                    "details": {
                        "ref": context.timer.ref,
                        "ctrl_net": context.ctrl_net,
                        "non_ground_cap_targets": ctrl_non_ground_caps,
                    },
                }
            )

    if context.timing_net is None:
        return

    timing_caps_to_ground = _net_pair_bridge_refs(
        bridges,
        context.timing_net,
        context.ground_net or "",
        kind="capacitor",
    )
    timing_caps_to_vcc = _net_pair_bridge_refs(
        bridges,
        context.timing_net,
        context.vcc_net or "",
        kind="capacitor",
    )
    if not timing_caps_to_ground:
        warnings.append(
            {
                "code": "TIMER555_TIMING_CAP_NOT_TO_GROUND",
                "message": (
                    f"555 timer {context.timer.ref} timing node should connect "
                    "to GND through a capacitor."
                ),
                "details": {"ref": context.timer.ref, "timing_net": context.timing_net},
            }
        )
    if timing_caps_to_vcc:
        warnings.append(
            {
                "code": "TIMER555_TIMING_CAP_ACROSS_SUPPLY",
                "message": (
                    f"555 timer {context.timer.ref} timing capacitor appears to "
                    "bridge the timing node to VCC."
                ),
                "details": {
                    "ref": context.timer.ref,
                    "timing_net": context.timing_net,
                    "vcc_net": context.vcc_net,
                    "capacitor_refs": timing_caps_to_vcc,
                },
            }
        )


def _timer555_non_ground_cap_targets(
    ctrl_net: str,
    component_map: dict[str, ComponentIR],
    component_nets: dict[str, set[str]],
) -> list[dict[str, object]]:
    non_ground_targets: list[dict[str, object]] = []
    for component_ref, nets in component_nets.items():
        if ctrl_net not in nets:
            continue
        component = component_map.get(component_ref)
        if component is None or _component_kind(component.ref, component.symbol) != "capacitor":
            continue
        other_nets = sorted(
            net_name for net_name in nets if net_name != ctrl_net and not _is_ground_net(net_name)
        )
        if not other_nets:
            continue
        non_ground_targets.append({"component_ref": component_ref, "other_nets": other_nets})
    return non_ground_targets


def _append_timer555_steering_warnings(
    context: _Timer555Context,
    ir: CircuitIR,
    pin_to_net: dict[tuple[str, str], str],
    warnings: list[dict[str, object]],
) -> None:
    if context.discharge_net is None:
        return

    pot_pin1_net = pin_to_net.get((context.pot.ref, "1")) if context.pot is not None else None
    pot_pin2_net = pin_to_net.get((context.pot.ref, "2")) if context.pot is not None else None
    pot_pin3_net = pin_to_net.get((context.pot.ref, "3")) if context.pot is not None else None
    diode_targets: set[str] = set()
    for component in ir.components:
        if _symbol_tail(component.symbol) != "d":
            continue
        pin1_net = pin_to_net.get((component.ref, "1"))
        pin2_net = pin_to_net.get((component.ref, "2"))
        if (
            pin1_net == context.discharge_net
            and pin2_net is not None
            and pin2_net in {pot_pin1_net, pot_pin3_net}
        ):
            diode_targets.add(pin2_net)
        if (
            pin2_net == context.discharge_net
            and pin1_net is not None
            and pin1_net in {pot_pin1_net, pot_pin3_net}
        ):
            diode_targets.add(pin1_net)

    if (
        context.pot is None
        or context.timing_net is None
        or pot_pin2_net != context.timing_net
        or diode_targets != {pot_pin1_net, pot_pin3_net}
    ):
        warnings.append(
            {
                "code": "TIMER555_STEERING_NETWORK_INVALID",
                "message": (
                    f"555 timer {context.timer.ref} does not have the expected "
                    "diode-steered potentiometer timing network."
                ),
                "details": {
                    "ref": context.timer.ref,
                    "discharge_net": context.discharge_net,
                    "timing_net": context.timing_net,
                    "pot_ref": context.pot.ref if context.pot is not None else None,
                    "pot_pin1_net": pot_pin1_net,
                    "pot_pin2_net": pot_pin2_net,
                    "pot_pin3_net": pot_pin3_net,
                    "diode_target_nets": sorted(net for net in diode_targets if net is not None),
                },
            }
        )


def _append_timer555_gate_and_load_warnings(
    context: _Timer555Context,
    pin_to_net: dict[tuple[str, str], str],
    bridges: dict[tuple[str, str], list[dict[str, str]]],
    warnings: list[dict[str, object]],
) -> None:
    if context.mosfet is None:
        return

    gate_net = pin_to_net.get((context.mosfet.ref, "1"))
    source_net = pin_to_net.get((context.mosfet.ref, "2"))
    drain_net = pin_to_net.get((context.mosfet.ref, "3"))
    if context.output_net is not None and gate_net is not None:
        gate_resistor_refs = _net_pair_bridge_refs(
            bridges,
            context.output_net,
            gate_net,
            kind="resistor",
        )
        if not gate_resistor_refs:
            warnings.append(
                {
                    "code": "TIMER555_GATE_RESISTOR_MISSING",
                    "message": (
                        f"555 timer {context.timer.ref} output should drive the "
                        "MOSFET gate through a resistor."
                    ),
                    "details": {
                        "ref": context.timer.ref,
                        "output_net": context.output_net,
                        "gate_net": gate_net,
                        "mosfet_ref": context.mosfet.ref,
                    },
                }
            )

    if gate_net is not None and context.ground_net is not None:
        gate_pulldown_refs = _net_pair_bridge_refs(
            bridges,
            gate_net,
            context.ground_net,
            kind="resistor",
        )
        if not gate_pulldown_refs:
            warnings.append(
                {
                    "code": "TIMER555_GATE_PULLDOWN_MISSING",
                    "message": (
                        f"MOSFET {context.mosfet.ref} gate should have a pull-down resistor to GND."
                    ),
                    "details": {"mosfet_ref": context.mosfet.ref, "gate_net": gate_net},
                }
            )
        gate_to_oscillator_refs = _timer555_gate_to_oscillator_refs(context, gate_net, bridges)
        if gate_to_oscillator_refs:
            warnings.append(
                {
                    "code": "TIMER555_GATE_PULLDOWN_TOUCHES_TIMING_NODE",
                    "message": (
                        f"MOSFET {context.mosfet.ref} gate pull-down must not touch "
                        "555 timing or oscillator nets."
                    ),
                    "details": {
                        "mosfet_ref": context.mosfet.ref,
                        "gate_net": gate_net,
                        "oscillator_nets": sorted(gate_to_oscillator_refs),
                    },
                }
            )

    if context.connector is None:
        return

    positive_net = pin_to_net.get((context.connector.ref, "1"))
    negative_net = pin_to_net.get((context.connector.ref, "2"))
    if (
        positive_net != context.vcc_net
        or negative_net != drain_net
        or not _is_ground_net(source_net)
    ):
        warnings.append(
            {
                "code": "TIMER555_LOW_SIDE_LOAD_TOPOLOGY_INVALID",
                "message": (
                    "555 PWM load path should read supply -> connector pin 1, "
                    "connector pin 2 -> MOSFET drain, MOSFET source -> GND."
                ),
                "details": {
                    "connector_ref": context.connector.ref,
                    "positive_net": positive_net,
                    "negative_net": negative_net,
                    "drain_net": drain_net,
                    "source_net": source_net,
                    "vcc_net": context.vcc_net,
                },
            }
        )


def _append_timer555_frequency_warning(
    context: _Timer555Context,
    bridges: dict[tuple[str, str], list[dict[str, str]]],
    component_map: dict[str, ComponentIR],
    component_nets: dict[str, set[str]],
    warnings: list[dict[str, object]],
) -> None:
    if context.pot is None or context.discharge_net is None:
        return

    timing_cap_refs = _timer555_frequency_capacitor_refs(
        context,
        bridges,
        component_map,
        component_nets,
    )
    timing_cap_value = next(
        (
            _parse_scalar_value(component_map[ref].value)
            for ref in timing_cap_refs
            if ref in component_map
        ),
        None,
    )
    pot_value = _parse_scalar_value(context.pot.value)
    fixed_resistor_total = sum(
        _parse_scalar_value(component_map[ref].value) or 0.0
        for ref in _net_pair_bridge_refs(
            bridges,
            context.vcc_net or "",
            context.discharge_net,
            kind="resistor",
        )
        if ref in component_map
    )
    if not (timing_cap_value and pot_value and fixed_resistor_total):
        return

    estimated_frequency_hz = 1.44 / ((fixed_resistor_total + pot_value) * timing_cap_value)
    if estimated_frequency_hz < 500.0 or estimated_frequency_hz > 2000.0:
        warnings.append(
            {
                "code": "TIMER555_PWM_FREQUENCY_OUT_OF_RANGE",
                "message": (
                    f"555 PWM timing values estimate {estimated_frequency_hz:.1f} Hz, "
                    "outside the 500 Hz to 2 kHz target range."
                ),
                "details": {
                    "ref": context.timer.ref,
                    "estimated_frequency_hz": round(estimated_frequency_hz, 2),
                    "timing_capacitor_refs": timing_cap_refs,
                    "fixed_resistance_ohms": round(fixed_resistor_total, 2),
                    "pot_resistance_ohms": round(pot_value, 2),
                },
            }
        )


def _timer555_frequency_capacitor_refs(
    context: _Timer555Context,
    bridges: dict[tuple[str, str], list[dict[str, str]]],
    component_map: dict[str, ComponentIR],
    component_nets: dict[str, set[str]],
) -> list[str]:
    if context.timing_net is not None:
        timing_refs = _net_pair_bridge_refs(
            bridges,
            context.timing_net,
            context.ground_net or "",
            kind="capacitor",
        )
        if timing_refs:
            return timing_refs

    fallback_refs = [
        component_ref
        for component_ref, nets in component_nets.items()
        if component_ref in component_map
        and _component_kind(component_ref, component_map[component_ref].symbol) == "capacitor"
        and (context.ground_net is not None and context.ground_net in nets)
        and (context.ctrl_net is None or context.ctrl_net not in nets)
    ]
    return sorted(
        fallback_refs,
        key=lambda ref: _parse_scalar_value(component_map[ref].value) or float("inf"),
    )


def _timer555_gate_to_oscillator_refs(
    context: _Timer555Context,
    gate_net: str,
    bridges: dict[tuple[str, str], list[dict[str, str]]],
) -> dict[str, list[str]]:
    oscillator_nets = {
        net_name
        for net_name in (
            context.timing_net,
            context.trigger_net,
            context.threshold_net,
            context.discharge_net,
            context.ctrl_net,
        )
        if net_name is not None and net_name not in {gate_net, context.ground_net}
    }
    offending: dict[str, list[str]] = {}
    for net_name in sorted(oscillator_nets):
        resistor_refs = _net_pair_bridge_refs(
            bridges,
            gate_net,
            net_name,
            kind="resistor",
        )
        if resistor_refs:
            offending[net_name] = resistor_refs
    return offending


_CIRCUIT_LINT_RULES: tuple[_CircuitLintRule, ...] = (
    _CircuitLintRule(family="generic", checker=_generic_connectivity_warnings),
    _CircuitLintRule(family="audio_connector", checker=_audio_connector_warnings),
    _CircuitLintRule(family="audio_opamp", checker=_audio_opamp_warnings),
    _CircuitLintRule(
        family="timer555_pwm",
        checker=_timer555_warnings,
        severity_overrides=tuple((code, "hard_fail") for code in sorted(_TIMER555_HARD_FAIL_CODES)),
    ),
)


def advisory_findings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None = None,
) -> tuple[AdvisoryFinding, ...]:
    findings: list[AdvisoryFinding] = []
    for rule in _CIRCUIT_LINT_RULES:
        for finding in rule.checker(ir, symbol_index):
            code = finding.get("code")
            message = finding.get("message")
            if not isinstance(code, str) or not isinstance(message, str):
                continue
            details_obj = finding.get("details")
            details = details_obj if isinstance(details_obj, dict) else {}
            findings.append(
                AdvisoryFinding(
                    family=rule.family,
                    code=code,
                    message=message,
                    details=details,
                    severity=rule.severity_for(code),
                )
            )
    return tuple(findings)


def blocking_advisory_findings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None = None,
) -> tuple[AdvisoryFinding, ...]:
    return tuple(
        finding
        for finding in advisory_findings(ir, symbol_index)
        if finding.severity == "hard_fail"
    )


def raise_for_blocking_advisories(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None = None,
) -> None:
    blocking = blocking_advisory_findings(ir, symbol_index)
    if not blocking:
        return

    summary = "; ".join(f"{finding.code}: {finding.message}" for finding in blocking[:5])
    if len(blocking) > 5:
        summary += f" (and {len(blocking) - 5} more)"
    raise UserError(
        f"Circuit IR failed domain-specific lint validation: {summary}",
        code=ErrorCode.IR_SEMANTIC_INVALID,
        details={"blocking_lints": [finding.as_dict() for finding in blocking]},
    )


def full_validate(path: Path, symbol_index: SymbolIndex) -> CircuitIR:
    """Run all three validation layers on *path* and return the parsed IR.

    Layers (run in order; each raises on first failure):

    1. **Schema** — Pydantic validation of the JSON structure.
       Raises :class:`~kicad_pcb.errors.UserError` (``IR_SCHEMA_INVALID``).
    2. **Semantic** — duplicate refs/nets, zero-pin nets, unknown component refs.
       Raises :class:`~kicad_pcb.errors.UserError` (``IR_SEMANTIC_INVALID``).
    3. **Symbol + pin** — every symbol exists in *symbol_index*, every pin is
       valid for its symbol.
       Raises :class:`~kicad_pcb.errors.UserError` (``SYMBOL_NOT_FOUND`` /
       ``PIN_INVALID``).
    """
    ir = CircuitIR.load(path)  # Layer 1: schema
    validate_circuit_ir(ir)  # Layer 2: semantic
    validate_ir_symbols(ir, symbol_index)  # Layer 3: symbol + pin
    return ir


def advisory_warnings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None = None,
) -> list[dict[str, object]]:
    """Return domain lint findings as JSON-serializable warning payloads."""

    return [finding.as_dict() for finding in advisory_findings(ir, symbol_index)]

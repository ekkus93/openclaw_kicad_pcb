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

from functools import lru_cache
from pathlib import Path

from ..circuit_ir import CircuitIR
from ..component_types import is_power_net, normalize_gnd_net_name, power_rail_polarity
from ..errors import ParseError, UserError
from ..ir.validate import validate_circuit_ir, validate_ir_symbols
from ..lib_symbol import read_lib_symbol_def_flat
from ..sexpr.nodes import ListNode, StringNode
from ..sexpr.utils import walk
from ..symbol_index import SymbolIndex

_PIN_ROLE_INVERTING_INPUT = "inverting_input"
_PIN_ROLE_NONINVERTING_INPUT = "noninverting_input"
_PIN_ROLE_OUTPUT = "output"


def _component_kind(ref: str, symbol: str | None) -> str:
    ref_upper = ref.upper()
    symbol_tail = (symbol or "").rsplit(":", 1)[-1].lower()

    if ref_upper.startswith("C") or "capacitor" in symbol_tail or symbol_tail.startswith("c"):
        return "capacitor"
    if ref_upper.startswith("R") or "resistor" in symbol_tail or symbol_tail.startswith("r"):
        return "resistor"
    return "other"


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
    return dict(_symbol_pin_roles(component_symbol, symbol_index.directories))


def _collect_pin_to_net(ir: CircuitIR) -> dict[tuple[str, str], str]:
    return {(pin_ref.ref, pin_ref.pin): net.name for net in ir.nets for pin_ref in net.pins}


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
    """Return non-blocking advisory warnings for common IR mistakes.

    Checks performed:

    * **COMPONENT_NOT_IN_ANY_NET** — components absent from every net will
      appear as floating symbols in the generated schematic.
    * **SINGLE_PIN_NET** — nets with only one connected pin usually indicate
      a missing wire or a copy-paste error in the netlist.

    None of these warnings raise; callers decide whether to surface them.
    """
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

    warnings.extend(_input_coupling_bypass_warnings(ir))
    warnings.extend(_output_coupling_bypass_warnings(ir))
    warnings.extend(_ambiguous_connector_usage_warnings(ir, symbol_index))
    warnings.extend(_opamp_feedback_warnings(ir, symbol_index))
    warnings.extend(_opamp_output_sanity_warnings(ir, symbol_index))
    warnings.extend(_opamp_ac_coupled_output_load_warnings(ir, symbol_index))
    warnings.extend(_opamp_stage_topology_warnings(ir, symbol_index))

    return warnings

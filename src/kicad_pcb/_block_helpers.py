"""Block detection IR helpers, net predicates, and component classifiers."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ._block_types import (
    _DECOUPLING_VALUE_HINTS,
    _FEEDBACK_NET_HINTS,
    _INPUT_NET_HINTS,
    _OUTPUT_NET_HINTS,
    _SUPPLY_NET_HINTS,
    BlockRole,
)
from .component_types import component_type, is_ground_like_name, power_rail_polarity
from .component_types import is_power_net as _is_power_net

if TYPE_CHECKING:
    from .circuit_ir import CircuitIR


def _component_nets(ir: CircuitIR) -> dict[str, list[str]]:
    """Return ``{ref: [net_name, ...]}`` for all components in *ir*."""
    nets_by_ref: dict[str, list[str]] = {component.ref: [] for component in ir.components}
    for net in ir.nets:
        for pin in net.pins:
            nets_by_ref.setdefault(pin.ref, []).append(net.name)
    return nets_by_ref


def _signal_adjacency(ir: CircuitIR) -> dict[str, set[str]]:
    """Return component adjacency over signal nets only."""
    adjacency: dict[str, set[str]] = {component.ref: set() for component in ir.components}
    for net in ir.nets:
        if _is_supply_like_net(net.name) or len(net.pins) < 2:
            continue
        pin_refs = [pin.ref for pin in net.pins]
        for i, ref_a in enumerate(pin_refs):
            for ref_b in pin_refs[i + 1 :]:
                if ref_a == ref_b:
                    continue
                adjacency.setdefault(ref_a, set()).add(ref_b)
                adjacency.setdefault(ref_b, set()).add(ref_a)
    return adjacency


def _net_pin_index(ir: CircuitIR) -> dict[str, list[tuple[str, str]]]:
    """Return ``{net_name: [(ref, pin), ...]}`` for all nets in *ir*."""
    return {net.name: [(pin.ref, pin.pin) for pin in net.pins] for net in ir.nets}


def _net_pin_units(ir: CircuitIR) -> dict[str, list[tuple[str, str, str | None]]]:
    """Return ``{net_name: [(ref, pin, unit), ...]}`` for all nets in *ir*."""
    return {net.name: [(pin.ref, pin.pin, pin.unit) for pin in net.pins] for net in ir.nets}


def _refs_by_net(net_pins: Mapping[str, list[tuple[str, str]]]) -> dict[str, set[str]]:
    """Return ``{net_name: {ref, ...}}`` from a net pin index."""
    return {net_name: {ref for ref, _pin in pins} for net_name, pins in net_pins.items()}


def _bfs_distances(adjacency: dict[str, set[str]], seeds: list[str]) -> dict[str, int]:
    """Return BFS distances from *seeds* across *adjacency*."""
    distances: dict[str, int] = {}
    queue: deque[str] = deque()
    for seed in seeds:
        if seed not in distances:
            distances[seed] = 0
            queue.append(seed)

    while queue:
        ref = queue.popleft()
        for neighbor in adjacency.get(ref, set()):
            if neighbor in distances:
                continue
            distances[neighbor] = distances[ref] + 1
            queue.append(neighbor)
    return distances


def _has_any_hint(net_names: list[str], hints: tuple[str, ...]) -> bool:
    """Return True when any net name contains one of *hints*."""
    joined = " ".join(net_names).upper()
    return any(hint in joined for hint in hints)


def _is_ground_like_net(net_name: str) -> bool:
    """Return True when *net_name* looks like a ground net."""
    return is_ground_like_name(net_name)


def _is_supply_like_net(net_name: str) -> bool:
    """Return True for power rails, including common negative-rail aliases."""
    upper_name = net_name.upper()
    return (
        _is_power_net(net_name)
        or power_rail_polarity(net_name) is not None
        or any(hint in upper_name for hint in _SUPPLY_NET_HINTS)
    )


def _is_operational_core(ref: str, symbol: str) -> bool:
    """Return True when the component is the active op-amp/gain stage."""
    if component_type(ref) == "ic":
        return True
    return "AMPLIFIER_OPERATIONAL" in symbol.upper()


def _is_decoupling_component(ref: str, value: str, connected_nets: list[str]) -> bool:
    """Return True when the component looks like a decoupling or bypass capacitor."""
    if not ref.upper().startswith("C"):
        return False

    power_nets = [net for net in connected_nets if _is_supply_like_net(net)]
    signal_nets = [net for net in connected_nets if not _is_supply_like_net(net)]
    if not power_nets:
        return False
    if not signal_nets:
        return True

    upper_signal_nets = [net.upper() for net in signal_nets]
    has_io_like_signal = any(
        any(hint in net for hint in (*_INPUT_NET_HINTS, *_OUTPUT_NET_HINTS, *_FEEDBACK_NET_HINTS))
        for net in upper_signal_nets
    )
    if has_io_like_signal:
        return False

    has_supply_like_signal = any(
        any(hint in net for hint in (*_SUPPLY_NET_HINTS, "VREF", "BIAS", "MID"))
        for net in upper_signal_nets
    )

    upper_value = value.upper().replace(" ", "")
    if any(hint in upper_value for hint in _DECOUPLING_VALUE_HINTS):
        return has_supply_like_signal
    return len(signal_nets) == 1 and has_supply_like_signal


def _is_supply_support_component(connected_nets: list[str]) -> bool:
    """Return True when the component sits directly on a non-ground supply rail."""
    has_supply = any(
        _is_supply_like_net(net) and not _is_ground_like_net(net) for net in connected_nets
    )
    has_ground = any(_is_ground_like_net(net) for net in connected_nets)
    return has_supply and not has_ground


def _is_feedback_component(connected_nets: list[str]) -> bool:
    """Return True when the net names clearly identify a feedback leg."""
    return _has_any_hint(connected_nets, _FEEDBACK_NET_HINTS)


def _classify_by_reference(ref: str) -> BlockRole | None:
    """Classify based on component reference prefix."""
    prefix = ref.rstrip("0123456789")
    suffix = ref[len(prefix) :]

    if not suffix:
        return None

    if prefix in ("J", "P", "JP"):
        num = int(suffix) if suffix.isdigit() else 0
        if num == 3:
            return BlockRole.POWER_ENTRY
        if num in (1, 2):
            return BlockRole.INPUT
        if num >= 4:
            return BlockRole.OUTPUT

    if prefix in ("U", "IC"):
        return BlockRole.OPAMP_CORE

    return None


def _classify_by_value(ref: str, value: str) -> BlockRole | None:
    """Classify based on component value and reference type."""
    if ref.rstrip("0123456789") == "C":
        if any(val in value.lower() for val in ("100u", "100µ", "47u", "47µ", "10u", "10µ")):
            return BlockRole.DECOUPLING
        if any(val in value.lower() for val in ("10n", "100n", "1u", "2.2u")):
            return BlockRole.OUTPUT

    return None


def _classify_by_net_names(ref: str, connected_nets: list[str]) -> BlockRole | None:
    """Classify based on connected net name patterns."""
    net_str = " ".join(connected_nets).upper()

    if any(keyword in net_str for keyword in ("IN_", "INPUT", "AUDIO_IN", "AUDIO IN")):
        return BlockRole.INPUT

    if any(keyword in net_str for keyword in ("OUT_", "OUTPUT", "AUDIO_OUT", "AUDIO OUT")):
        return BlockRole.OUTPUT

    if any(_is_supply_like_net(net) and not _is_ground_like_net(net) for net in connected_nets):
        return BlockRole.POWER_ENTRY

    return None


def _is_two_pin_component(ref: str, nets_by_ref: Mapping[str, list[str]], prefix: str) -> bool:
    """Return True when *ref* matches *prefix* and touches exactly two nets."""
    return ref.upper().startswith(prefix) and len(nets_by_ref.get(ref, [])) == 2


def _other_connected_net(
    ref: str,
    current_net: str,
    nets_by_ref: Mapping[str, list[str]],
) -> str | None:
    """Return the opposite net for a two-pin component on *current_net*."""
    for net_name in nets_by_ref.get(ref, []):
        if net_name != current_net:
            return net_name
    return None


def _is_output_connector_net(
    net_name: str,
    refs_by_net: Mapping[str, set[str]],
    connector_roles: Mapping[str, str],
) -> bool:
    """Return True when *net_name* reaches an output connector."""
    return any(connector_roles.get(ref) == "output" for ref in refs_by_net.get(net_name, set()))


def _grounded_resistors_on_net(
    net_name: str,
    *,
    refs_by_net: Mapping[str, set[str]],
    nets_by_ref: Mapping[str, list[str]],
) -> list[str]:
    """Return two-pin resistor refs on *net_name* that also connect to ground."""
    grounded_refs: list[str] = []
    for ref in refs_by_net.get(net_name, set()):
        if not _is_two_pin_component(ref, nets_by_ref, "R"):
            continue
        if any(_is_ground_like_net(other_net) for other_net in nets_by_ref.get(ref, [])):
            grounded_refs.append(ref)
    return sorted(grounded_refs)

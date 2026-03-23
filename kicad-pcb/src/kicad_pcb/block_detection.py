"""Functional block detection for schematic readability improvements.

This module classifies circuit components into distinct functional blocks
(input, op-amp stage, output, power, etc.) using heuristics based on:
- Component reference prefixes (J, U, R, C, etc.)
- Component values and roles
- Net name patterns (IN, OUT, GND, VCC, etc.)
- Graph proximity to active devices and I/O connectors

Block classification enables layout constraints that enforce visual
separation of functional blocks, improving schematic readability.

Phase 1.1 of CODE_REVIEW6 readability improvements.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .circuit_ir import CircuitIR

from .component_types import component_type, is_ground_like_name, power_rail_polarity
from .component_types import is_power_net as _is_power_net
from .tier import assign_tiers, classify_connector_roles, identify_main_signal_path


class BlockRole(Enum):
    """Functional block role assignment for components."""

    INPUT = "input"
    """Input connector or input-stage component (jacks, input coupling)."""

    PRECONDITIONING = "preconditioning"
    """Input conditioning / volume control / biasing stage."""

    OPAMP_CORE = "opamp_core"
    """Operational amplifier or core active gain stage."""

    FEEDBACK = "feedback"
    """Feedback network around op-amp (resistors, caps)."""

    OUTPUT = "output"
    """Output coupling or output-stage connector."""

    POWER_ENTRY = "power_entry"
    """Power supply connector and entry point."""

    DECOUPLING = "decoupling"
    """Power supply filter and decoupling components."""


@dataclass
class BlockAssignment:
    """Assignment of a single component to a block role."""

    ref: str
    """Component reference (e.g., 'J1', 'U1', 'R5')."""

    role: BlockRole
    """Assigned functional block."""

    confidence: float = 1.0
    """Confidence score (0.0–1.0). 1.0 = certain, <1.0 = heuristic."""

    reason: str = ""
    """Human-readable explanation of why this block was chosen."""


@dataclass
class BlockLayout:
    """Layout-engine-ready block assignments with zone definitions."""

    assignments: dict[str, BlockAssignment] = field(default_factory=dict)
    """Mapping from component ref to block assignment."""

    zones: dict[BlockRole, tuple[float, float, float, float]] = field(default_factory=dict)
    """Page zones for each block: (x_min, y_min, x_max, y_max) in mm.
    Used to constrain layout engine placement."""

    def add_assignment(
        self, ref: str, role: BlockRole, confidence: float = 1.0, reason: str = ""
    ) -> None:
        """Register a component-to-block assignment."""
        self.assignments[ref] = BlockAssignment(
            ref=ref, role=role, confidence=confidence, reason=reason
        )

    def get_role(self, ref: str) -> BlockRole | None:
        """Retrieve the block role for a component."""
        assignment = self.assignments.get(ref)
        return assignment.role if assignment else None

    def components_by_role(self, role: BlockRole) -> list[str]:
        """List all components assigned to a given block role."""
        return [ref for ref, assignment in self.assignments.items() if assignment.role == role]


@dataclass(frozen=True)
class _DetectionContext:
    nets_by_ref: dict[str, list[str]]
    connector_roles: Mapping[str, str]
    path_index: dict[str, int]
    first_opamp_index: int | None
    opamp_dist: dict[str, int]
    input_dist: dict[str, int]
    output_dist: dict[str, int]


# Heuristics for block classification

_INPUT_NET_HINTS = ("IN", "INPUT", "AUDIO_IN", "LEFT_IN", "RIGHT_IN", "VOL")
_OUTPUT_NET_HINTS = ("OUT", "OUTPUT", "HP", "HEADPHONE", "BUF")
_FEEDBACK_NET_HINTS = ("FB", "INV", "NFB")
_SUPPLY_NET_HINTS = (
    "SUPPLY",
    "POWER",
)
_DECOUPLING_VALUE_HINTS = ("100N", "10U", "22U", "47U", "100U", "220U")


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
    """Classify based on component reference prefix.

    Heuristic: Power connectors (J+P/JP) and supply jacks (often J3)
    are likely power entry. Jacks J1, J2 are likely audio input.
    Op-amps are typically U*.
    """
    prefix = ref.rstrip("0123456789")
    suffix = ref[len(prefix) :]

    if not suffix:
        return None

    # Power entry: power supply jack (often third jack in audio circuits)
    if prefix in ("J", "P", "JP"):
        num = int(suffix) if suffix.isdigit() else 0
        if num == 3:
            return BlockRole.POWER_ENTRY
        # Jacks 1-2 are typically audio in
        if num in (1, 2):
            return BlockRole.INPUT
        # Jacks 4+ are typically audio out
        if num >= 4:
            return BlockRole.OUTPUT

    # Op-amps and active devices
    if prefix in ("U", "IC"):
        return BlockRole.OPAMP_CORE

    return None


def _classify_by_value(ref: str, value: str) -> BlockRole | None:
    """Classify based on component value and reference type.

    Value-only classification is intentionally conservative: the role should
    not swing solely because a resistor has a common feedback value.
    """
    # Capacitor classifications
    if ref.rstrip("0123456789") == "C":
        # Large value power supply caps (100µ, 47µ, 10µ) → decoupling
        if any(val in value.lower() for val in ("100u", "100µ", "47u", "47µ", "10u", "10µ")):
            return BlockRole.DECOUPLING
        # Smaller coupling caps → might be output coupling
        if any(val in value.lower() for val in ("10n", "100n", "1u", "2.2u")):
            return BlockRole.OUTPUT  # tentative

    return None


def _classify_by_net_names(ref: str, connected_nets: list[str]) -> BlockRole | None:
    """Classify based on connected net name patterns.

    Heuristic: Components connected to IN_*, INPUT nets → input block.
    Components on OUT_*, OUTPUT nets → output block.
    Parts on VCC, V+, V- power rails → power/decoupling (if capacitors).
    Parts connected only to power/GND → PRECONDITIONING (resistors) or DECOUPLING (caps).
    """
    net_str = " ".join(connected_nets).upper()

    if any(keyword in net_str for keyword in ("IN_", "INPUT", "AUDIO_IN", "AUDIO IN")):
        return BlockRole.INPUT

    if any(keyword in net_str for keyword in ("OUT_", "OUTPUT", "AUDIO_OUT", "AUDIO OUT")):
        return BlockRole.OUTPUT

    # Power entry: explicitly on power supply rail
    if any(_is_supply_like_net(net) and not _is_ground_like_net(net) for net in connected_nets):
        return BlockRole.POWER_ENTRY

    return None


def _path_role(
    ref: str,
    *,
    path_index: dict[str, int],
    first_opamp_index: int | None,
    connector_roles: Mapping[str, str],
    connected_nets: list[str],
) -> BlockRole | None:
    """Return a block role for refs on the main signal path when possible."""
    role: BlockRole | None = None
    if ref in path_index:
        connector_role = connector_roles.get(ref)
        if connector_role == "input":
            role = BlockRole.INPUT
        elif connector_role == "output":
            role = BlockRole.OUTPUT
        elif first_opamp_index is not None:
            index = path_index[ref]
            if index < first_opamp_index:
                role = BlockRole.INPUT if index <= 1 else BlockRole.PRECONDITIONING
            elif index > first_opamp_index:
                role = BlockRole.OUTPUT
    return role


def _distance_role(
    ref: str,
    signal_nets: list[str],
    context: _DetectionContext,
) -> tuple[BlockRole, str, float]:
    """Classify remaining signal-carrying parts by graph proximity."""
    if context.opamp_dist.get(ref, 99) <= 1 and signal_nets:
        in_dist = context.input_dist.get(ref, 99)
        out_dist = context.output_dist.get(ref, 99)
        if in_dist <= out_dist:
            return (
                BlockRole.PRECONDITIONING,
                f"Op-amp-adjacent input support (d_in={in_dist}, d_out={out_dist})",
                0.75,
            )
        return (
            BlockRole.OUTPUT,
            f"Op-amp-adjacent output support (d_in={in_dist}, d_out={out_dist})",
            0.75,
        )

    in_dist = context.input_dist.get(ref, 99)
    out_dist = context.output_dist.get(ref, 99)
    if out_dist < in_dist:
        return (
            BlockRole.OUTPUT,
            f"Closer to output than input (d_in={in_dist}, d_out={out_dist})",
            0.55,
        )
    return (
        BlockRole.PRECONDITIONING,
        f"Default support role (d_in={in_dist}, d_out={out_dist})",
        0.55,
    )


def _classify_component(
    component_ref: str,
    component_symbol: str,
    component_value: str,
    context: _DetectionContext,
) -> tuple[BlockRole, str, float]:
    """Return ``(role, reason, confidence)`` for one component."""
    connected_nets = context.nets_by_ref.get(component_ref, [])
    signal_nets = [net for net in connected_nets if not _is_supply_like_net(net)]
    power_nets = [net for net in connected_nets if _is_supply_like_net(net)]

    role: BlockRole | None = None
    reason = ""
    confidence = 0.55

    connector_role = context.connector_roles.get(component_ref)
    if connector_role == "power":
        role, reason, confidence = BlockRole.POWER_ENTRY, f"Connector role: {connector_role}", 1.0
    elif connector_role == "input":
        role, reason, confidence = BlockRole.INPUT, f"Connector role: {connector_role}", 1.0
    elif connector_role == "output":
        role, reason, confidence = BlockRole.OUTPUT, f"Connector role: {connector_role}", 1.0
    elif power_nets and not signal_nets and _is_operational_core(component_ref, component_symbol):
        role, reason, confidence = (
            BlockRole.POWER_ENTRY,
            f"Power-only support: {', '.join(connected_nets)}",
            0.85,
        )
    elif _is_operational_core(component_ref, component_symbol):
        role, reason, confidence = BlockRole.OPAMP_CORE, f"Active stage: {component_symbol}", 1.0
    elif _is_decoupling_component(component_ref, component_value, connected_nets):
        role, reason, confidence = (
            BlockRole.DECOUPLING,
            f"Power bypass on {', '.join(connected_nets)}",
            0.95,
        )
    elif _is_supply_support_component(connected_nets):
        role, reason, confidence = (
            BlockRole.POWER_ENTRY,
            f"Supply support nets: {', '.join(connected_nets)}",
            0.8,
        )

    if role is None:
        path_role = _path_role(
            component_ref,
            path_index=context.path_index,
            first_opamp_index=context.first_opamp_index,
            connector_roles=context.connector_roles,
            connected_nets=connected_nets,
        )
        if path_role is not None:
            role = path_role
            confidence = 0.9 if path_role in {BlockRole.INPUT, BlockRole.OUTPUT} else 0.8
            reason = (
                f"Main path segment: index {context.path_index[component_ref]} "
                f"of {len(context.path_index)}"
            )

    if role is None and _is_feedback_component(connected_nets):
        role, reason, confidence = (
            BlockRole.FEEDBACK,
            f"Feedback net hint: {', '.join(connected_nets)}",
            0.9,
        )

    if role is None:
        role_from_nets = _classify_by_net_names(component_ref, connected_nets)
        if role_from_nets is not None:
            role, reason, confidence = (
                role_from_nets,
                f"Net names: {', '.join(connected_nets)}",
                0.75,
            )

    if role is None:
        role_from_ref = _classify_by_reference(component_ref)
        if role_from_ref is not None:
            role, reason, confidence = role_from_ref, f"Reference pattern: {component_ref}", 0.7

    if role is None:
        role_from_val = _classify_by_value(component_ref, component_value)
        if role_from_val is not None:
            role, reason, confidence = role_from_val, f"Value heuristic: {component_value}", 0.65

    if role is None:
        role, reason, confidence = _distance_role(component_ref, signal_nets, context)

    return role, reason, confidence


def classify_circuit(ir: CircuitIR) -> BlockLayout:
    """Classify all components in a circuit into functional blocks.

    Uses a cascade of heuristics:
    1. Reference-based classification (J/U/R/C prefixes)
    2. Value-based classification (common values for feedback, decoupling)
    3. Net name analysis (IN/OUT/VCC patterns)
    4. Graph proximity refinement (future: distance-based classification)

    Args:
        ir: Circuit IR to classify

    Returns:
        BlockLayout with all assignments and zone definitions
    """
    layout = BlockLayout()
    refs = [component.ref for component in ir.components]
    nets_by_ref = _component_nets(ir)
    adjacency = _signal_adjacency(ir)
    tiers = assign_tiers(ir)
    connector_roles = classify_connector_roles(refs, tiers, ir=ir)
    main_path = identify_main_signal_path(ir, tiers=tiers)
    path_index = {ref: index for index, ref in enumerate(main_path)}

    opamp_refs = [
        component.ref
        for component in ir.components
        if _is_operational_core(component.ref, component.symbol)
    ]
    first_opamp_index = min(
        (path_index[ref] for ref in opamp_refs if ref in path_index),
        default=None,
    )

    input_seeds = sorted(ref for ref, role in connector_roles.items() if role == "input")
    output_seeds = sorted(ref for ref, role in connector_roles.items() if role == "output")
    context = _DetectionContext(
        nets_by_ref=nets_by_ref,
        connector_roles=connector_roles,
        path_index=path_index,
        first_opamp_index=first_opamp_index,
        opamp_dist=_bfs_distances(adjacency, opamp_refs),
        input_dist=_bfs_distances(adjacency, input_seeds),
        output_dist=_bfs_distances(adjacency, output_seeds),
    )

    for component in ir.components:
        role, reason, confidence = _classify_component(
            component.ref,
            component.symbol,
            component.value or "",
            context,
        )
        layout.add_assignment(component.ref, role, confidence=confidence, reason=reason)

    # Define default page zones (can be overridden by layout engine)
    _set_default_zones(layout)

    return layout


def _set_default_zones(layout: BlockLayout) -> None:
    """Set default page zones for block placement.

    A standard schematic page is ~254mm wide × 203mm tall (letter landscape).
    Divide into regions: left (input), center (op-amp), right (output),
    top (power/supply).
    """
    origin_x = 30.48
    origin_y = 50.80
    page_max_x = 287.0
    page_max_y = 200.0

    layout.zones = {
        BlockRole.INPUT: (origin_x, origin_y, 120.0, page_max_y - 15.0),
        BlockRole.PRECONDITIONING: (origin_x, origin_y, 150.0, page_max_y - 15.0),
        BlockRole.OPAMP_CORE: (120.0, origin_y + 15.0, 195.0, 170.0),
        BlockRole.FEEDBACK: (120.0, origin_y, 195.0, 170.0),
        BlockRole.OUTPUT: (185.0, origin_y, page_max_x, page_max_y - 15.0),
        BlockRole.POWER_ENTRY: (origin_x, origin_y, 175.0, 100.0),
        BlockRole.DECOUPLING: (120.0, origin_y, 220.0, 110.0),
    }


def debug_dump(layout: BlockLayout) -> str:
    """Generate human-readable debug output of block assignments.

    Returns:
        Multi-line string with component-to-block mappings and confidence scores
    """
    lines = ["Block Assignments Debug Dump", "=" * 50]

    # Group by role
    by_role: dict[BlockRole, list[BlockAssignment]] = {}
    for assignment in layout.assignments.values():
        by_role.setdefault(assignment.role, []).append(assignment)

    # Sort by role name
    for role in sorted(by_role.keys(), key=lambda r: r.value):
        lines.append(f"\n{role.value.upper()}")
        lines.append("-" * len(role.value))

        for assignment in sorted(by_role[role], key=lambda a: a.ref):
            filled = int(assignment.confidence * 10)
            empty = 10 - filled
            conf_bar = "█" * filled + "░" * empty
            lines.append(f"  {assignment.ref:8} [{conf_bar}] {assignment.reason}")

    return "\n".join(lines)

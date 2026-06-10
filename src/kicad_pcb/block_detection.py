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

from collections.abc import Mapping
from typing import TYPE_CHECKING

from ._block_helpers import (
    _bfs_distances,
    _classify_by_net_names,
    _classify_by_reference,
    _classify_by_value,
    _component_nets,
    _is_decoupling_component,
    _is_feedback_component,
    _is_operational_core,
    _is_supply_like_net,
    _is_supply_support_component,
    _net_pin_index,
    _net_pin_units,
    _refs_by_net,
    _signal_adjacency,
)
from ._block_motifs import _augment_split_unit_assignments, _build_motif_roles
from ._block_types import (
    BlockAssignment,
    BlockLayout,
    BlockRole,
    _DetectionContext,
    _MotifInputs,
    is_core_like_role,
    is_input_like_role,
    is_output_like_role,
    is_power_like_role,
)
from .tier import assign_tiers, classify_connector_roles, identify_main_signal_path

if TYPE_CHECKING:
    from .circuit_ir import CircuitIR

__all__ = [
    "BlockAssignment",
    "BlockLayout",
    "BlockRole",
    "classify_circuit",
    "debug_dump",
    "is_core_like_role",
    "is_input_like_role",
    "is_output_like_role",
    "is_power_like_role",
    "_is_supply_like_net",
]


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
    elif component_ref in context.motif_roles and (
        context.motif_roles[component_ref][0] == BlockRole.BUFFER_STAGE
    ):
        role, reason, confidence = context.motif_roles[component_ref]
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
    elif component_ref in context.motif_roles:
        role, reason, confidence = context.motif_roles[component_ref]

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
    net_pins = _net_pin_index(ir)
    net_pin_units_map = _net_pin_units(ir)
    refs_by_net = _refs_by_net(net_pins)
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
    opamp_like_refs = {
        component.ref
        for component in ir.components
        if "AMPLIFIER_OPERATIONAL" in component.symbol.upper()
    }
    motif_roles = _build_motif_roles(
        _MotifInputs(
            nets_by_ref=nets_by_ref,
            refs_by_net=refs_by_net,
            net_pins=net_pins,
            net_pin_units=net_pin_units_map,
            active_refs=opamp_like_refs,
            connector_roles=connector_roles,
        )
    )
    first_opamp_index = min(
        (path_index[ref] for ref in opamp_refs if ref in path_index),
        default=None,
    )

    input_seeds = sorted(ref for ref, role in connector_roles.items() if role == "input")
    output_seeds = sorted(ref for ref, role in connector_roles.items() if role == "output")
    context = _DetectionContext(
        nets_by_ref=nets_by_ref,
        refs_by_net=refs_by_net,
        net_pins=net_pins,
        connector_roles=connector_roles,
        path_index=path_index,
        first_opamp_index=first_opamp_index,
        opamp_dist=_bfs_distances(adjacency, opamp_refs),
        input_dist=_bfs_distances(adjacency, input_seeds),
        output_dist=_bfs_distances(adjacency, output_seeds),
        motif_roles=motif_roles,
    )

    for component in ir.components:
        role, reason, confidence = _classify_component(
            component.ref,
            component.symbol,
            component.value or "",
            context,
        )
        layout.add_assignment(component.ref, role, confidence=confidence, reason=reason)

    _augment_split_unit_assignments(
        layout,
        ir,
        net_pin_units=net_pin_units_map,
        opamp_like_refs=opamp_like_refs,
    )

    _set_default_zones(layout)

    return layout


def _set_default_zones(layout: BlockLayout) -> None:
    """Set default page zones for block placement."""
    origin_x = 30.48
    origin_y = 50.80
    page_max_x = 287.0
    page_max_y = 200.0

    layout.zones = {
        BlockRole.INPUT: (origin_x, origin_y, 120.0, page_max_y - 15.0),
        BlockRole.PRECONDITIONING: (origin_x, origin_y, 150.0, page_max_y - 15.0),
        BlockRole.OPAMP_CORE: (120.0, origin_y + 15.0, 195.0, 170.0),
        BlockRole.FEEDBACK: (120.0, origin_y, 195.0, 170.0),
        BlockRole.INTERSTAGE: (150.0, origin_y + 15.0, 225.0, 170.0),
        BlockRole.BUFFER_STAGE: (165.0, origin_y + 15.0, 235.0, 170.0),
        BlockRole.OUTPUT: (185.0, origin_y, page_max_x, page_max_y - 15.0),
        BlockRole.OUTPUT_CONDITIONING: (210.0, origin_y, page_max_x, page_max_y - 15.0),
        BlockRole.POWER_ENTRY: (origin_x, origin_y, 175.0, 100.0),
        BlockRole.DECOUPLING: (120.0, origin_y, 220.0, 110.0),
    }


def debug_dump(layout: BlockLayout) -> str:
    """Generate human-readable debug output of block assignments."""
    lines = ["Block Assignments Debug Dump", "=" * 50]

    by_role: dict[BlockRole, list[BlockAssignment]] = {}
    for assignment in layout.assignments.values():
        by_role.setdefault(assignment.role, []).append(assignment)

    for role in sorted(by_role.keys(), key=lambda r: r.value):
        lines.append(f"\n{role.value.upper()}")
        lines.append("-" * len(role.value))

        for assignment in sorted(by_role[role], key=lambda a: a.ref):
            filled = int(assignment.confidence * 10)
            empty = 10 - filled
            conf_bar = "█" * filled + "░" * empty
            lines.append(f"  {assignment.ref:8} [{conf_bar}] {assignment.reason}")

    return "\n".join(lines)

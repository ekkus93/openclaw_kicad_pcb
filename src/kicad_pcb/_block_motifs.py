"""Block detection motif recognition: op-amp/buffer stages and interstage coupling."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ._block_helpers import (
    _grounded_resistors_on_net,
    _is_ground_like_net,
    _is_output_connector_net,
    _is_supply_like_net,
    _is_two_pin_component,
    _other_connected_net,
)
from ._block_types import BlockLayout, BlockRole, _MotifInputs

if TYPE_CHECKING:
    from .circuit_ir import CircuitIR


_SINGLE_UNIT_OPAMP_PIN_ROLES = {
    "1": "out",
    "2": "inv",
    "3": "noninv",
}

_UB_PIN_ROLES = {
    "5": "noninv",
    "6": "inv",
    "7": "out",
}

_UC_PIN_ROLES = {
    "8": "out",
    "9": "inv",
    "10": "noninv",
}

_UD_PIN_ROLES = {
    "12": "noninv",
    "13": "inv",
    "14": "out",
}

_DUAL_UNIT_OPAMP_PIN_ROLES = {
    "A": _SINGLE_UNIT_OPAMP_PIN_ROLES,
    "1": _SINGLE_UNIT_OPAMP_PIN_ROLES,
    "B": _UB_PIN_ROLES,
    "2": _UB_PIN_ROLES,
    "C": _UC_PIN_ROLES,
    "3": _UC_PIN_ROLES,
    "D": _UD_PIN_ROLES,
    "4": _UD_PIN_ROLES,
}


def _opamp_stage_pin_role(
    ref: str,
    pin: str,
    unit: str | None,
) -> tuple[str, str] | None:
    """Return ``(stage_id, pin_role)`` when *pin* identifies an op-amp unit pin."""
    stage_pin_role: tuple[str, str] | None = None

    if unit is not None:
        unit_key = unit.upper()
        unit_roles = _DUAL_UNIT_OPAMP_PIN_ROLES.get(unit_key)
        if unit_roles is not None and pin in unit_roles:
            stage_pin_role = (f"{ref}:{unit_key}", unit_roles[pin])
        elif pin in _SINGLE_UNIT_OPAMP_PIN_ROLES:
            stage_pin_role = (f"{ref}:{unit_key}", _SINGLE_UNIT_OPAMP_PIN_ROLES[pin])
        return stage_pin_role

    if pin in _UD_PIN_ROLES:
        stage_pin_role = (f"{ref}:D", _UD_PIN_ROLES[pin])
    elif pin in _UC_PIN_ROLES:
        stage_pin_role = (f"{ref}:C", _UC_PIN_ROLES[pin])
    elif pin in _UB_PIN_ROLES:
        stage_pin_role = (f"{ref}:B", _UB_PIN_ROLES[pin])
    elif pin in _SINGLE_UNIT_OPAMP_PIN_ROLES:
        stage_pin_role = (f"{ref}:A", _SINGLE_UNIT_OPAMP_PIN_ROLES[pin])

    return stage_pin_role


def _buffer_stage_roles(
    *,
    net_pin_units: Mapping[str, list[tuple[str, str, str | None]]],
    opamp_like_refs: set[str],
) -> dict[str, tuple[BlockRole, str, float]]:
    """Detect explicit follower/buffer stages when unit identity is unambiguous."""
    stage_feedback_nets: dict[str, str] = {}
    stage_noninv_nets: dict[str, set[str]] = {}
    stage_ref_by_id: dict[str, str] = {}
    signal_stages_by_ref: dict[str, set[str]] = {}

    for net_name, pin_members in net_pin_units.items():
        if _is_supply_like_net(net_name):
            continue

        stage_roles_on_net: dict[str, set[str]] = {}
        for ref, pin, unit in pin_members:
            if ref not in opamp_like_refs:
                continue
            stage_pin_role = _opamp_stage_pin_role(ref, pin, unit)
            if stage_pin_role is None:
                continue

            stage_id, pin_role = stage_pin_role
            stage_ref_by_id[stage_id] = ref
            signal_stages_by_ref.setdefault(ref, set()).add(stage_id)
            stage_roles_on_net.setdefault(stage_id, set()).add(pin_role)
            if pin_role == "noninv":
                stage_noninv_nets.setdefault(stage_id, set()).add(net_name)

        for stage_id, roles_on_net in stage_roles_on_net.items():
            if {"out", "inv"}.issubset(roles_on_net):
                stage_feedback_nets[stage_id] = net_name

    buffer_roles: dict[str, tuple[BlockRole, str, float]] = {}
    for stage_id, feedback_net in stage_feedback_nets.items():
        noninv_nets = {
            net_name
            for net_name in stage_noninv_nets.get(stage_id, set())
            if net_name != feedback_net
        }
        if not noninv_nets:
            continue

        ref = stage_ref_by_id[stage_id]
        if len(signal_stages_by_ref.get(ref, set())) != 1:
            continue

        unit_name = stage_id.split(":", 1)[1]
        buffer_roles[ref] = (
            BlockRole.BUFFER_STAGE,
            f"Follower/buffer unit {unit_name} shorts output to inverting input on {feedback_net}",
            0.97,
        )

    return buffer_roles


def _buffer_stage_unit_roles(
    *,
    net_pin_units: Mapping[str, list[tuple[str, str, str | None]]],
    opamp_like_refs: set[str],
) -> dict[str, tuple[BlockRole, str, float]]:
    """Detect explicit follower stages at the individual op-amp-unit level."""
    stage_feedback_nets: dict[str, str] = {}
    stage_noninv_nets: dict[str, set[str]] = {}

    for net_name, pin_members in net_pin_units.items():
        if _is_supply_like_net(net_name):
            continue

        stage_roles_on_net: dict[str, set[str]] = {}
        for ref, pin, unit in pin_members:
            if ref not in opamp_like_refs:
                continue
            stage_pin_role = _opamp_stage_pin_role(ref, pin, unit)
            if stage_pin_role is None:
                continue

            stage_id, pin_role = stage_pin_role
            stage_roles_on_net.setdefault(stage_id, set()).add(pin_role)
            if pin_role == "noninv":
                stage_noninv_nets.setdefault(stage_id, set()).add(net_name)

        for stage_id, roles_on_net in stage_roles_on_net.items():
            if {"out", "inv"}.issubset(roles_on_net):
                stage_feedback_nets[stage_id] = net_name

    buffer_roles: dict[str, tuple[BlockRole, str, float]] = {}
    for stage_id, feedback_net in stage_feedback_nets.items():
        noninv_nets = {
            net_name
            for net_name in stage_noninv_nets.get(stage_id, set())
            if net_name != feedback_net
        }
        if not noninv_nets:
            continue

        ref, unit_name = stage_id.split(":", 1)
        buffer_roles[f"{ref}{unit_name}"] = (
            BlockRole.BUFFER_STAGE,
            f"Follower/buffer unit {unit_name} shorts output to inverting input on {feedback_net}",
            0.97,
        )

    return buffer_roles


def _augment_split_unit_assignments(
    layout: BlockLayout,
    ir: CircuitIR,
    *,
    net_pin_units: Mapping[str, list[tuple[str, str, str | None]]],
    opamp_like_refs: set[str],
) -> None:
    """Add synthetic per-unit roles for unsplit multi-unit op-amp components."""
    component_refs = {component.ref for component in ir.components}
    if not opamp_like_refs:
        return

    stage_refs_by_base: dict[str, set[str]] = {}
    for pin_members in net_pin_units.values():
        for ref, pin, unit in pin_members:
            if ref not in opamp_like_refs:
                continue
            stage_pin_role = _opamp_stage_pin_role(ref, pin, unit)
            if stage_pin_role is None:
                continue
            stage_id, _pin_role = stage_pin_role
            base_ref, unit_name = stage_id.split(":", 1)
            stage_refs_by_base.setdefault(base_ref, set()).add(f"{base_ref}{unit_name}")

    if not stage_refs_by_base:
        return

    buffer_unit_roles = _buffer_stage_unit_roles(
        net_pin_units=net_pin_units,
        opamp_like_refs=opamp_like_refs,
    )
    for base_ref, split_refs in stage_refs_by_base.items():
        base_assignment = layout.assignments.get(base_ref)
        if base_assignment is None:
            continue
        for split_ref in sorted(split_refs):
            if split_ref in component_refs or split_ref in layout.assignments:
                continue
            role, reason, confidence = buffer_unit_roles.get(
                split_ref,
                (
                    base_assignment.role,
                    f"Split signal unit inherits {base_assignment.role.value} from {base_ref}",
                    base_assignment.confidence,
                ),
            )
            layout.add_assignment(split_ref, role, confidence=confidence, reason=reason)


def _build_motif_roles(inputs: _MotifInputs) -> dict[str, tuple[BlockRole, str, float]]:
    """Precompute structural role assignments for stage handoff motifs."""
    motif_roles = _buffer_stage_roles(
        net_pin_units=inputs.net_pin_units,
        opamp_like_refs=inputs.active_refs,
    )
    buffer_output_nets = {
        net_name
        for net_name, pins in inputs.net_pins.items()
        if any(
            count >= 2
            for count in Counter(ref for ref, _pin in pins if ref in inputs.active_refs).values()
        )
    }

    for ref, connected_nets in inputs.nets_by_ref.items():
        if not _is_two_pin_component(ref, inputs.nets_by_ref, "C"):
            continue

        signal_nets = [net_name for net_name in connected_nets if not _is_supply_like_net(net_name)]
        if len(signal_nets) != 2:
            continue

        output_net = next(
            (
                net_name
                for net_name in signal_nets
                if _is_output_connector_net(
                    net_name,
                    inputs.refs_by_net,
                    inputs.connector_roles,
                )
            ),
            None,
        )
        if output_net is not None:
            inner_net = signal_nets[1] if signal_nets[0] == output_net else signal_nets[0]
            motif_roles[ref] = (
                BlockRole.OUTPUT_CONDITIONING,
                f"Output coupling into connector net {output_net}",
                0.95,
            )
            for grounded_ref in _grounded_resistors_on_net(
                output_net,
                refs_by_net=inputs.refs_by_net,
                nets_by_ref=inputs.nets_by_ref,
            ):
                motif_roles.setdefault(
                    grounded_ref,
                    (
                        BlockRole.OUTPUT_CONDITIONING,
                        f"Output-side load/bleed on {output_net}",
                        0.92,
                    ),
                )

            for member_ref in sorted(inputs.refs_by_net.get(inner_net, set())):
                if member_ref == ref or not _is_two_pin_component(
                    member_ref,
                    inputs.nets_by_ref,
                    "R",
                ):
                    continue
                if any(
                    _is_ground_like_net(net_name)
                    for net_name in inputs.nets_by_ref.get(member_ref, [])
                ):
                    continue
                upstream_net = _other_connected_net(member_ref, inner_net, inputs.nets_by_ref)
                if upstream_net is None:
                    continue
                if upstream_net in buffer_output_nets or bool(
                    inputs.refs_by_net.get(upstream_net, set()) & inputs.active_refs
                ):
                    motif_roles.setdefault(
                        member_ref,
                        (
                            BlockRole.OUTPUT_CONDITIONING,
                            f"Series output element between {upstream_net} and {inner_net}",
                            0.9,
                        ),
                    )
            continue

        for downstream_net in signal_nets:
            grounded_support = _grounded_resistors_on_net(
                downstream_net,
                refs_by_net=inputs.refs_by_net,
                nets_by_ref=inputs.nets_by_ref,
            )
            if not grounded_support:
                continue
            if _is_output_connector_net(
                downstream_net,
                inputs.refs_by_net,
                inputs.connector_roles,
            ):
                continue
            if not (inputs.refs_by_net.get(downstream_net, set()) & inputs.active_refs):
                continue

            upstream_net = signal_nets[1] if signal_nets[0] == downstream_net else signal_nets[0]
            if not (inputs.refs_by_net.get(upstream_net, set()) & inputs.active_refs):
                continue

            motif_roles.setdefault(
                ref,
                (
                    BlockRole.INTERSTAGE,
                    f"Interstage coupling between {upstream_net} and {downstream_net}",
                    0.95,
                ),
            )
            for grounded_ref in grounded_support:
                motif_roles.setdefault(
                    grounded_ref,
                    (
                        BlockRole.INTERSTAGE,
                        f"Stage-handoff bias/load on {downstream_net}",
                        0.9,
                    ),
                )
            break

    return motif_roles

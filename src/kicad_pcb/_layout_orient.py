"""Signal-flow layout: component orientation heuristics."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ._layout_graph import (
    _OP_AMP_PREFIXES,
    _PASSIVE_PREFIXES,
    _SOURCE_PREFIXES,
    GRID_COL_MM,
    _find_decoupling_caps_layout,
    _is_power_net_layout,
    _preferred_shunt_passive_rotation,
)
from .block_detection import BlockRole, is_input_like_role, is_output_like_role
from .component_types import component_type

if TYPE_CHECKING:
    from .block_detection import BlockLayout
    from .circuit_ir import CircuitIR


def _classify_passive_pins(
    ir: CircuitIR,
    placed_pin_numbers: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(refs_with_power_pin, refs_with_signal_pin)`` for components in *ir*."""
    power_refs: set[str] = set()
    signal_refs: set[str] = set()
    for net in ir.nets:
        bucket = power_refs if _is_power_net_layout(net.name) else signal_refs
        for pin in net.pins:
            if placed_pin_numbers is not None:
                allowed_pins = placed_pin_numbers.get(pin.ref)
                if allowed_pins is not None and pin.pin not in allowed_pins:
                    continue
            bucket.add(pin.ref)
    return frozenset(power_refs), frozenset(signal_refs)


def _series_passive_rotation(
    ref: str,
    positions: dict[str, tuple[float, float]],
    adjacency: dict[str, list[str]],
) -> int:
    """Return 0° or 90° for a series passive using the position heuristic."""
    if ref not in positions:
        return 0
    x, y = positions[ref]
    total_dx = total_dy = 0.0
    for nbr in adjacency.get(ref, []):
        if nbr in positions:
            nx, ny = positions[nbr]
            total_dx += abs(nx - x)
            total_dy += abs(ny - y)
    return 90 if total_dy > total_dx else 0


def _all_feedback_in_opamp_column(
    feedback_refs: list[str],
    ir: CircuitIR,
    positions: dict[str, tuple[float, float]],
) -> bool:
    """Return True if all feedback passives are in the same column as an op-amp."""
    if not feedback_refs or not positions:
        return False

    feedback_xs = [positions[ref][0] for ref in feedback_refs if ref in positions]
    if not feedback_xs:
        return False

    opamp_xs = [
        positions[comp.ref][0]
        for comp in ir.components
        if any(comp.ref.upper().startswith(p) for p in _OP_AMP_PREFIXES) and comp.ref in positions
    ]
    if not opamp_xs:
        return False

    tolerance = GRID_COL_MM / 2
    for fb_x in feedback_xs:
        near_opamp = any(abs(fb_x - opamp_x) < tolerance for opamp_x in opamp_xs)
        if not near_opamp:
            return False

    return True


def _normalize_passive_orientations_by_role(
    orientations: dict[str, int],
    ir: CircuitIR,
    block_layout: BlockLayout,
    positions: dict[str, tuple[float, float]],
) -> dict[str, int]:
    """Normalize passive orientations to ensure consistency within block roles."""
    role_groups: dict[BlockRole, list[str]] = {}
    for ref in orientations:
        role = block_layout.get_role(ref)
        if role is None:
            continue
        upper = ref.upper()
        if not any(upper.startswith(pfx) for pfx in _PASSIVE_PREFIXES):
            continue
        if role not in role_groups:
            role_groups[role] = []
        role_groups[role].append(ref)

    result = dict(orientations)
    for role, refs in role_groups.items():
        if len(refs) < 2:
            continue

        orientation_counts = Counter(orientations[ref] for ref in refs)
        most_common_rot, count_most = orientation_counts.most_common(1)[0]

        canonical_rot: int | None = None

        if role == BlockRole.FEEDBACK:
            if most_common_rot == 90 or count_most > len(refs) / 2:
                canonical_rot = 90
            else:
                all_in_opamp_col = _all_feedback_in_opamp_column(refs, ir, positions)
                canonical_rot = 90 if all_in_opamp_col else 0

        elif is_input_like_role(role) or is_output_like_role(role) or role == BlockRole.INTERSTAGE:
            canonical_rot = 0

        if canonical_rot is not None:
            for ref in refs:
                result[ref] = canonical_rot

    return result


def compute_orientations(  # noqa: PLR0912, PLR0913, PLR0915
    ir: CircuitIR,
    positions: dict[str, tuple[float, float]],
    tiers: dict[str, int] | None = None,
    roles: Mapping[str, str] | None = None,
    block_layout: BlockLayout | None = None,
    placed_pin_numbers: Mapping[str, tuple[str, ...]] | None = None,
) -> dict[str, int]:
    """Return ``{ref: rotation_degrees}`` orientation for every component.

    Rules (applied in priority order):
    - Connectors: role-based or tier-based facing
    - Op-amps/ICs: always 0°
    - Passives: block-role-aware, then shunt topology, then position heuristic
    - Diodes and others: 0°
    """
    power_pin_refs, signal_pin_refs = _classify_passive_pins(
        ir,
        placed_pin_numbers=placed_pin_numbers,
    )
    decoupling_caps = _find_decoupling_caps_layout(ir)

    adjacency: dict[str, list[str]] = defaultdict(list)
    for net in ir.nets:
        if _is_power_net_layout(net.name):
            continue
        pin_refs = [
            p.ref
            for p in net.pins
            if placed_pin_numbers is None
            or p.ref not in placed_pin_numbers
            or p.pin in placed_pin_numbers[p.ref]
        ]
        for i, r_i in enumerate(pin_refs):
            for r_j in pin_refs[i + 1 :]:
                if r_i != r_j:
                    adjacency[r_i].append(r_j)
                    adjacency[r_j].append(r_i)

    _max_tier: int = max(tiers.values()) if tiers else 0

    result: dict[str, int] = {}
    for comp in ir.components:
        ref = comp.ref
        upper = ref.upper()

        if any(upper.startswith(pfx) for pfx in _SOURCE_PREFIXES):
            if roles is not None and ref in roles:
                result[ref] = 180 if roles[ref] == "output" else 0
            elif tiers is not None and _max_tier > 0:
                result[ref] = 180 if tiers.get(ref, 0) == _max_tier else 0
            else:
                result[ref] = 0
            continue

        if any(upper.startswith(pfx) for pfx in _OP_AMP_PREFIXES):
            result[ref] = 0
            continue

        if any(upper.startswith(pfx) for pfx in _PASSIVE_PREFIXES):
            if (
                upper.startswith("C")
                and ref in decoupling_caps
                and component_type(decoupling_caps[ref]) == "ic"
            ):
                result[ref] = 0
                continue

            if ref in power_pin_refs and ref in signal_pin_refs:
                result[ref] = _preferred_shunt_passive_rotation(ir, ref)
                continue

            if block_layout is not None:
                role = block_layout.get_role(ref)
                if role is not None:
                    if role == BlockRole.FEEDBACK:
                        if ref in positions:
                            x, _y = positions[ref]
                            opamp_refs = [
                                r
                                for r in ir.components
                                if any(r.ref.upper().startswith(p) for p in _OP_AMP_PREFIXES)
                            ]
                            for opamp in opamp_refs:
                                if opamp.ref in positions:
                                    ox, _oy = positions[opamp.ref]
                                    if abs(ox - x) < GRID_COL_MM / 2:
                                        result[ref] = 90
                                        continue
                    elif (
                        is_input_like_role(role)
                        or is_output_like_role(role)
                        or role == BlockRole.INTERSTAGE
                    ):
                        position_rot = _series_passive_rotation(ref, positions, adjacency)
                        if ref in positions:
                            x, y = positions[ref]
                            total_dx = total_dy = 0.0
                            for nbr in adjacency.get(ref, []):
                                if nbr in positions:
                                    nx, ny = positions[nbr]
                                    total_dx += abs(nx - x)
                                    total_dy += abs(ny - y)
                            if total_dx > 0 and total_dy / total_dx < 1.5:
                                result[ref] = 0
                                continue
                        result[ref] = position_rot
                        continue

            result[ref] = _series_passive_rotation(ref, positions, adjacency)
            continue

        result[ref] = 0

    if block_layout is not None:
        result = _normalize_passive_orientations_by_role(result, ir, block_layout, positions)

    return result

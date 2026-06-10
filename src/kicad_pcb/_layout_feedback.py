"""Signal-flow layout: feedback detection, opamp halo, stereo channel classification."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ._layout_graph import (
    _OP_AMP_PREFIXES,
    _PASSIVE_PREFIXES,
    _is_power_net_layout,
)
from ._layout_sds import compute_signal_distance_scores

if TYPE_CHECKING:
    from .circuit_ir import CircuitIR


@dataclass
class ComponentAnnotation:
    """Metadata annotations produced by layout analysis passes."""

    feedback: bool = False
    sds: float = 0.5


def find_feedback_paths(
    ir: CircuitIR,
    tiers: dict[str, int],
    roles: Mapping[str, str] | None = None,
) -> dict[str, ComponentAnnotation]:
    """Detect passive components that act as feedback (back-edge) connections."""
    signal_nets = [n for n in ir.nets if not _is_power_net_layout(n.name) and len(n.pins) >= 2]
    signal_refs: set[str] = {p.ref for net in signal_nets for p in net.pins}

    comp_to_nets: dict[str, list] = defaultdict(list)
    for net in signal_nets:
        for pin in net.pins:
            comp_to_nets[pin.ref].append(net)

    result: dict[str, ComponentAnnotation] = {}
    for comp in ir.components:
        ref = comp.ref
        upper = ref.upper()

        if not any(upper.startswith(pfx) for pfx in _PASSIVE_PREFIXES):
            result[ref] = ComponentAnnotation(feedback=False)
            continue

        own_nets = comp_to_nets.get(ref, [])
        if len(own_nets) < 2 or ref not in signal_refs:
            result[ref] = ComponentAnnotation(feedback=False)
            continue

        is_feedback = False
        for i, net_a in enumerate(own_nets):
            others_a = {p.ref for p in net_a.pins if p.ref != ref}
            for net_b in own_nets[i + 1 :]:
                others_b = {p.ref for p in net_b.pins if p.ref != ref}
                if others_a & others_b:
                    is_feedback = True
                    break
            if is_feedback:
                break

        result[ref] = ComponentAnnotation(feedback=is_feedback)

    if roles:
        sds_scores = compute_signal_distance_scores(ir, roles)
        for ref in list(result):
            result[ref] = ComponentAnnotation(
                feedback=result[ref].feedback,
                sds=sds_scores.get(ref, 0.5),
            )

    return result


def _compute_opamp_halo(  # noqa: PLR0912
    ir: CircuitIR,
    annotations: dict[str, ComponentAnnotation],
    tiers: dict[str, int],
) -> dict[str, str]:
    """Detect passive components that belong to an op-amp's halo network."""
    power_refs: set[str] = set()
    for net in ir.nets:
        if _is_power_net_layout(net.name):
            for pin in net.pins:
                power_refs.add(pin.ref)

    sig_nbrs: dict[str, set[str]] = defaultdict(set)
    for net in ir.nets:
        if _is_power_net_layout(net.name):
            continue
        pin_refs_net = [p.ref for p in net.pins]
        for ref_i in pin_refs_net:
            for ref_j in pin_refs_net:
                if ref_i != ref_j:
                    sig_nbrs[ref_i].add(ref_j)

    result: dict[str, str] = {}
    for comp in ir.components:
        ref = comp.ref
        upper = ref.upper()

        if not any(upper.startswith(p) for p in _PASSIVE_PREFIXES):
            continue

        if ref in power_refs:
            continue

        nbrs = sig_nbrs.get(ref, set())
        ic_set = {n for n in nbrs if any(n.upper().startswith(p) for p in _OP_AMP_PREFIXES)}
        if not ic_set:
            continue

        ann = annotations.get(ref)
        is_feedback = ann is not None and ann.feedback

        non_ic_nbrs = nbrs - ic_set
        is_exclusive = len(ic_set) == 1 and not non_ic_nbrs

        if not (is_feedback or is_exclusive):
            continue

        anchor = min(
            ic_set,
            key=lambda n: (abs(tiers.get(n, 0) - tiers.get(ref, 0)), n),
        )
        result[ref] = anchor

    return result


#: Channel label type — ``"L"``, ``"R"``, or ``"mono"``.
StereoChannel = Literal["L", "R", "mono"]

#: Regex matching the stereo-channel suffix at the end of a net name.
_STEREO_SUFFIX_RE: re.Pattern[str] = re.compile(r"[_-]([LR])$", re.IGNORECASE)


def detect_stereo_channels(
    ir: CircuitIR,
) -> dict[str, StereoChannel]:
    """Classify every component as left-channel, right-channel, or mono."""
    signal_nets = [n for n in ir.nets if not _is_power_net_layout(n.name) and len(n.pins) >= 2]

    comp_channels: dict[str, set[str]] = {c.ref: set() for c in ir.components}
    for net in signal_nets:
        m = _STEREO_SUFFIX_RE.search(net.name)
        if m is None:
            continue
        letter = m.group(1).upper()
        for pin in net.pins:
            if pin.ref in comp_channels:
                comp_channels[pin.ref].add(letter)

    result: dict[str, StereoChannel] = {}
    for ref, letters in comp_channels.items():
        if letters == {"L"}:
            result[ref] = "L"
        elif letters == {"R"}:
            result[ref] = "R"
        else:
            result[ref] = "mono"

    return result

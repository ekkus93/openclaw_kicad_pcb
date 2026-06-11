"""Connector I/O role classification for the tier assignment pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._tier_graph import ConnectorRole, _is_power_net
from .component_types import component_type, power_rail_polarity

if TYPE_CHECKING:  # pragma: no cover
    from .circuit_ir import CircuitIR


def _has_connector_hint(text: str, hints: tuple[str, ...]) -> bool:
    """Return True when *text* contains any whole-word-ish connector hint."""
    upper = f" {text.upper().replace('-', ' ').replace('_', ' ')} "
    return any(hint in upper for hint in hints)


def _infer_connector_roles_from_ir(
    ir: CircuitIR,
    connectors: list[str],
) -> dict[str, ConnectorRole]:
    """Infer connector roles from component metadata and connected net names.

    Topology is still the primary signal-flow signal, but some circuits expose
    clearer evidence in connector metadata than in the tier graph alone.  This
    helper recognizes two high-confidence cases that matter for audio fixtures:

    * connectors attached only to power rails are ``"power"``;
    * connectors whose symbol/value or signal-net names explicitly say
      ``IN``/``OUT`` are classified accordingly.
    """
    input_hints = (" IN ", " INPUT ", " AUDIO IN ", " TRS IN ")
    output_hints = (
        " OUT ",
        " OUTPUT ",
        " AUDIO OUT ",
        " TRS OUT ",
        " HP OUT ",
        " LOAD ",
        " LED LOAD ",
    )

    def _is_supply_alias(net_name: str) -> bool:
        return power_rail_polarity(net_name) is not None

    ref_to_component = {component.ref: component for component in ir.components}
    ref_to_nets: dict[str, list[str]] = {ref: [] for ref in connectors}
    for net in ir.nets:
        for pin in net.pins:
            if pin.ref in ref_to_nets:
                ref_to_nets[pin.ref].append(net.name)

    inferred: dict[str, ConnectorRole] = {}
    for ref in connectors:
        connected_nets = ref_to_nets.get(ref, [])
        if not connected_nets:
            continue

        signal_nets = [
            net for net in connected_nets if not _is_power_net(net) and not _is_supply_alias(net)
        ]
        power_nets = [net for net in connected_nets if _is_power_net(net) or _is_supply_alias(net)]
        component = ref_to_component.get(ref)
        metadata = ""
        if component is not None:
            metadata = f"{component.symbol} {component.value}"

        if power_nets and not signal_nets:
            inferred[ref] = "power"
            continue

        if _has_connector_hint(metadata, output_hints) or any(
            _has_connector_hint(net, output_hints) for net in signal_nets
        ):
            inferred[ref] = "output"
            continue

        if _has_connector_hint(metadata, input_hints) or any(
            _has_connector_hint(net, input_hints) for net in signal_nets
        ):
            inferred[ref] = "input"

    return inferred


def _classify_connector_roles(
    refs: list[str],
    tiers: dict[str, int],
    *,
    ir: CircuitIR | None = None,
) -> dict[str, ConnectorRole]:
    """Return the I/O role for each connector ref based on its tier position.

    Uses the tier values from :func:`~kicad_pcb._tier_graph._longest_path_dp`
    to classify connectors as *input* (signal source) or *output* (signal sink):

    * **input** — connector at tier ``0``; it is the BFS seed and sits at the
      leftmost position in the signal-flow layout.
    * **output** — connector at ``max_tier`` (where ``max_tier > 0``); it is
      the farthest-downstream component and belongs on the right edge of the
      schematic.
    * **power** — connector attached only to power rails.
    * **unknown** — connector at any intermediate tier (not 0, not max_tier),
      or when ``max_tier == 0`` and the circuit is a single-tier graph.

    Parameters
    ----------
    refs:
        All component references in the circuit.
    tiers:
        ``{ref: tier_index}`` from :func:`~kicad_pcb._tier_graph._longest_path_dp`.

    Returns
    -------
    dict[str, ConnectorRole]
        One entry per connector ref in *refs*.
        Non-connector refs are not included in the result.
    """
    connectors = [r for r in refs if component_type(r) == "connector"]
    if not connectors:
        return {}

    max_tier = max(tiers.values(), default=0)
    result: dict[str, ConnectorRole] = {}
    for ref in connectors:
        t = tiers.get(ref, 0)
        if t == max_tier and max_tier > 0:
            result[ref] = "output"
        elif t == 0:
            result[ref] = "input"
        else:
            result[ref] = "unknown"

    if ir is not None:
        result.update(_infer_connector_roles_from_ir(ir, connectors))

    return result

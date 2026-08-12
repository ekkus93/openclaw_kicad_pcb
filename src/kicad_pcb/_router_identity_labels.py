"""Hidden electrical net-name labels kept separate from display labels.

The routing label policy controls what a user sees.  KiCad connectivity still
needs an electrically attached label to retain an authored signal-net name when
a route is otherwise just wires.  These helpers keep those two concerns
separate: identity labels are recorded outside ``NetRouting.labels`` and are
serialized as ordinary KiCad labels whose text effects include ``hide``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from .sch_doc.nodes import make_global_label_node, make_label_node
from .sexpr.builder import atom
from .sexpr.nodes import ListNode

if TYPE_CHECKING:
    from collections.abc import Callable

    from ._router_types import NetRouting
    from .sch_doc import SchematicDoc

_IDENTITY_LABELS_ATTRIBUTE = "_electrical_identity_labels"


@dataclass(frozen=True)
class ElectricalIdentityLabel:
    """An electrically active KiCad label whose text is intentionally hidden."""

    name: str
    x: float
    y: float
    angle: int
    global_scope: bool = False


def identity_labels(routing: NetRouting) -> tuple[ElectricalIdentityLabel, ...]:
    """Return the hidden identity labels recorded for *routing*."""
    stored = getattr(routing, _IDENTITY_LABELS_ATTRIBUTE, None)
    if stored is None:
        return ()
    return tuple(cast(list[ElectricalIdentityLabel], stored))


def append_identity_label(  # noqa: PLR0913
    routing: NetRouting,
    *,
    name: str,
    x: float,
    y: float,
    angle: int,
    global_scope: bool = False,
) -> None:
    """Record one hidden identity label, preserving deterministic deduplication."""
    label = ElectricalIdentityLabel(name, x, y, angle, global_scope)
    stored = getattr(routing, _IDENTITY_LABELS_ATTRIBUTE, None)
    if stored is None:
        stored = []
        setattr(routing, _IDENTITY_LABELS_ATTRIBUTE, stored)
    labels = cast(list[ElectricalIdentityLabel], stored)
    if label not in labels:
        labels.append(label)


def identity_label_points(routing: NetRouting) -> set[tuple[float, float]]:
    """Return rounded attachment points occupied by hidden identity labels."""
    return {(round(label.x, 2), round(label.y, 2)) for label in identity_labels(routing)}


def _hide_label_text(node: ListNode) -> ListNode:
    """Return *node* with KiCad's ``hide`` token added to its text effects."""
    items = list(node.items)
    for index, item in enumerate(items):
        if isinstance(item, ListNode) and item.key == "effects":
            if any(getattr(effect_item, "value", None) == "hide" for effect_item in item.items):
                return node
            items[index] = ListNode(item.items + (atom("hide"),), item.pos)
            return ListNode(tuple(items), node.pos)
    raise ValueError("KiCad label node is missing its effects section")


def write_identity_labels(
    *,
    doc: SchematicDoc,
    routing: NetRouting,
    new_uuid: Callable[[], str],
) -> None:
    """Serialize hidden electrical identity labels into *doc*.

    Identity labels deliberately do not increment the visible-label statistics:
    they are generator infrastructure, not part of the selected display policy.
    """
    for label in identity_labels(routing):
        if label.global_scope:
            node = make_global_label_node(
                label.name,
                label.x,
                label.y,
                new_uuid(),
                angle=label.angle,
                shape="passive",
            )
        else:
            node = make_label_node(
                label.name,
                label.x,
                label.y,
                new_uuid(),
                angle=label.angle,
            )
        doc._insert_before_sheet_instances(_hide_label_text(node))  # noqa: SLF001

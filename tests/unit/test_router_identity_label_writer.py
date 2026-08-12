from __future__ import annotations

from itertools import count

from kicad_pcb._router_identity_labels import append_identity_label
from kicad_pcb.router import NetRouting, write_routing
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.builder import L, atom
from kicad_pcb.sexpr.nodes import AtomNode, ListNode


def _new_uuid_factory():
    sequence = count(1)

    def _new_uuid() -> str:
        return f"identity-label-{next(sequence)}"

    return _new_uuid


def _has_hide_effect(node: ListNode) -> bool:
    effects = next(
        item for item in node.items if isinstance(item, ListNode) and item.key == "effects"
    )
    return any(
        isinstance(effect_item, AtomNode) and effect_item.value == "hide"
        for effect_item in effects.items
    )


def test_public_writer_emits_hidden_local_identity_label_without_visible_stats() -> None:
    doc = SchematicDoc(L(atom("kicad_sch")))
    routing = NetRouting()
    append_identity_label(routing, name="VMID", x=10.0, y=20.0, angle=180)
    stats: dict[str, int] = {}

    write_routing(doc=doc, routing=routing, new_uuid=_new_uuid_factory(), stats=stats)

    labels = [
        item for item in doc.root.items if isinstance(item, ListNode) and item.key == "label"
    ]
    assert len(labels) == 1
    assert _has_hide_effect(labels[0])
    assert stats.get("labels", 0) == 0


def test_public_writer_emits_hidden_global_identity_label() -> None:
    doc = SchematicDoc(L(atom("kicad_sch")))
    routing = NetRouting()
    append_identity_label(
        routing,
        name="/SHARED_SIGNAL",
        x=10.0,
        y=20.0,
        angle=180,
        global_scope=True,
    )

    write_routing(doc=doc, routing=routing, new_uuid=_new_uuid_factory(), stats={})

    labels = [
        item
        for item in doc.root.items
        if isinstance(item, ListNode) and item.key == "global_label"
    ]
    assert len(labels) == 1
    assert _has_hide_effect(labels[0])

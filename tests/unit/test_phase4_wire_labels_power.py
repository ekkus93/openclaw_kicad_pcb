"""Phase 4 wire labels: power symbol routing and placement tests."""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from kicad_pcb.commands._project import minimal_schematic_text
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.router import (
    BindMarker,
    GlobalLabelPlacement,
    NetRouting,
    PowerSymbolPlacement,
    write_routing,
)
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr import parse
from kicad_pcb.sexpr.nodes import ListNode, StringNode
from kicad_pcb.sexpr.utils import find_first

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Phase 3 — Power net strategy: PowerSymbolPlacement + write_routing
# ---------------------------------------------------------------------------


_UUID_COUNTER = itertools.count(1)


def _next_test_uuid() -> str:
    return f"test-uuid-{next(_UUID_COUNTER):04d}"


def _make_sch_doc() -> SchematicDoc:
    """Return a fresh minimal :class:`SchematicDoc` for write_routing tests."""
    root = parse(minimal_schematic_text())
    assert isinstance(root, ListNode)
    return SchematicDoc(root)


class TestPhase3PowerSymbols:
    """Phase 3 — power net strategy: PowerSymbolPlacement and write_routing integration.

    Route-nets tests confirm that power nets produce :class:`PowerSymbolPlacement`
    objects instead of :class:`GlobalLabelPlacement`.  Write-routing tests
    confirm that :func:`write_routing` embeds the ``power:`` lib definition and
    places a proper ``(symbol ...)`` instance, with a fallback to
    ``(global_label ...)`` when the library symbol is not found.
    """

    # ------------------------------------------------------------------
    # PowerSymbolPlacement dataclass
    # ------------------------------------------------------------------

    def test_power_symbol_placement_defaults(self) -> None:
        """PowerSymbolPlacement has zero default angle and exposes net_name."""
        ps = PowerSymbolPlacement("GND", 10.0, 20.0)
        assert ps.net_name == "GND"
        assert ps.x == 10.0
        assert ps.y == 20.0
        assert ps.angle == 0

    def test_power_symbol_placement_custom_angle(self) -> None:
        ps = PowerSymbolPlacement("VCC", 0.0, 0.0, angle=90)
        assert ps.angle == 90

    # ------------------------------------------------------------------
    # write_routing — power symbol embedding (requires system KiCad libs)
    # ------------------------------------------------------------------

    def test_write_routing_embeds_power_lib_symbol(self) -> None:
        """write_routing embeds ``power:GND`` in lib_symbols and places instance."""
        doc = _make_sch_doc()
        routing = NetRouting()
        routing.power_symbols.append(PowerSymbolPlacement("GND", 50.0, 80.0))
        stats: dict[str, int] = {
            "wires": 0,
            "labels": 0,
            "global_labels": 0,
            "power_symbols": 0,
            "junctions": 0,
            "binding_markers": 0,
        }
        write_routing(doc=doc, routing=routing, new_uuid=_next_test_uuid, stats=stats)

        # lib_symbols section must contain the "power:GND" definition.
        lib_sym_section = find_first(doc.root, "lib_symbols")
        assert lib_sym_section is not None, "lib_symbols section missing after write_routing"
        embedded_ids = [
            item.items[1].value
            for item in lib_sym_section.items
            if isinstance(item, ListNode)
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
        ]
        assert "power:GND" in embedded_ids, (
            f"power:GND not embedded in lib_symbols; found: {embedded_ids}"
        )
        assert stats["power_symbols"] == 1
        assert stats["global_labels"] == 0

    def test_write_routing_places_power_symbol_instance(self) -> None:
        """write_routing places a \"symbol\" node with lib_id \"power:GND\" in the schematic."""
        doc = _make_sch_doc()
        routing = NetRouting()
        routing.power_symbols.append(PowerSymbolPlacement("GND", 30.0, 40.0))
        stats: dict[str, int] = {
            "wires": 0,
            "labels": 0,
            "global_labels": 0,
            "power_symbols": 0,
            "junctions": 0,
            "binding_markers": 0,
        }
        write_routing(doc=doc, routing=routing, new_uuid=_next_test_uuid, stats=stats)

        # A symbol instance with lib_id "power:GND" must appear in the schematic.
        placed = [
            item
            for item in doc.root.items
            if isinstance(item, ListNode)
            and item.key == "symbol"
            and any(
                isinstance(c, ListNode)
                and c.key == "lib_id"
                and len(c.items) >= 2
                and isinstance(c.items[1], StringNode)
                and c.items[1].value == "power:GND"
                for c in item.items
            )
        ]
        assert len(placed) == 1, f"Expected 1 placed power:GND instance; got {len(placed)}"

    def test_write_routing_power_symbol_fallback_to_global_label(self, tmp_path: Path) -> None:
        """Fallback to global_label when power symbol is absent from the library."""
        doc = _make_sch_doc()
        routing = NetRouting()
        # Use a net name that cannot exist in the power library.
        routing.power_symbols.append(PowerSymbolPlacement("NOT_A_REAL_NET_XYZ", 50.0, 80.0, 0))
        stats: dict[str, int] = {
            "wires": 0,
            "labels": 0,
            "global_labels": 0,
            "power_symbols": 0,
            "junctions": 0,
            "binding_markers": 0,
        }
        # Point symbols_dir to an empty temp dir → no power.kicad_sym available.
        write_routing(
            doc=doc,
            routing=routing,
            new_uuid=_next_test_uuid,
            stats=stats,
            symbols_dir=tmp_path,
        )
        assert stats["power_symbols"] == 0, "Expected 0 successful power symbols"
        assert stats["global_labels"] == 1, "Expected global_label fallback"
        assert stats["wires"] == 1, "Expected one orthogonal jog to the fallback label"

        placed_labels = [
            item
            for item in doc.root.items
            if isinstance(item, ListNode) and item.key == "global_label"
        ]
        assert len(placed_labels) == 1
        label_at = find_first(placed_labels[0], "at")
        assert label_at is not None
        label_x = label_at.items[1].value  # type: ignore[union-attr]
        label_y = label_at.items[2].value  # type: ignore[union-attr]
        assert (label_x, label_y) != ("50.00", "80.00")

        wires = [
            item for item in doc.root.items if isinstance(item, ListNode) and item.key == "wire"
        ]
        assert len(wires) == 1
        pts = find_first(wires[0], "pts")
        assert pts is not None
        xy1, xy2 = pts.items[1], pts.items[2]
        assert isinstance(xy1, ListNode)
        assert isinstance(xy2, ListNode)
        assert xy1.items[1].value == "50.00"  # type: ignore[union-attr]
        assert xy1.items[2].value == "80.00"  # type: ignore[union-attr]
        assert xy2.items[1].value == label_x  # type: ignore[union-attr]
        assert xy2.items[2].value == label_y  # type: ignore[union-attr]

    def test_write_routing_power_symbol_fallback_avoids_occupied_anchor(
        self, tmp_path: Path
    ) -> None:
        doc = _make_sch_doc()
        routing = NetRouting(
            global_labels=[GlobalLabelPlacement("/BUS", 50.0, 73.66, 270)],
            power_symbols=[PowerSymbolPlacement("NOT_A_REAL_NET_XYZ", 50.0, 80.0, 0)],
        )
        stats: dict[str, int] = {
            "wires": 0,
            "labels": 0,
            "global_labels": 0,
            "power_symbols": 0,
            "junctions": 0,
            "binding_markers": 0,
        }

        write_routing(
            doc=doc,
            routing=routing,
            new_uuid=_next_test_uuid,
            stats=stats,
            symbols_dir=tmp_path,
        )

        placed_labels = [
            item
            for item in doc.root.items
            if isinstance(item, ListNode)
            and item.key == "global_label"
            and isinstance(item.items[1], StringNode)
            and item.items[1].value == "NOT_A_REAL_NET_XYZ"
        ]
        assert len(placed_labels) == 1
        label_at = find_first(placed_labels[0], "at")
        assert label_at is not None
        assert not (
            label_at.items[1].value == "50.00"  # type: ignore[union-attr]
            and label_at.items[2].value == "73.66"  # type: ignore[union-attr]
        )

    def test_write_routing_strict_raises_when_power_symbol_missing(self, tmp_path: Path) -> None:
        """Strict mode must fail fast when a power symbol cannot be resolved."""
        doc = _make_sch_doc()
        routing = NetRouting()
        routing.power_symbols.append(PowerSymbolPlacement("NOT_A_REAL_NET_XYZ", 50.0, 80.0))
        stats: dict[str, int] = {
            "wires": 0,
            "labels": 0,
            "global_labels": 0,
            "power_symbols": 0,
            "junctions": 0,
            "binding_markers": 0,
        }

        with pytest.raises(UserError) as exc_info:
            write_routing(
                doc=doc,
                routing=routing,
                new_uuid=_next_test_uuid,
                stats=stats,
                symbols_dir=tmp_path,
                strict=True,
            )

        assert exc_info.value.code == ErrorCode.SYMBOL_NOT_FOUND
        assert exc_info.value.details["symbol"] == "power:NOT_A_REAL_NET_XYZ"
        assert stats["global_labels"] == 0
        assert stats["power_symbols"] == 0

    def test_write_routing_multiple_power_nets(self) -> None:
        """Multiple power symbol placements all embedded and placed correctly."""
        doc = _make_sch_doc()
        routing = NetRouting()
        for net, x in [("GND", 10.0), ("GND", 20.0), ("VCC", 30.0)]:
            routing.power_symbols.append(PowerSymbolPlacement(net, x, 50.0))
        stats: dict[str, int] = {
            "wires": 0,
            "labels": 0,
            "global_labels": 0,
            "power_symbols": 0,
            "junctions": 0,
            "binding_markers": 0,
        }
        write_routing(doc=doc, routing=routing, new_uuid=_next_test_uuid, stats=stats)
        assert stats["power_symbols"] == 3
        assert stats["global_labels"] == 0

    def test_write_routing_preserves_binding_marker_refs(self) -> None:
        doc = _make_sch_doc()
        routing = NetRouting(bind_markers=[BindMarker("U1A", "1", "IN_A")])
        stats: dict[str, int] = {
            "wires": 0,
            "labels": 0,
            "global_labels": 0,
            "power_symbols": 0,
            "junctions": 0,
            "binding_markers": 0,
        }

        write_routing(
            doc=doc,
            routing=routing,
            new_uuid=_next_test_uuid,
            stats=stats,
        )

        assert stats["binding_markers"] == 1
        assert doc.extract_pin_label_bindings() == [{"ref": "U1A", "pin": "1", "net_name": "IN_A"}]

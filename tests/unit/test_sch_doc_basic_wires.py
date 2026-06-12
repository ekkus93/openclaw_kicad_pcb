"""SchematicDoc tests — wires, labels, no-connects, pin bindings, save."""

from __future__ import annotations

from pathlib import Path

from kicad_pcb.sch_doc import (
    SchematicDoc,
)
from kicad_pcb.sexpr import find_all, find_first, parse
from kicad_pcb.sexpr.nodes import StringNode

MINIMAL_SCH = """\
(kicad_sch (version 20230121) (generator test)
  (lib_symbols)
  (sheet_instances (path "/" (page "1")))
)
"""


def _doc_from(content: str) -> SchematicDoc:
    return SchematicDoc(parse(content))


# ---------------------------------------------------------------------------
# SchematicDoc — load / construction
# ---------------------------------------------------------------------------


class TestAddWire:
    def test_wire_added_to_root(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_wire(50.8, 76.2, 76.2, 76.2, "w1")
        wires = find_all(doc.root, "wire")
        assert len(wires) == 1

    def test_wire_inserted_before_sheet_instances(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_wire(50.8, 76.2, 76.2, 76.2, "w1")
        items = doc.root.items
        w_idx = next(i for i, it in enumerate(items) if hasattr(it, "key") and it.key == "wire")
        si_idx = next(
            i for i, it in enumerate(items) if hasattr(it, "key") and it.key == "sheet_instances"
        )
        assert w_idx < si_idx

    def test_wire_has_pts(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_wire(10.0, 20.0, 30.0, 40.0, "w1")
        wires = find_all(doc.root, "wire")
        pts = find_first(wires[0], "pts")
        assert pts is not None
        xys = find_all(pts, "xy")
        assert len(xys) == 2

    def test_wire_has_uuid(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_wire(0.0, 0.0, 10.0, 0.0, "my-uuid")
        wires = find_all(doc.root, "wire")
        uuid_node = find_first(wires[0], "uuid")
        assert uuid_node is not None
        assert uuid_node.items[1].value == "my-uuid"  # type: ignore[union-attr]

    def test_multiple_wires(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_wire(0.0, 0.0, 10.0, 0.0, "w1")
        doc.add_wire(10.0, 0.0, 20.0, 0.0, "w2")
        assert len(find_all(doc.root, "wire")) == 2


# ---------------------------------------------------------------------------
# add_label
# ---------------------------------------------------------------------------


class TestAddLabel:
    def test_label_added_to_root(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_label("VCC", 60.0, 50.0, "lbl1")
        labels = find_all(doc.root, "label")
        assert len(labels) == 1

    def test_label_inserted_before_sheet_instances(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_label("GND", 60.0, 50.0, "lbl1")
        items = doc.root.items
        l_idx = next(i for i, it in enumerate(items) if hasattr(it, "key") and it.key == "label")
        si_idx = next(
            i for i, it in enumerate(items) if hasattr(it, "key") and it.key == "sheet_instances"
        )
        assert l_idx < si_idx

    def test_label_has_correct_name(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_label("VCC", 60.0, 50.0, "lbl1")
        labels = find_all(doc.root, "label")
        assert isinstance(labels[0].items[1], StringNode)
        assert labels[0].items[1].value == "VCC"

    def test_label_has_uuid(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_label("NET1", 0.0, 0.0, "lbl-uuid-99")
        labels = find_all(doc.root, "label")
        uuid_node = find_first(labels[0], "uuid")
        assert uuid_node is not None
        assert uuid_node.items[1].value == "lbl-uuid-99"  # type: ignore[union-attr]

    def test_label_has_intersheet_property(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_label("VCC", 60.0, 50.0, "lbl1")
        labels = find_all(doc.root, "label")
        props = find_all(labels[0], "property")
        assert any(p.items[1].value == "Intersheet References" for p in props)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# add_no_connect
# ---------------------------------------------------------------------------


class TestAddNoConnect:
    def test_no_connect_added_to_root(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_no_connect(60.0, 50.0, "nc1")
        markers = find_all(doc.root, "no_connect")
        assert len(markers) == 1

    def test_no_connect_has_correct_coordinates_and_uuid(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_no_connect(60.0, 50.0, "nc-uuid")
        markers = find_all(doc.root, "no_connect")
        at_node = find_first(markers[0], "at")
        uuid_node = find_first(markers[0], "uuid")

        assert at_node is not None
        assert at_node.items[1].value == "60.00"  # type: ignore[union-attr]
        assert at_node.items[2].value == "50.00"  # type: ignore[union-attr]
        assert uuid_node is not None
        assert uuid_node.items[1].value == "nc-uuid"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# extract_pin_label_bindings
# ---------------------------------------------------------------------------


class TestExtractPinLabelBindings:
    def test_returns_bindings_from_deterministic_markers(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_text(
            'OpenClaw:bind={"ref":"R1","pin":"1","net_name":"N1"}',
            -1200.0,
            -1500.0,
            hidden=True,
        )
        doc.add_text(
            'OpenClaw:bind={"ref":"R2","pin":"2","net_name":"N2"}',
            -1200.0,
            -1510.0,
            hidden=True,
        )

        bindings = doc.extract_pin_label_bindings()

        assert bindings == [
            {"ref": "R1", "pin": "1", "net_name": "N1"},
            {"ref": "R2", "pin": "2", "net_name": "N2"},
        ]

    def test_ignores_malformed_markers(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_text("OpenClaw:bind=not-json", -1200.0, -1500.0, hidden=True)
        doc.add_text('OpenClaw:bind={"ref":"R1"}', -1200.0, -1510.0, hidden=True)

        assert doc.extract_pin_label_bindings() == []


# ---------------------------------------------------------------------------
# save / round-trip
# ---------------------------------------------------------------------------


class TestSchematicDocSave:
    def test_save_and_reload(self, tmp_path: Path) -> None:
        out = tmp_path / "test.kicad_sch"
        doc = _doc_from(MINIMAL_SCH)
        doc.add_wire(50.8, 76.2, 76.2, 76.2, "w1")
        doc.save(out)
        assert out.exists()
        doc2 = SchematicDoc.load(out)
        assert len(find_all(doc2.root, "wire")) == 1

    def test_save_creates_valid_sexp(self, tmp_path: Path) -> None:
        out = tmp_path / "test.kicad_sch"
        doc = _doc_from(MINIMAL_SCH)
        doc.save(out)
        text = out.read_text()
        assert text.startswith("(kicad_sch")

    def test_save_backup_creates_bak(self, tmp_path: Path) -> None:
        out = tmp_path / "test.kicad_sch"
        out.write_text(MINIMAL_SCH)
        doc = _doc_from(MINIMAL_SCH)
        doc.save(out, backup=True)
        assert (tmp_path / "test.kicad_sch.bak").exists()

    def test_save_no_backup_by_default(self, tmp_path: Path) -> None:
        out = tmp_path / "test.kicad_sch"
        out.write_text(MINIMAL_SCH)
        doc = _doc_from(MINIMAL_SCH)
        doc.save(out)
        assert not (tmp_path / "test.kicad_sch.bak").exists()

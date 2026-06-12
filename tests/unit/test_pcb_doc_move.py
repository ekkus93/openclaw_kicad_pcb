"""Unit tests for kicad_pcb.pcb_doc — footprint movement, save, and gr_line helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb.pcb_doc import PcbDoc, make_gr_line_node
from kicad_pcb.sexpr import find_all, find_first, parse, serialize

pytestmark = pytest.mark.unit

MINIMAL_PCB = """\
(kicad_pcb (version 20230121) (generator test)
)
"""

PCB_WITH_FOOTPRINTS = """\
(kicad_pcb (version 20230121) (generator test)
  (footprint "Resistor_SMD:R_0402"
    (at 10.000 20.000)
    (property "Reference" "R1"
      (effects (font (size 1.27 1.27)))
    )
    (property "Value" "10k"
      (effects (font (size 1.27 1.27)))
    )
  )
  (footprint "Resistor_SMD:R_0402"
    (at 30.000 20.000)
    (property "Reference" "R2"
      (effects (font (size 1.27 1.27)))
    )
    (property "Value" "100k"
      (effects (font (size 1.27 1.27)))
    )
  )
)
"""

PCB_WITH_ROTATED_FOOTPRINT = """\
(kicad_pcb (version 20230121) (generator test)
  (footprint "Resistor_SMD:R_0402"
    (at 10.000 20.000 90)
    (property "Reference" "R3"
      (effects (font (size 1.27 1.27)))
    )
  )
)
"""

PCB_FOOTPRINT_NO_REF = """\
(kicad_pcb (version 20230121) (generator test)
  (footprint "Capacitor_SMD:C_0402"
    (at 5.000 5.000)
  )
)
"""


def _doc_from(content: str) -> PcbDoc:
    return PcbDoc(parse(content))


class TestMoveFootprint:
    def test_moves_footprint_updates_at(self) -> None:
        doc = _doc_from(PCB_WITH_FOOTPRINTS)
        doc.move_footprint("R1", 99.0, 88.0)
        fp = doc.find_footprint_by_ref("R1")
        assert fp is not None
        at_node = find_first(fp, "at")
        assert at_node is not None
        assert at_node.items[1].value == "99.000"  # type: ignore[union-attr]
        assert at_node.items[2].value == "88.000"  # type: ignore[union-attr]

    def test_returns_true_when_found(self) -> None:
        doc = _doc_from(PCB_WITH_FOOTPRINTS)
        assert doc.move_footprint("R1", 10.0, 10.0) is True

    def test_returns_false_when_not_found(self) -> None:
        doc = _doc_from(PCB_WITH_FOOTPRINTS)
        assert doc.move_footprint("U99", 10.0, 10.0) is False

    def test_preserves_rotation(self) -> None:
        doc = _doc_from(PCB_WITH_ROTATED_FOOTPRINT)
        doc.move_footprint("R3", 50.0, 50.0)
        fp = doc.find_footprint_by_ref("R3")
        assert fp is not None
        at_node = find_first(fp, "at")
        assert at_node is not None
        # Original rotation was 90; should still be there as third/extra item
        assert len(at_node.items) == 4
        assert at_node.items[3].value == "90"  # type: ignore[union-attr]

    def test_does_not_move_other_footprints(self) -> None:
        doc = _doc_from(PCB_WITH_FOOTPRINTS)
        doc.move_footprint("R1", 99.0, 88.0)
        fp2 = doc.find_footprint_by_ref("R2")
        assert fp2 is not None
        at_node = find_first(fp2, "at")
        assert at_node is not None
        # R2 should still be at original position 30.000, 20.000
        assert at_node.items[1].value == "30.000"  # type: ignore[union-attr]
        assert at_node.items[2].value == "20.000"  # type: ignore[union-attr]

    def test_move_preserves_child_content(self) -> None:
        doc = _doc_from(PCB_WITH_FOOTPRINTS)
        fp_before = doc.find_footprint_by_ref("R1")
        assert fp_before is not None
        n_props_before = len(find_all(fp_before, "property"))
        doc.move_footprint("R1", 20.0, 20.0)
        fp_after = doc.find_footprint_by_ref("R1")
        assert fp_after is not None
        assert len(find_all(fp_after, "property")) == n_props_before


# ---------------------------------------------------------------------------
# all_footprints
# ---------------------------------------------------------------------------


class TestAllFootprints:
    def test_returns_all_footprints(self) -> None:
        doc = _doc_from(PCB_WITH_FOOTPRINTS)
        fps = doc.all_footprints()
        assert len(fps) == 2

    def test_returns_correct_refs(self) -> None:
        doc = _doc_from(PCB_WITH_FOOTPRINTS)
        fps = doc.all_footprints()
        refs = [ref for ref, _ in fps]
        assert "R1" in refs
        assert "R2" in refs

    def test_fallback_to_lib_name_when_no_ref(self) -> None:
        doc = _doc_from(PCB_FOOTPRINT_NO_REF)
        fps = doc.all_footprints()
        assert len(fps) == 1
        ref, _node = fps[0]
        # Should fall back to the footprint lib name
        assert ref == "Capacitor_SMD:C_0402"

    def test_returns_empty_for_pcb_without_footprints(self) -> None:
        doc = _doc_from(MINIMAL_PCB)
        assert doc.all_footprints() == []


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------


class TestPcbDocSave:
    def test_save_creates_file(self, tmp_path: Path) -> None:
        out = tmp_path / "test.kicad_pcb"
        doc = _doc_from(MINIMAL_PCB)
        doc.save(out)
        assert out.exists()

    def test_save_creates_valid_sexp(self, tmp_path: Path) -> None:
        out = tmp_path / "test.kicad_pcb"
        doc = _doc_from(MINIMAL_PCB)
        doc.save(out)
        text = out.read_text()
        assert text.startswith("(kicad_pcb")

    def test_save_and_reload_preserves_outline(self, tmp_path: Path) -> None:
        out = tmp_path / "test.kicad_pcb"
        doc = _doc_from(MINIMAL_PCB)
        doc.set_rect_outline(50.0, 30.0)
        doc.save(out)
        doc2 = PcbDoc.load(out)
        assert len(find_all(doc2.root, "gr_line")) == 4

    def test_save_backup_creates_bak(self, tmp_path: Path) -> None:
        out = tmp_path / "test.kicad_pcb"
        out.write_text(MINIMAL_PCB)
        doc = _doc_from(MINIMAL_PCB)
        doc.save(out, backup=True)
        assert (tmp_path / "test.kicad_pcb.bak").exists()

    def test_save_no_backup_by_default(self, tmp_path: Path) -> None:
        out = tmp_path / "test.kicad_pcb"
        out.write_text(MINIMAL_PCB)
        doc = _doc_from(MINIMAL_PCB)
        doc.save(out)
        assert not (tmp_path / "test.kicad_pcb.bak").exists()


# ---------------------------------------------------------------------------
# make_gr_line_node (standalone)
# ---------------------------------------------------------------------------


class TestMakeGrLineNode:
    def test_key_is_gr_line(self) -> None:
        node = make_gr_line_node(0.0, 0.0, 50.0, 0.0, "uid")
        assert node.key == "gr_line"

    def test_default_layer_is_edge_cuts(self) -> None:
        node = make_gr_line_node(0.0, 0.0, 50.0, 0.0, "uid")
        layer = find_first(node, "layer")
        assert layer is not None
        assert layer.items[1].value == "Edge.Cuts"  # type: ignore[union-attr]

    def test_custom_layer(self) -> None:
        node = make_gr_line_node(0.0, 0.0, 50.0, 0.0, "uid", layer="F.SilkS")
        layer = find_first(node, "layer")
        assert layer is not None
        assert layer.items[1].value == "F.SilkS"  # type: ignore[union-attr]

    def test_start_end_coords(self) -> None:
        node = make_gr_line_node(1.0, 2.0, 3.0, 4.0, "uid")
        start = find_first(node, "start")
        end = find_first(node, "end")
        assert start is not None and end is not None
        assert start.items[1].value == "1.000"  # type: ignore[union-attr]
        assert start.items[2].value == "2.000"  # type: ignore[union-attr]
        assert end.items[1].value == "3.000"  # type: ignore[union-attr]
        assert end.items[2].value == "4.000"  # type: ignore[union-attr]

    def test_has_uuid(self) -> None:
        node = make_gr_line_node(0.0, 0.0, 10.0, 0.0, "my-line-uuid")
        uuid_node = find_first(node, "uuid")
        assert uuid_node is not None
        assert uuid_node.items[1].value == "my-line-uuid"  # type: ignore[union-attr]

    def test_serializable(self) -> None:
        node = make_gr_line_node(0.0, 0.0, 50.0, 0.0, "uid")
        text = serialize(node)
        assert "gr_line" in text
        assert "Edge.Cuts" in text

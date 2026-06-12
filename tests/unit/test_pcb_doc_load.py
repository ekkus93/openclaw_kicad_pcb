"""Unit tests for kicad_pcb.pcb_doc — PcbDoc and PCB AST helpers.

Tests use in-memory string fixtures and temporary directories; no system
KiCad installation is required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb.errors import ParseError
from kicad_pcb.pcb_doc import PcbDoc
from kicad_pcb.sexpr import find_all, find_first, parse

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

MINIMAL_PCB = """\
(kicad_pcb (version 20230121) (generator test)
)
"""

PCB_WITH_OUTLINE = """\
(kicad_pcb (version 20230121) (generator test)
  (gr_line (start 0.000 0.000) (end 50.000 0.000)
    (stroke (width 0.05) (type solid)) (layer "Edge.Cuts") (uuid "l1"))
  (gr_line (start 50.000 0.000) (end 50.000 30.000)
    (stroke (width 0.05) (type solid)) (layer "Edge.Cuts") (uuid "l2"))
  (gr_line (start 50.000 30.000) (end 0.000 30.000)
    (stroke (width 0.05) (type solid)) (layer "Edge.Cuts") (uuid "l3"))
  (gr_line (start 0.000 30.000) (end 0.000 0.000)
    (stroke (width 0.05) (type solid)) (layer "Edge.Cuts") (uuid "l4"))
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

PCB_WITH_NON_EDGE_LINE = """\
(kicad_pcb (version 20230121) (generator test)
  (gr_line (start 0.000 0.000) (end 10.000 0.000)
    (stroke (width 0.1) (type solid)) (layer "F.SilkS") (uuid "silk-1"))
  (gr_line (start 0.000 0.000) (end 50.000 0.000)
    (stroke (width 0.05) (type solid)) (layer "Edge.Cuts") (uuid "ec-1"))
)
"""

WRONG_ROOT_PCB = """\
(kicad_sch (version 20230121) (generator test)
)
"""


def _doc_from(content: str) -> PcbDoc:
    return PcbDoc(parse(content))


# ---------------------------------------------------------------------------
# PcbDoc — load / construction
# ---------------------------------------------------------------------------


class TestPcbDocLoad:
    def test_construct_valid(self) -> None:
        doc = _doc_from(MINIMAL_PCB)
        assert doc.root.key == "kicad_pcb"

    def test_construct_wrong_root_raises(self) -> None:
        with pytest.raises(ParseError, match="kicad_pcb"):
            _doc_from(WRONG_ROOT_PCB)

    def test_load_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises((ParseError, OSError, FileNotFoundError)):
            PcbDoc.load(tmp_path / "nonexistent.kicad_pcb")

    def test_load_malformed_file_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.kicad_pcb"
        p.write_text("(unclosed")
        with pytest.raises(ParseError):
            PcbDoc.load(p)

    def test_load_and_save_roundtrip(self, tmp_path: Path) -> None:
        p = tmp_path / "test.kicad_pcb"
        p.write_text(MINIMAL_PCB)
        doc = PcbDoc.load(p)
        doc.save(p)
        doc2 = PcbDoc.load(p)
        assert doc2.root.key == "kicad_pcb"


# ---------------------------------------------------------------------------
# clear_generated_outline
# ---------------------------------------------------------------------------


class TestClearGeneratedOutline:
    def test_removes_edge_cuts_gr_lines(self) -> None:
        doc = _doc_from(PCB_WITH_OUTLINE)
        lines_before = find_all(doc.root, "gr_line")
        assert len(lines_before) == 4
        doc.clear_generated_outline()
        lines_after = find_all(doc.root, "gr_line")
        assert len(lines_after) == 0

    def test_preserves_non_edge_cuts_lines(self) -> None:
        doc = _doc_from(PCB_WITH_NON_EDGE_LINE)
        doc.clear_generated_outline()
        lines = find_all(doc.root, "gr_line")
        # Only the F.SilkS line should remain
        assert len(lines) == 1
        layer = find_first(lines[0], "layer")
        assert layer is not None
        assert layer.items[1].value == "F.SilkS"  # type: ignore[union-attr]

    def test_idempotent_on_empty(self) -> None:
        doc = _doc_from(MINIMAL_PCB)
        doc.clear_generated_outline()  # should not raise
        assert find_all(doc.root, "gr_line") == []

    def test_other_items_preserved(self) -> None:
        doc = _doc_from(PCB_WITH_OUTLINE)
        n_before = len(doc.root.items)
        doc.clear_generated_outline()
        # All 4 gr_lines removed, but root structure otherwise intact
        n_after = len(doc.root.items)
        assert n_after == n_before - 4


# ---------------------------------------------------------------------------
# set_rect_outline
# ---------------------------------------------------------------------------


class TestSetRectOutline:
    def test_adds_four_gr_lines(self) -> None:
        doc = _doc_from(MINIMAL_PCB)
        doc.set_rect_outline(50.0, 30.0)
        lines = find_all(doc.root, "gr_line")
        assert len(lines) == 4

    def test_all_lines_are_edge_cuts(self) -> None:
        doc = _doc_from(MINIMAL_PCB)
        doc.set_rect_outline(50.0, 30.0)
        for line in find_all(doc.root, "gr_line"):
            layer = find_first(line, "layer")
            assert layer is not None
            assert layer.items[1].value == "Edge.Cuts"  # type: ignore[union-attr]

    def test_replaces_existing_outline(self) -> None:
        doc = _doc_from(PCB_WITH_OUTLINE)
        doc.set_rect_outline(100.0, 80.0)
        lines = find_all(doc.root, "gr_line")
        # Old 4 lines replaced with new 4
        assert len(lines) == 4

    def test_correct_corner_coordinates(self) -> None:
        doc = _doc_from(MINIMAL_PCB)
        doc.set_rect_outline(50.0, 30.0)
        lines = find_all(doc.root, "gr_line")
        # Collect all start/end coord pairs and check against expected corners
        coord_pairs: set[tuple[str, str, str, str]] = set()
        for line in lines:
            start = find_first(line, "start")
            end = find_first(line, "end")
            assert start is not None and end is not None
            sx = start.items[1].value  # type: ignore[union-attr]
            sy = start.items[2].value  # type: ignore[union-attr]
            ex = end.items[1].value  # type: ignore[union-attr]
            ey = end.items[2].value  # type: ignore[union-attr]
            coord_pairs.add((sx, sy, ex, ey))
        # All four edges of a 50×30 mm rectangle from origin
        expected = {
            ("0.000", "0.000", "50.000", "0.000"),
            ("50.000", "0.000", "50.000", "30.000"),
            ("50.000", "30.000", "0.000", "30.000"),
            ("0.000", "30.000", "0.000", "0.000"),
        }
        assert coord_pairs == expected

    def test_each_line_has_unique_uuid(self) -> None:
        doc = _doc_from(MINIMAL_PCB)
        doc.set_rect_outline(50.0, 30.0)
        uuids = [
            find_first(line, "uuid").items[1].value  # type: ignore[union-attr]
            for line in find_all(doc.root, "gr_line")
        ]
        assert len(set(uuids)) == 4  # all unique

    def test_serializable(self, tmp_path: Path) -> None:
        doc = _doc_from(MINIMAL_PCB)
        doc.set_rect_outline(50.0, 30.0)
        out = tmp_path / "out.kicad_pcb"
        doc.save(out)
        doc2 = PcbDoc.load(out)
        assert len(find_all(doc2.root, "gr_line")) == 4


# ---------------------------------------------------------------------------
# find_footprint_by_ref
# ---------------------------------------------------------------------------


class TestFindFootprintByRef:
    def test_finds_existing_ref(self) -> None:
        doc = _doc_from(PCB_WITH_FOOTPRINTS)
        fp = doc.find_footprint_by_ref("R1")
        assert fp is not None
        assert fp.key == "footprint"

    def test_returns_none_for_missing_ref(self) -> None:
        doc = _doc_from(PCB_WITH_FOOTPRINTS)
        assert doc.find_footprint_by_ref("U1") is None

    def test_returns_none_in_empty_pcb(self) -> None:
        doc = _doc_from(MINIMAL_PCB)
        assert doc.find_footprint_by_ref("R1") is None

    def test_finds_second_footprint(self) -> None:
        doc = _doc_from(PCB_WITH_FOOTPRINTS)
        fp = doc.find_footprint_by_ref("R2")
        assert fp is not None
        props = find_all(fp, "property")
        refs = [p for p in props if p.items[1].value == "Reference"]  # type: ignore[union-attr]
        assert refs and refs[0].items[2].value == "R2"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# move_footprint
# ---------------------------------------------------------------------------

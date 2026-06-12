"""Phase 3 correctness tests — lint checks (SCH010, PCB010, PCB011, suggestions)."""

from __future__ import annotations

from kicad_pcb.lint import LINT_SUGGESTIONS, LintSeverity, lint_pcb, lint_schematic
from kicad_pcb.sexpr import parse

_BASE_SCH = """\
(kicad_sch (version 20230121) (generator eeschema)
  (uuid "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
  (paper "A4")
  (lib_symbols)
  (sheet_instances
    (path "/" (page "1"))
  )
{extra})
"""


def _sch_with(extra: str) -> str:
    return _BASE_SCH.format(extra=extra)


class TestSch010LabelMissingAt:
    """SCH010 fires for label-type nodes that lack (at …)."""

    def test_label_without_at_fires_sch010(self) -> None:
        content = _sch_with('  (label "GND" (fields_autoplaced))')
        root = parse(content)
        issues = lint_schematic(root)
        codes = [i.code for i in issues]
        assert "SCH010" in codes

    def test_global_label_without_at_fires_sch010(self) -> None:
        content = _sch_with('  (global_label "VCC" (shape input))')
        root = parse(content)
        issues = lint_schematic(root)
        codes = [i.code for i in issues]
        assert "SCH010" in codes

    def test_hierarchical_label_without_at_fires_sch010(self) -> None:
        content = _sch_with('  (hierarchical_label "CLK" (shape output))')
        root = parse(content)
        issues = lint_schematic(root)
        codes = [i.code for i in issues]
        assert "SCH010" in codes

    def test_label_with_at_does_not_fire_sch010(self) -> None:
        content = _sch_with('  (label "GND" (at 50.8 76.2 0) (fields_autoplaced))')
        root = parse(content)
        issues = lint_schematic(root)
        codes = [i.code for i in issues]
        assert "SCH010" not in codes

    def test_global_label_with_at_does_not_fire(self) -> None:
        content = _sch_with('  (global_label "VCC" (at 60.0 50.0 0) (shape input))')
        root = parse(content)
        issues = lint_schematic(root)
        codes = [i.code for i in issues]
        assert "SCH010" not in codes

    def test_sch010_is_error_severity(self) -> None:
        content = _sch_with('  (label "GND" (fields_autoplaced))')
        root = parse(content)
        issues = lint_schematic(root)
        sch010 = [i for i in issues if i.code == "SCH010"]
        assert sch010, "Expected at least one SCH010 issue"
        assert all(i.severity is LintSeverity.ERROR for i in sch010)

    def test_sch010_message_contains_label_name(self) -> None:
        content = _sch_with('  (label "MY_NET" (fields_autoplaced))')
        root = parse(content)
        issues = lint_schematic(root)
        sch010 = [i for i in issues if i.code == "SCH010"]
        assert sch010
        assert "MY_NET" in sch010[0].message

    def test_clean_schematic_no_sch010(self) -> None:
        """A schematic with no labels should not trigger SCH010."""
        content = _sch_with("")
        root = parse(content)
        issues = lint_schematic(root)
        codes = [i.code for i in issues]
        assert "SCH010" not in codes


# ---------------------------------------------------------------------------
# 3.3 — PCB010: graphic primitives missing (layer …)
# ---------------------------------------------------------------------------

_BASE_PCB = """\
(kicad_pcb (version 20230121) (generator pcbnew)
  (general (thickness 1.6))
  (paper "A4")
  (layers)
  (net 0 "")
{extra})
"""


def _pcb_with(extra: str) -> str:
    return _BASE_PCB.format(extra=extra)


class TestPcb010GrPrimitiveMissingLayer:
    """PCB010 fires for gr_* nodes that lack (layer …)."""

    def test_gr_line_without_layer_fires_pcb010(self) -> None:
        content = _pcb_with("  (gr_line (start 0 0) (end 50 0) (width 0.05))")
        root = parse(content)
        issues = lint_pcb(root)
        codes = [i.code for i in issues]
        assert "PCB010" in codes

    def test_gr_arc_without_layer_fires_pcb010(self) -> None:
        content = _pcb_with("  (gr_arc (start 25 0) (end 50 25) (angle 90) (width 0.05))")
        root = parse(content)
        issues = lint_pcb(root)
        codes = [i.code for i in issues]
        assert "PCB010" in codes

    def test_gr_rect_without_layer_fires_pcb010(self) -> None:
        content = _pcb_with("  (gr_rect (start 0 0) (end 50 50) (width 0.05))")
        root = parse(content)
        issues = lint_pcb(root)
        codes = [i.code for i in issues]
        assert "PCB010" in codes

    def test_gr_poly_without_layer_fires_pcb010(self) -> None:
        content = _pcb_with("  (gr_poly (pts (xy 0 0) (xy 10 0) (xy 5 10)) (width 0.05))")
        root = parse(content)
        issues = lint_pcb(root)
        codes = [i.code for i in issues]
        assert "PCB010" in codes

    def test_gr_line_with_layer_does_not_fire_pcb010(self) -> None:
        content = _pcb_with('  (gr_line (start 0 0) (end 50 0) (layer "Edge.Cuts") (width 0.05))')
        root = parse(content)
        issues = lint_pcb(root)
        codes = [i.code for i in issues]
        assert "PCB010" not in codes

    def test_gr_arc_with_layer_does_not_fire(self) -> None:
        content = _pcb_with(
            '  (gr_arc (start 25 0) (end 50 25) (layer "F.Cu") (angle 90) (width 0.05))'
        )
        root = parse(content)
        issues = lint_pcb(root)
        codes = [i.code for i in issues]
        assert "PCB010" not in codes

    def test_pcb010_is_error_severity(self) -> None:
        content = _pcb_with("  (gr_line (start 0 0) (end 50 0) (width 0.05))")
        root = parse(content)
        issues = lint_pcb(root)
        pcb010 = [i for i in issues if i.code == "PCB010"]
        assert pcb010, "Expected at least one PCB010 issue"
        assert all(i.severity is LintSeverity.ERROR for i in pcb010)

    def test_pcb010_message_contains_primitive_name(self) -> None:
        content = _pcb_with("  (gr_arc (start 0 0) (end 10 10) (angle 90) (width 0.05))")
        root = parse(content)
        issues = lint_pcb(root)
        pcb010 = [i for i in issues if i.code == "PCB010"]
        assert pcb010
        assert "gr_arc" in pcb010[0].message

    def test_clean_pcb_no_pcb010(self) -> None:
        """A PCB with no gr_* primitives should not trigger PCB010."""
        content = _pcb_with("")
        root = parse(content)
        issues = lint_pcb(root)
        codes = [i.code for i in issues]
        assert "PCB010" not in codes


# ---------------------------------------------------------------------------
# 3.3 — PCB011: footprint pads missing (layers …)
# ---------------------------------------------------------------------------


class TestPcb011PadMissingLayers:
    """PCB011 fires for footprint pad nodes that lack (layers …)."""

    def test_pad_without_layers_fires_pcb011(self) -> None:
        content = _pcb_with(
            '  (footprint "Resistor_SMD:R_0402"\n'
            "    (at 50 50)\n"
            "    (pad 1 smd rect (at 0 0) (size 1 1))\n"
            "  )"
        )
        root = parse(content)
        issues = lint_pcb(root)
        codes = [i.code for i in issues]
        assert "PCB011" in codes

    def test_pad_with_layers_does_not_fire_pcb011(self) -> None:
        content = _pcb_with(
            '  (footprint "Resistor_SMD:R_0402"\n'
            "    (at 50 50)\n"
            '    (pad 1 smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))\n'
            "  )"
        )
        root = parse(content)
        issues = lint_pcb(root)
        codes = [i.code for i in issues]
        assert "PCB011" not in codes

    def test_pcb011_is_warning_severity(self) -> None:
        content = _pcb_with(
            '  (footprint "Device:C"\n    (at 30 30)\n    (pad 1 smd rect (at 0 0) (size 1 1))\n  )'
        )
        root = parse(content)
        issues = lint_pcb(root)
        pcb011 = [i for i in issues if i.code == "PCB011"]
        assert pcb011, "Expected at least one PCB011 issue"
        assert all(i.severity is LintSeverity.WARNING for i in pcb011)

    def test_pcb011_message_mentions_pad_number(self) -> None:
        content = _pcb_with(
            '  (footprint "Device:R"\n'
            "    (at 10 10)\n"
            "    (pad 2 thru_hole circle (at 0 0) (size 1.6 1.6) (drill 0.8))\n"
            "  )"
        )
        root = parse(content)
        issues = lint_pcb(root)
        pcb011 = [i for i in issues if i.code == "PCB011"]
        assert pcb011
        assert "2" in pcb011[0].message

    def test_multiple_pads_each_reported(self) -> None:
        """Every pad missing layers is reported independently."""
        content = _pcb_with(
            '  (footprint "Device:R"\n'
            "    (at 10 10)\n"
            "    (pad 1 smd rect (at -1 0) (size 1 1))\n"
            "    (pad 2 smd rect (at 1 0) (size 1 1))\n"
            "  )"
        )
        root = parse(content)
        issues = lint_pcb(root)
        pcb011 = [i for i in issues if i.code == "PCB011"]
        assert len(pcb011) == 2


# ---------------------------------------------------------------------------
# 3.3 — LINT_SUGGESTIONS completeness
# ---------------------------------------------------------------------------


class TestLintSuggestionsCompleteness:
    """Every lint code referenced in the lint module must have a suggestion entry."""

    def test_sch010_has_suggestion(self) -> None:
        assert "SCH010" in LINT_SUGGESTIONS
        assert LINT_SUGGESTIONS["SCH010"]

    def test_pcb010_has_suggestion(self) -> None:
        assert "PCB010" in LINT_SUGGESTIONS
        assert LINT_SUGGESTIONS["PCB010"]

    def test_pcb011_has_suggestion(self) -> None:
        assert "PCB011" in LINT_SUGGESTIONS
        assert LINT_SUGGESTIONS["PCB011"]

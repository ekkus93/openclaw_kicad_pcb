"""Phase 3 S-expression and KiCad document correctness tests.

Covers:
- 3.1 Parser strategy: kiutils is NOT a runtime dependency (dev-only).
- 3.2 AST-based skeleton builders:
    * _build_sch_skeleton produces parseable, structurally correct kicad_sch.
    * _build_pcb_skeleton produces parseable, structurally correct kicad_pcb.
    * Both skeletons round-trip cleanly (serialize → re-parse → re-serialize).
    * Skeletons pass _check_sexp (the fast paren-balance validator).
- 3.3 New lint rules:
    * SCH010 fires for label / global_label / hierarchical_label missing (at …).
    * SCH010 does NOT fire when (at …) is present.
    * PCB010 fires for gr_line / gr_arc / gr_rect / gr_poly missing (layer …).
    * PCB010 does NOT fire when (layer …) is present.
    * PCB011 fires for footprint pad missing (layers …).
    * PCB011 does NOT fire when (layers …) is present.
    * Severities are as declared (PCB010=ERROR, PCB011=WARNING).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from kicad_pcb.commands.project import (
    _PCB_LAYERS,
    _build_pcb_skeleton,
    _build_sch_skeleton,
)
from kicad_pcb.fs import _check_sexp
from kicad_pcb.lint import LINT_SUGGESTIONS, LintSeverity, lint_pcb, lint_schematic
from kicad_pcb.sexpr import parse, serialize
from kicad_pcb.sexpr.nodes import AtomNode, ListNode
from kicad_pcb.sexpr.utils import find_all, find_first

pytestmark = pytest.mark.unit

_PYPROJECT = Path(__file__).parent.parent.parent / "pyproject.toml"


# ---------------------------------------------------------------------------
# 3.1 — Parser strategy: kiutils must not be a runtime dependency
# ---------------------------------------------------------------------------


class TestParserStrategy:
    """kiutils must be restricted to the dev optional-dependency group."""

    def test_kiutils_not_in_main_dependencies(self) -> None:
        """The [project.dependencies] section must not list kiutils."""
        text = _PYPROJECT.read_text(encoding="utf-8")

        # Find the [project.dependencies] block (stop at the next header).
        in_deps = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped == "dependencies = [":
                in_deps = True
                continue
            if in_deps:
                if stripped.startswith("[") or stripped == "]":
                    break
                assert "kiutils" not in stripped, (
                    f"kiutils found in [project.dependencies]: {stripped!r}\n"
                    "kiutils should only be in [project.optional-dependencies.dev]."
                )

    def test_kiutils_in_dev_optional_dependencies(self) -> None:
        """kiutils must appear in [project.optional-dependencies.dev]."""
        text = _PYPROJECT.read_text(encoding="utf-8")
        in_dev = False
        found = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped in ("dev = [", "dev= [", "dev =["):
                in_dev = True
                continue
            if in_dev:
                if stripped.startswith("[") or stripped == "]":
                    break
                if "kiutils" in stripped:
                    found = True
                    break
        assert found, "kiutils not found in [project.optional-dependencies.dev]"

    def test_in_repo_sexpr_is_sole_runtime_parser(self) -> None:
        """The runtime kicad_pcb package must not import kiutils in any source file."""
        src_root = Path(__file__).parent.parent.parent / "kicad-pcb" / "src" / "kicad_pcb"
        violations: list[str] = []
        for py_file in sorted(src_root.rglob("*.py")):
            for line in py_file.read_text(encoding="utf-8").splitlines():
                if "import kiutils" in line or "from kiutils" in line:
                    violations.append(f"{py_file.relative_to(src_root)}: {line.strip()}")
        assert not violations, (
            "kiutils imported in runtime source (must be dev-only):\n" + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# 3.2 — SCH skeleton builder
# ---------------------------------------------------------------------------


class TestSchSkeleton:
    """_build_sch_skeleton must produce a valid, structured kicad_sch."""

    UUID = "12345678-1234-1234-1234-123456789012"

    def _build(self) -> str:
        return _build_sch_skeleton(self.UUID)

    def test_output_is_str(self) -> None:
        assert isinstance(self._build(), str)

    def test_passes_check_sexp(self) -> None:
        _check_sexp(self._build(), "kicad_sch")  # must not raise

    def test_is_parseable(self) -> None:
        root = parse(self._build())
        assert root is not None

    def test_root_node_is_kicad_sch(self) -> None:
        root = parse(self._build())
        assert root.key == "kicad_sch"

    def test_version_node_present(self) -> None:
        root = parse(self._build())
        version = find_first(root, "version")
        assert version is not None
        assert version.items[1].value == "20230121"

    def test_uuid_is_embedded(self) -> None:
        content = self._build()
        assert self.UUID in content

    def test_uuid_node_present_in_ast(self) -> None:
        root = parse(self._build())
        uuid_node = find_first(root, "uuid")
        assert uuid_node is not None
        assert uuid_node.items[1].value == self.UUID

    def test_lib_symbols_present(self) -> None:
        root = parse(self._build())
        lib_syms = find_first(root, "lib_symbols")
        assert lib_syms is not None

    def test_sheet_instances_present(self) -> None:
        root = parse(self._build())
        sheet_inst = find_first(root, "sheet_instances")
        assert sheet_inst is not None

    def test_sheet_instances_has_root_path(self) -> None:
        root = parse(self._build())
        sheet_inst = find_first(root, "sheet_instances")
        assert sheet_inst is not None
        path_node = find_first(sheet_inst, "path")
        assert path_node is not None
        assert path_node.items[1].value == "/"

    def test_round_trip_stable(self) -> None:
        """serialize → re-parse → re-serialize must produce identical output."""
        content = self._build()
        root1 = parse(content)
        content2 = serialize(root1) + "\n"
        root2 = parse(content2)
        assert serialize(root1) == serialize(root2)

    def test_different_uuids_produce_different_content(self) -> None:
        s1 = _build_sch_skeleton("aaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        s2 = _build_sch_skeleton("bbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        assert s1 != s2

    def test_lint_schematic_clean(self) -> None:
        """Generated skeleton must pass structural lint (no ERROR-level issues)."""
        root = parse(self._build())
        issues = lint_schematic(root)
        errors = [i for i in issues if i.severity is LintSeverity.ERROR]
        assert not errors, f"Skeleton schematic has lint ERRORs: {errors}"


# ---------------------------------------------------------------------------
# 3.2 — PCB skeleton builder
# ---------------------------------------------------------------------------


class TestPcbSkeleton:
    """_build_pcb_skeleton must produce a valid, structured kicad_pcb."""

    def _build(self) -> str:
        return _build_pcb_skeleton()

    def test_output_is_str(self) -> None:
        assert isinstance(self._build(), str)

    def test_passes_check_sexp(self) -> None:
        _check_sexp(self._build(), "kicad_pcb")  # must not raise

    def test_is_parseable(self) -> None:
        root = parse(self._build())
        assert root is not None

    def test_root_node_is_kicad_pcb(self) -> None:
        root = parse(self._build())
        assert root.key == "kicad_pcb"

    def test_version_node_present(self) -> None:
        root = parse(self._build())
        version = find_first(root, "version")
        assert version is not None
        assert version.items[1].value == "20230121"

    def test_general_thickness_present(self) -> None:
        root = parse(self._build())
        general = find_first(root, "general")
        assert general is not None
        thickness = find_first(general, "thickness")
        assert thickness is not None
        assert thickness.items[1].value == "1.6"

    def test_paper_present(self) -> None:
        root = parse(self._build())
        paper = find_first(root, "paper")
        assert paper is not None
        assert paper.items[1].value == "A4"

    def test_layers_present(self) -> None:
        root = parse(self._build())
        layers = find_first(root, "layers")
        assert layers is not None

    def test_layer_count_matches_table(self) -> None:
        """The number of generated layer nodes must equal len(_PCB_LAYERS)."""
        root = parse(self._build())
        layers = find_first(root, "layers")
        assert layers is not None
        # Each direct child of (layers ...) that starts with a number is a layer.
        layer_items = [
            item
            for item in layers.items[1:]
            if isinstance(item, ListNode) and item.items and isinstance(item.items[0], AtomNode)
        ]
        assert len(layer_items) == len(_PCB_LAYERS)

    def test_edge_cuts_layer_present(self) -> None:
        content = self._build()
        assert "Edge.Cuts" in content

    def test_net_zero_present(self) -> None:
        root = parse(self._build())
        net_nodes = find_all(root, "net")
        assert any(len(n.items) >= 2 and n.items[1].value == "0" for n in net_nodes), (
            "Net 0 not found in generated PCB skeleton"
        )

    def test_round_trip_stable(self) -> None:
        """serialize → re-parse → re-serialize must produce identical output."""
        content = self._build()
        root1 = parse(content)
        content2 = serialize(root1) + "\n"
        root2 = parse(content2)
        assert serialize(root1) == serialize(root2)

    def test_lint_pcb_no_unexpected_errors(self) -> None:
        """Skeleton PCB must have no ERROR-level lint issues.

        PCB005 (no Edge.Cuts outline) is expected as a WARNING; it fires because
        the skeleton has no outline geometry yet — that is correct behaviour.
        """
        root = parse(self._build())
        issues = lint_pcb(root)
        errors = [i for i in issues if i.severity is LintSeverity.ERROR]
        assert not errors, f"Skeleton PCB has lint ERRORs: {errors}"


# ---------------------------------------------------------------------------
# 3.3 — SCH010: label nodes missing (at …)
# ---------------------------------------------------------------------------

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

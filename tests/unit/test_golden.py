"""Golden file tests — round-trip stability, lint cleanliness, and bug regressions.

These tests assert three properties:

1. Round-trip stability: ``serialize(parse(golden_text)) == golden_text`` (modulo
   trailing newline) — the serializer is idempotent on canonical fixture content.

2. Lint cleanliness: valid golden schematics and PCBs pass lint with no ERROR-level
   issues.

3. Broken-fixture regressions: each broken fixture still contains its specific bug
   pattern so we know accidental "fixes" don't silently slip through.  These use
   the ``kicad_pcb.sexpr`` module directly (no kiutils dependency) to make the
   checks orthogonal to the kiutils-based tests in ``test_fixtures.py``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent.parent / "fixtures"
GOLDEN = FIXTURES / "golden"
BROKEN = FIXTURES / "broken"
WORKING = FIXTURES / "working"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_comments(text: str) -> str:
    """Remove leading-``#`` comment lines (used in broken fixture files)."""
    return "\n".join(ln for ln in text.splitlines() if not ln.startswith("#"))


def _read_golden(filename: str) -> str:
    return (GOLDEN / filename).read_text()


# ---------------------------------------------------------------------------
# 1. Round-trip stability — serialize(parse(content)) == content
# ---------------------------------------------------------------------------


class TestGoldenRoundTrip:
    """Canonical golden fixtures must survive a parse/serialize cycle unchanged."""

    # We strip a trailing newline from the file because editors add one but the
    # serializer does not emit one.  The important invariant is idempotence of
    # the *serializer output*, not the raw file bytes.

    @pytest.mark.parametrize(
        "filename",
        ["minimal.kicad_sch", "sch_with_resistor.kicad_sch"],
    )
    def test_sch_golden_is_idempotent(self, filename: str) -> None:
        from kicad_pcb.sexpr import parse, serialize

        content = _read_golden(filename).rstrip("\n")
        assert serialize(parse(content)) == content

    @pytest.mark.parametrize(
        "filename",
        ["minimal.kicad_pcb", "pcb_with_footprint.kicad_pcb"],
    )
    def test_pcb_golden_is_idempotent(self, filename: str) -> None:
        from kicad_pcb.sexpr import parse, serialize

        content = _read_golden(filename).rstrip("\n")
        assert serialize(parse(content)) == content

    def test_smoke_test_r1_double_round_trip_is_stable(self) -> None:
        """SmokeTest_R1 may not start in canonical form, but two round-trips must match."""
        from kicad_pcb.sexpr import parse, serialize

        content = (WORKING / "SmokeTest_R1.kicad_sch").read_text()
        first = serialize(parse(content))
        second = serialize(parse(first))
        assert first == second, "Double round-trip produced different output"


# ---------------------------------------------------------------------------
# 2. Lint cleanliness — valid fixtures must have no ERROR-level issues
# ---------------------------------------------------------------------------


class TestGoldenLintClean:
    """Valid golden fixtures must pass lint with no ERROR-level findings."""

    @pytest.mark.parametrize(
        "filename",
        ["minimal.kicad_sch", "sch_with_resistor.kicad_sch"],
    )
    def test_sch_golden_has_no_lint_errors(self, filename: str) -> None:
        from kicad_pcb.lint import LintSeverity, lint_schematic
        from kicad_pcb.sexpr import parse

        root = parse(_read_golden(filename).rstrip("\n"))
        errors = [i for i in lint_schematic(root) if i.severity == LintSeverity.ERROR]
        assert errors == [], f"{filename}: unexpected lint errors: {errors}"

    @pytest.mark.parametrize(
        "filename",
        ["minimal.kicad_pcb", "pcb_with_footprint.kicad_pcb"],
    )
    def test_pcb_golden_has_no_lint_errors(self, filename: str) -> None:
        from kicad_pcb.lint import LintSeverity, lint_pcb
        from kicad_pcb.sexpr import parse

        root = parse(_read_golden(filename).rstrip("\n"))
        errors = [i for i in lint_pcb(root) if i.severity == LintSeverity.ERROR]
        assert errors == [], f"{filename}: unexpected lint errors: {errors}"

    def test_smoke_test_r1_has_no_lint_errors(self) -> None:
        from kicad_pcb.lint import LintSeverity, lint_schematic
        from kicad_pcb.sexpr import parse, serialize

        # Parse round-tripped form to avoid any pre-existing formatting quirks
        content = serialize(parse((WORKING / "SmokeTest_R1.kicad_sch").read_text()))
        root = parse(content)
        errors = [i for i in lint_schematic(root) if i.severity == LintSeverity.ERROR]
        assert errors == [], f"SmokeTest_R1: unexpected lint errors: {errors}"


# ---------------------------------------------------------------------------
# 3. Broken-fixture regressions via kicad_pcb.sexpr (Phase 0 anchors)
#
# Each test asserts the specific structural bug is STILL PRESENT in the
# fixture, so accidental "fixes" are caught.  Tests pass when the bug exists.
# ---------------------------------------------------------------------------


class TestBrokenFixtureRegressions:
    """Phase 0 bug patterns must remain detectable via kicad_pcb.sexpr."""

    # ------------------------------------------------------------------
    # Bug 1 — sub-symbol names carry the library prefix inside lib_symbols
    # ------------------------------------------------------------------

    def test_bug1_sub_symbols_have_library_prefix(self) -> None:
        """Regression anchor: lib_symbols sub-entries carry 'Device:' prefix.

        In a correct file sub-symbols are named 'R_0_1'; the bug named them
        'Device:R_0_1'.  We verify this via AST traversal so the check is
        independent of indentation or whitespace.
        """
        from kicad_pcb.sexpr import parse
        from kicad_pcb.sexpr.nodes import ListNode, StringNode
        from kicad_pcb.sexpr.utils import find_first, walk

        text = _strip_comments((BROKEN / "bug1_subname_rename.kicad_sch").read_text())
        root = parse(text)
        lib_syms = find_first(root, "lib_symbols")
        assert lib_syms is not None, "lib_symbols section not found"

        # Collect all (symbol "Name" ...) nodes anywhere inside lib_symbols
        sub_names = [
            node.items[1].value  # type: ignore[union-attr]
            for node in walk(lib_syms)
            if (
                isinstance(node, ListNode)
                and node.key == "symbol"
                and len(node.items) >= 2
                and isinstance(node.items[1], StringNode)
            )
        ]
        buggy = [n for n in sub_names if ":" in n and not n.startswith("Device:R") or n in ("Device:R_0_1", "Device:R_1_1")]
        assert any("Device:R_0_1" == n or "Device:R_1_1" == n for n in sub_names), (
            f"Bug1 fixture no longer contains bad sub-symbol names; found: {sub_names}"
        )

    # ------------------------------------------------------------------
    # Bug 2 — properties carry old (id N) token
    # ------------------------------------------------------------------

    def test_bug2_properties_contain_old_id_token(self) -> None:
        """Regression anchor: at least one property node has a child (id N) token.

        Once the (id N) tokens are stripped by the fix, re-running this check
        would fail, alerting us that the fixture was silently corrected.
        """
        from kicad_pcb.sexpr import parse
        from kicad_pcb.sexpr.nodes import AtomNode, ListNode
        from kicad_pcb.sexpr.utils import walk

        text = _strip_comments((BROKEN / "bug2_id_property.kicad_sch").read_text())
        # Textual check: quick and readable
        assert re.search(r"\(id\s+\d+\)", text), (
            "Bug2 fixture no longer contains old (id N) property format"
        )
        # Structural check via AST: at least one (id N) node exists
        root = parse(text)
        id_nodes = [
            n
            for n in walk(root)
            if (
                isinstance(n, ListNode)
                and n.key == "id"
                and len(n.items) >= 2
                and isinstance(n.items[1], AtomNode)
                and n.items[1].value.isdigit()
            )
        ]
        assert id_nodes, "Bug2: no (id N) nodes found in AST — fixture may have been corrected"

    # ------------------------------------------------------------------
    # Bug 3 — lib_symbols closing paren at column 0 (wrong indentation)
    # ------------------------------------------------------------------

    def test_bug3_lib_symbols_closes_at_column_zero(self) -> None:
        """Regression anchor: the lib_symbols section closes with ')' at column 0.

        In a correct file every closing paren (except the root node) is
        indented by at least 2 spaces.  Bug 3 produced a ')' at column 0
        that closed lib_symbols prematurely.  We count bare ')' lines — a
        well-formed file has exactly one (the root close); the bug produces ≥ 2.
        """
        text = _strip_comments((BROKEN / "bug3_paren_indent.kicad_sch").read_text())
        bare_close_lines = [ln for ln in text.splitlines() if ln == ")"]
        assert len(bare_close_lines) >= 2, (
            f"Bug3 fixture should have ≥2 bare ')' lines (root + lib_symbols close), "
            f"got {len(bare_close_lines)}: {bare_close_lines}"
        )

    # ------------------------------------------------------------------
    # Bug 4 — placed symbol is missing (instances ...) block
    # ------------------------------------------------------------------

    def test_bug4_placed_symbol_missing_instances_block(self) -> None:
        """Regression anchor: at least one placed symbol lacks an (instances ...) child.

        Without (instances ...), kicad-cli exports an empty netlist.
        """
        from kicad_pcb.sexpr import parse
        from kicad_pcb.sexpr.nodes import ListNode
        from kicad_pcb.sexpr.utils import find_first

        text = _strip_comments((BROKEN / "bug4_no_instances.kicad_sch").read_text())
        root = parse(text)

        placed_syms = [
            item
            for item in root.items
            if (
                isinstance(item, ListNode)
                and item.key == "symbol"
                and find_first(item, "lib_id") is not None
            )
        ]
        assert placed_syms, "Bug4 fixture contains no placed symbols"

        missing = [sym for sym in placed_syms if find_first(sym, "instances") is None]
        assert missing, (
            "Bug4 fixture: all placed symbols appear to have (instances ...) blocks — "
            "fixture may have been silently corrected"
        )

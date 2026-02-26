"""P4.1 regression-fixture tests.

Tests that verify each fixture file behaves as expected when processed by the
kicad_pcb API — covering parsing, lint, validate, and error-message context.

Fixture layout:
  tests/fixtures/valid/    — known-good minimal .kicad_sch / .kicad_pcb files
  tests/fixtures/broken/   — four known-bad schematics (semantic bugs, not
                             syntax errors; all parse cleanly as S-expressions)
  tests/fixtures/golden/   — round-trip canonical files (also valid)
  tests/fixtures/working/  — real-world working schematic

For each fixture category the tests assert:
  * parsing result (pass ↔ valid S-expression / fail ↔ syntactically broken)
  * validator expectation  (syntax_ok, lint error count)
  * error message context  (ParseError includes file path on hard failures)
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import kicad_pcb
import pytest
from kicad_pcb.commands.lint import cmd_lint_sch, cmd_validate_pcb, cmd_validate_sch
from kicad_pcb.errors import ParseError
from kicad_pcb.sexpr.nodes import AtomNode, ListNode
from kicad_pcb.sexpr.parser import parse_file

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent.parent / "fixtures"
VALID = FIXTURES / "valid"
BROKEN = FIXTURES / "broken"
GOLDEN = FIXTURES / "golden"
WORKING = FIXTURES / "working"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _args(path: Path) -> SimpleNamespace:
    """Build a minimal argparse-like namespace for cmd_* functions."""
    return SimpleNamespace(path=str(path))


def _strip_comments(text: str) -> str:
    """Remove leading-``#`` comment lines (used in broken fixture files)."""
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )


# ---------------------------------------------------------------------------
# Valid fixtures — tests/fixtures/valid/
# ---------------------------------------------------------------------------


class TestValidFixtureDirectory:
    """Verify the valid/ directory exists and contains expected files."""

    def test_valid_dir_exists(self) -> None:
        assert VALID.is_dir(), "tests/fixtures/valid/ directory must exist"

    def test_minimal_sch_exists(self) -> None:
        assert (VALID / "minimal.kicad_sch").is_file()

    def test_minimal_pcb_exists(self) -> None:
        assert (VALID / "minimal.kicad_pcb").is_file()


class TestValidSchematicParseLint:
    """Valid .kicad_sch files parse cleanly and pass lint."""

    @pytest.mark.parametrize(
        "fixture",
        [
            VALID / "minimal.kicad_sch",
            GOLDEN / "minimal.kicad_sch",
            GOLDEN / "sch_with_resistor.kicad_sch",
            WORKING / "SmokeTest_R1.kicad_sch",
        ],
        ids=lambda p: p.name,
    )
    def test_parses_as_kicad_sch(self, fixture: Path) -> None:
        """Parser must return a root ListNode with first atom 'kicad_sch'."""
        root = parse_file(fixture)
        assert isinstance(root, ListNode), f"{fixture.name}: expected ListNode root"
        first = root.items[0] if root.items else None
        assert isinstance(first, AtomNode) and first.value == "kicad_sch", (
            f"{fixture.name}: root key is {first!r}, expected 'kicad_sch'"
        )

    @pytest.mark.parametrize(
        "fixture",
        [
            VALID / "minimal.kicad_sch",
            GOLDEN / "minimal.kicad_sch",
            WORKING / "SmokeTest_R1.kicad_sch",
        ],
        ids=lambda p: p.name,
    )
    def test_cmd_lint_sch_ok(self, fixture: Path) -> None:
        """cmd_lint_sch must return ok=True for valid files."""
        result = cmd_lint_sch(_args(fixture))
        assert result.ok, (
            f"{fixture.name}: expected lint ok=True, got {result.error_count} errors: "
            + ", ".join(f"[{i.code}] {i.message}" for i in result.issues)
        )

    @pytest.mark.parametrize(
        "fixture",
        [
            VALID / "minimal.kicad_sch",
            GOLDEN / "minimal.kicad_sch",
            WORKING / "SmokeTest_R1.kicad_sch",
        ],
        ids=lambda p: p.name,
    )
    def test_cmd_validate_sch_syntax_ok(self, fixture: Path) -> None:
        """cmd_validate_sch must report syntax_ok=True for valid files."""
        result = cmd_validate_sch(_args(fixture))
        assert result.syntax_ok, f"{fixture.name}: syntax_ok must be True"


class TestValidPcbParseLint:
    """Valid .kicad_pcb files parse cleanly and pass lint."""

    @pytest.mark.parametrize(
        "fixture",
        [
            VALID / "minimal.kicad_pcb",
            GOLDEN / "minimal.kicad_pcb",
            GOLDEN / "pcb_with_footprint.kicad_pcb",
        ],
        ids=lambda p: p.name,
    )
    def test_parses_as_kicad_pcb(self, fixture: Path) -> None:
        root = parse_file(fixture)
        assert isinstance(root, ListNode)
        first = root.items[0] if root.items else None
        assert isinstance(first, AtomNode) and first.value == "kicad_pcb", (
            f"{fixture.name}: root key is {first!r}, expected 'kicad_pcb'"
        )

    @pytest.mark.parametrize(
        "fixture",
        [VALID / "minimal.kicad_pcb", GOLDEN / "minimal.kicad_pcb"],
        ids=lambda p: p.name,
    )
    def test_cmd_validate_pcb_syntax_ok(self, fixture: Path) -> None:
        result = cmd_validate_pcb(_args(fixture))
        assert result.syntax_ok, f"{fixture.name}: syntax_ok must be True"


# ---------------------------------------------------------------------------
# Broken fixtures — tests/fixtures/broken/
# All four files are syntactically valid S-expressions; their bugs are
# semantic.  All should parse cleanly but exhibit the known bad pattern.
# ---------------------------------------------------------------------------


BROKEN_SCH_FIXTURES = [
    BROKEN / "bug1_subname_rename.kicad_sch",
    BROKEN / "bug2_id_property.kicad_sch",
    BROKEN / "bug3_paren_indent.kicad_sch",
    BROKEN / "bug4_no_instances.kicad_sch",
]


class TestBrokenFixturesParsing:
    """All broken fixtures must be parseable as valid S-expressions.

    The bugs they contain are semantic (wrong field values / missing blocks),
    NOT syntax errors.  Parsing must succeed so that mutation tools *can*
    load and fix these files.
    """

    @pytest.mark.parametrize("fixture", BROKEN_SCH_FIXTURES, ids=lambda p: p.name)
    def test_parses_without_exception(self, fixture: Path, tmp_path: Path) -> None:
        """parse_file must succeed on comment-stripped broken fixture."""
        stripped = _strip_comments(fixture.read_text(encoding="utf-8"))
        tmp = tmp_path / fixture.name
        tmp.write_text(stripped, encoding="utf-8")
        root = parse_file(tmp)  # must not raise
        assert isinstance(root, ListNode), f"{fixture.name}: expected ListNode"

    @pytest.mark.parametrize("fixture", BROKEN_SCH_FIXTURES, ids=lambda p: p.name)
    def test_root_is_kicad_sch(self, fixture: Path, tmp_path: Path) -> None:
        """Root node key must be 'kicad_sch' even in broken files."""
        stripped = _strip_comments(fixture.read_text(encoding="utf-8"))
        tmp = tmp_path / fixture.name
        tmp.write_text(stripped, encoding="utf-8")
        root = parse_file(tmp)
        first = root.items[0] if isinstance(root, ListNode) and root.items else None
        assert isinstance(first, AtomNode) and first.value == "kicad_sch", (
            f"{fixture.name}: root key should be kicad_sch, got {first!r}"
        )

    @pytest.mark.parametrize("fixture", BROKEN_SCH_FIXTURES, ids=lambda p: p.name)
    def test_cmd_lint_sch_does_not_crash(self, fixture: Path, tmp_path: Path) -> None:
        """cmd_lint_sch must not raise; bugs are beyond syntax-level lint."""
        stripped = _strip_comments(fixture.read_text(encoding="utf-8"))
        tmp = tmp_path / fixture.name
        tmp.write_text(stripped, encoding="utf-8")
        result = cmd_lint_sch(_args(tmp))
        # Result is a LintFileResult — just assert it's a real object
        assert result is not None
        assert hasattr(result, "ok")

    @pytest.mark.parametrize("fixture", BROKEN_SCH_FIXTURES, ids=lambda p: p.name)
    def test_cmd_validate_sch_syntax_ok(self, fixture: Path, tmp_path: Path) -> None:
        """cmd_validate_sch must report syntax_ok=True (bugs are semantic)."""
        stripped = _strip_comments(fixture.read_text(encoding="utf-8"))
        tmp = tmp_path / fixture.name
        tmp.write_text(stripped, encoding="utf-8")
        result = cmd_validate_sch(_args(tmp))
        assert result.syntax_ok, (
            f"{fixture.name}: syntax_ok must be True — bugs are semantic, not syntactic"
        )


class TestBrokenFixtureBugPatterns:
    """Each broken fixture still exhibits its specific known-bad pattern.

    These are regression anchors: if a fixture is silently 'fixed' the
    anchor test will fail, alerting us to the change.
    """

    def test_bug1_sub_symbol_has_library_prefix(self) -> None:
        text = _strip_comments(
            (BROKEN / "bug1_subname_rename.kicad_sch").read_text(encoding="utf-8")
        )
        assert '"Device:R_0_1"' in text or '"Device:R_1_1"' in text, (
            "Bug1 fixture no longer contains the bad sub-symbol name pattern"
        )

    def test_bug2_contains_old_id_property(self) -> None:
        text = _strip_comments(
            (BROKEN / "bug2_id_property.kicad_sch").read_text(encoding="utf-8")
        )
        assert re.search(r"\(id\s+\d+\)", text), (
            "Bug2 fixture no longer contains old (id N) property format"
        )

    def test_bug3_has_bare_close_paren_at_column_zero(self) -> None:
        text = _strip_comments(
            (BROKEN / "bug3_paren_indent.kicad_sch").read_text(encoding="utf-8")
        )
        bare = [ln for ln in text.splitlines() if ln == ")"]
        assert len(bare) >= 2, (
            f"Bug3: expected ≥2 bare ')' lines (root + lib_symbols), got {len(bare)}"
        )

    def test_bug4_placed_symbol_missing_instances_block(self) -> None:
        text = _strip_comments(
            (BROKEN / "bug4_no_instances.kicad_sch").read_text(encoding="utf-8")
        )
        assert "(instances" not in text, (
            "Bug4 fixture unexpectedly contains an (instances ...) block"
        )


# ---------------------------------------------------------------------------
# Error-message context — truly malformed S-expression
# ---------------------------------------------------------------------------


class TestErrorMessageContext:
    """When a file is syntactically broken, errors must mention the file path."""

    def test_cmd_lint_sch_parse_error_includes_file_path(
        self, tmp_path: Path
    ) -> None:
        """ParseError raised by cmd_lint_sch must include the target file path."""
        bad = tmp_path / "malformed.kicad_sch"
        bad.write_text("(kicad_sch (unclosed\n", encoding="utf-8")
        with pytest.raises(ParseError) as exc_info:
            cmd_lint_sch(_args(bad))
        assert str(bad) in str(exc_info.value) or bad.name in str(exc_info.value), (
            f"ParseError message should mention the file path; got: {exc_info.value}"
        )

    def test_cmd_validate_sch_wrong_root_gives_syntax_error(
        self, tmp_path: Path
    ) -> None:
        """A .kicad_sch file with wrong root node reports syntax_ok=False."""
        wrong = tmp_path / "wrong_root.kicad_sch"
        wrong.write_text("(kicad_pcb (version 1))\n", encoding="utf-8")
        result = cmd_validate_sch(_args(wrong))
        assert not result.syntax_ok, "Wrong root node must cause syntax_ok=False"

    def test_parse_error_on_truncated_file(self, tmp_path: Path) -> None:
        """A truncated (mid-write) file raises ParseError or returns syntax_ok=False."""
        truncated = tmp_path / "truncated.kicad_sch"
        truncated.write_text("(kicad_sch (version 20230121)", encoding="utf-8")
        # Either raises ParseError or returns syntax_ok=False — both are acceptable
        try:
            result = cmd_validate_sch(_args(truncated))
            assert not result.syntax_ok, "Truncated file should not pass syntax check"
        except ParseError:
            pass  # also acceptable

    def test_check_sexp_error_message_names_root(self) -> None:
        """_check_sexp ParseError must mention the expected root key."""
        with pytest.raises(ParseError, match="kicad_sch"):
            kicad_pcb._check_sexp("(kicad_pcb (version 1))\n", "kicad_sch")

    def test_check_sexp_error_message_on_unbalanced(self) -> None:
        """_check_sexp ParseError for unbalanced parens must mention 'Unbalanced'."""
        with pytest.raises(ParseError, match="[Uu]nbalanced"):
            kicad_pcb._check_sexp("(kicad_sch (missing-close\n", "kicad_sch")

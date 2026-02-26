"""P3.2 tests — richer exception hierarchy.

Verifies the structured exception types added by P3.2:

Hierarchy:
    KiCadError
    ├── UserError
    ├── ToolError
    │   └── KicadCliValidationError
    └── ParseError
        ├── SExprTokenizeError  — lexer: line/col + hint
        ├── SExprParseError     — parser: line/col + hint
        └── DocSyntaxError      — file-level: path + hint

Contract:
    1. Every new type is a subclass of its documented parent.
    2. ``except ParseError`` still catches tokenize/parse/doc errors.
    3. ``except KiCadError`` catches everything.
    4. Structured attributes (``line``, ``col``, ``path``, ``issue_count``) are
       set correctly and are accessible without inspecting the string message.
    5. ``hint`` is always a non-empty string providing actionable guidance.
    6. The tokenizer raises ``SExprTokenizeError`` for unterminated strings,
       with the correct 1-based line/col and ``__str__`` in ``line:col: msg`` form.
    7. The parser raises ``SExprParseError`` for structural errors, carrying
       line/col when the position is known.
    8. ``parse_file`` raises ``DocSyntaxError`` (with ``path`` set) on I/O failure.
    9. ``_check_sexp`` raises ``DocSyntaxError`` for balance/root issues.
"""

from __future__ import annotations

from pathlib import Path

import kicad_pcb
import pytest
from kicad_pcb.errors import (
    DocLintError,
    DocSyntaxError,
    KicadCliValidationError,
    KiCadError,
    ParseError,
    SExprParseError,
    SExprTokenizeError,
    ToolError,
)
from kicad_pcb.fs import _check_sexp
from kicad_pcb.sexpr.parser import parse, parse_file
from kicad_pcb.sexpr.tokenizer import tokenize

pytestmark = pytest.mark.unit


# ===========================================================================
# 1. Hierarchy — isinstance / issubclass
# ===========================================================================


class TestExceptionHierarchy:
    """All new types honour their documented inheritance."""

    def test_sexpr_tokenize_error_is_parse_error(self) -> None:
        assert issubclass(SExprTokenizeError, ParseError)

    def test_sexpr_parse_error_is_parse_error(self) -> None:
        assert issubclass(SExprParseError, ParseError)

    def test_doc_syntax_error_is_parse_error(self) -> None:
        assert issubclass(DocSyntaxError, ParseError)

    def test_doc_lint_error_is_kicad_error(self) -> None:
        assert issubclass(DocLintError, KiCadError)

    def test_doc_lint_error_is_not_parse_error(self) -> None:
        assert not issubclass(DocLintError, ParseError)

    def test_kicad_cli_validation_error_is_tool_error(self) -> None:
        assert issubclass(KicadCliValidationError, ToolError)

    def test_kicad_cli_validation_error_is_kicad_error(self) -> None:
        assert issubclass(KicadCliValidationError, KiCadError)

    def test_all_parse_errors_also_kicad_errors(self) -> None:
        for cls in (SExprTokenizeError, SExprParseError, DocSyntaxError):
            assert issubclass(cls, KiCadError), f"{cls.__name__} should be KiCadError"

    def test_top_level_package_exports_new_types(self) -> None:
        """kicad_pcb package exposes the new error types."""
        for name in (
            "SExprTokenizeError",
            "SExprParseError",
            "DocSyntaxError",
            "DocLintError",
            "KicadCliValidationError",
        ):
            assert hasattr(kicad_pcb, name), f"kicad_pcb.{name} missing from package"


# ===========================================================================
# 2. SExprTokenizeError — attributes and catchability
# ===========================================================================


class TestSExprTokenizeError:
    """SExprTokenizeError carries line, col, hint and is a ParseError."""

    def _make(self, msg: str = "test error", line: int = 3, col: int = 7) -> SExprTokenizeError:
        return SExprTokenizeError(msg, line=line, col=col)

    def test_line_attribute(self) -> None:
        exc = self._make(line=5, col=1)
        assert exc.line == 5

    def test_col_attribute(self) -> None:
        exc = self._make(line=5, col=12)
        assert exc.col == 12

    def test_hint_is_nonempty_string(self) -> None:
        exc = self._make()
        assert isinstance(exc.hint, str) and exc.hint

    def test_str_shows_line_col_prefix(self) -> None:
        exc = SExprTokenizeError("unterminated string", line=4, col=8)
        s = str(exc)
        assert "4:8" in s, f"Expected '4:8' in str(exc), got {s!r}"

    def test_caught_by_except_parse_error(self) -> None:
        with pytest.raises(ParseError):
            raise SExprTokenizeError("oops", line=1, col=1)

    def test_caught_by_except_kicad_error(self) -> None:
        with pytest.raises(KiCadError):
            raise SExprTokenizeError("oops", line=1, col=1)

    def test_isinstance_parse_error(self) -> None:
        exc = self._make()
        assert isinstance(exc, ParseError)


# ===========================================================================
# 3. SExprParseError — attributes and catchability
# ===========================================================================


class TestSExprParseError:
    """SExprParseError carries optional line/col, hint, and is a ParseError."""

    def test_line_and_col_set(self) -> None:
        exc = SExprParseError("bad input", line=2, col=5)
        assert exc.line == 2
        assert exc.col == 5

    def test_line_col_defaults_to_none(self) -> None:
        exc = SExprParseError("no position")
        assert exc.line is None
        assert exc.col is None

    def test_hint_is_nonempty_string(self) -> None:
        exc = SExprParseError("x")
        assert isinstance(exc.hint, str) and exc.hint

    def test_caught_by_except_parse_error(self) -> None:
        with pytest.raises(ParseError):
            raise SExprParseError("problem", line=1, col=1)

    def test_isinstance_parse_error(self) -> None:
        exc = SExprParseError("problem", line=1, col=1)
        assert isinstance(exc, ParseError)


# ===========================================================================
# 4. DocSyntaxError — attributes and catchability
# ===========================================================================


class TestDocSyntaxError:
    """DocSyntaxError carries optional path, hint, and is a ParseError."""

    def test_path_attribute_set(self, tmp_path: Path) -> None:
        p = tmp_path / "myfile.kicad_sch"
        exc = DocSyntaxError("bad doc", path=p)
        assert exc.path == p

    def test_path_defaults_to_none(self) -> None:
        exc = DocSyntaxError("bad doc")
        assert exc.path is None

    def test_hint_is_nonempty_string(self) -> None:
        exc = DocSyntaxError("x")
        assert isinstance(exc.hint, str) and exc.hint

    def test_caught_by_except_parse_error(self) -> None:
        with pytest.raises(ParseError):
            raise DocSyntaxError("bad file", path=None)

    def test_isinstance_parse_error(self) -> None:
        exc = DocSyntaxError("bad file")
        assert isinstance(exc, ParseError)


# ===========================================================================
# 5. DocLintError — attributes
# ===========================================================================


class TestDocLintError:
    """DocLintError carries path, issue_count, and a dynamic hint."""

    def test_path_and_issue_count(self, tmp_path: Path) -> None:
        p = tmp_path / "x.kicad_sch"
        exc = DocLintError("3 issues", path=p, issue_count=3)
        assert exc.path == p
        assert exc.issue_count == 3

    def test_path_defaults_none(self) -> None:
        exc = DocLintError("msg")
        assert exc.path is None

    def test_issue_count_defaults_zero(self) -> None:
        exc = DocLintError("msg")
        assert exc.issue_count == 0

    def test_hint_mentions_count(self) -> None:
        exc = DocLintError("msg", issue_count=4)
        assert "4" in exc.hint

    def test_hint_singular_for_one_issue(self) -> None:
        exc = DocLintError("msg", issue_count=1)
        assert "1 lint issue" in exc.hint

    def test_is_kicad_error_not_parse_error(self) -> None:
        exc = DocLintError("msg", issue_count=0)
        assert isinstance(exc, KiCadError)
        assert not isinstance(exc, ParseError)


# ===========================================================================
# 6. KicadCliValidationError — attributes
# ===========================================================================


class TestKicadCliValidationError:
    """KicadCliValidationError carries path, issue_count, hint, is ToolError."""

    def test_path_and_issue_count(self, tmp_path: Path) -> None:
        p = tmp_path / "proj.kicad_sch"
        exc = KicadCliValidationError("ERC failed", path=p, issue_count=2)
        assert exc.path == p
        assert exc.issue_count == 2

    def test_defaults(self) -> None:
        exc = KicadCliValidationError("DRC failed")
        assert exc.path is None
        assert exc.issue_count == 0

    def test_hint_is_nonempty(self) -> None:
        exc = KicadCliValidationError("x")
        assert isinstance(exc.hint, str) and exc.hint

    def test_caught_by_except_tool_error(self) -> None:
        with pytest.raises(ToolError):
            raise KicadCliValidationError("fail")

    def test_caught_by_except_kicad_error(self) -> None:
        with pytest.raises(KiCadError):
            raise KicadCliValidationError("fail")


# ===========================================================================
# 7. Tokenizer raises SExprTokenizeError with correct position
# ===========================================================================


class TestTokenizerRaisesSExprTokenizeError:
    """tokenize() raises SExprTokenizeError for unterminated string literals."""

    def test_unterminated_string_on_line_1(self) -> None:
        with pytest.raises(SExprTokenizeError) as exc_info:
            tokenize('(foo "unterminated')
        exc = exc_info.value
        assert exc.line == 1
        assert exc.col >= 5  # somewhere after the opening quote

    def test_unterminated_string_on_line_3(self) -> None:
        src = '(foo\n  (bar\n  "bad string))'
        with pytest.raises(SExprTokenizeError) as exc_info:
            tokenize(src)
        exc = exc_info.value
        assert exc.line == 3

    def test_backslash_at_end_of_string(self) -> None:
        with pytest.raises(SExprTokenizeError) as exc_info:
            tokenize(r'(foo "bad\\')
        exc = exc_info.value
        assert exc.line >= 1
        assert exc.col >= 1

    def test_error_is_parse_error(self) -> None:
        with pytest.raises(ParseError):
            tokenize('(oops "never closes')

    def test_str_contains_line_col(self) -> None:
        with pytest.raises(SExprTokenizeError) as exc_info:
            tokenize('"unclosed')
        s = str(exc_info.value)
        # Should contain "line:col:" prefix
        assert ":" in s


# ===========================================================================
# 8. Parser raises SExprParseError with position info
# ===========================================================================


class TestParserRaisesSExprParseError:
    """parse() raises SExprParseError carrying line/col for bad input."""

    def test_unmatched_open_paren(self) -> None:
        with pytest.raises(SExprParseError) as exc_info:
            parse("(foo (bar")
        exc = exc_info.value
        assert exc.line is not None
        assert exc.col is not None

    def test_unexpected_close_paren(self) -> None:
        with pytest.raises(SExprParseError) as exc_info:
            parse(")")
        exc = exc_info.value
        assert exc.line == 1
        assert exc.col == 1

    def test_empty_string_no_position(self) -> None:
        with pytest.raises(SExprParseError) as exc_info:
            parse("")
        exc = exc_info.value
        # No meaningful position for empty input
        assert exc.line is None
        assert exc.col is None

    def test_comments_only_no_position(self) -> None:
        with pytest.raises(SExprParseError) as exc_info:
            parse("; just a comment")
        exc = exc_info.value
        assert exc.line is None

    def test_trailing_content_has_position(self) -> None:
        with pytest.raises(SExprParseError) as exc_info:
            parse("(kicad_sch) (extra)")
        exc = exc_info.value
        assert exc.line is not None
        assert exc.col is not None

    def test_wrong_root_token(self) -> None:
        with pytest.raises(SExprParseError) as exc_info:
            parse("not-a-paren")
        exc = exc_info.value
        assert exc.line == 1
        assert exc.col == 1

    def test_error_message_contains_position(self) -> None:
        """Error message should contain line:col for positional errors."""
        with pytest.raises(SExprParseError) as exc_info:
            parse(")")
        msg = str(exc_info.value)
        assert "1:1" in msg

    def test_parse_error_caught_by_parent(self) -> None:
        with pytest.raises(ParseError):
            parse("(unclosed")


# ===========================================================================
# 9. parse_file raises DocSyntaxError with path on I/O failure
# ===========================================================================


class TestParseFileRaisesDocSyntaxError:
    """parse_file() raises DocSyntaxError (with path) when file is unreadable."""

    def test_missing_file_raises_doc_syntax_error(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.kicad_sch"
        with pytest.raises(DocSyntaxError) as exc_info:
            parse_file(missing)
        exc = exc_info.value
        assert exc.path == missing

    def test_path_attribute_matches_requested_file(self, tmp_path: Path) -> None:
        path = tmp_path / "absent.kicad_pcb"
        with pytest.raises(DocSyntaxError) as exc_info:
            parse_file(path)
        assert exc_info.value.path == path

    def test_error_still_caught_as_parse_error(self, tmp_path: Path) -> None:
        missing = tmp_path / "none.kicad_sch"
        with pytest.raises(ParseError):
            parse_file(missing)

    def test_hint_is_nonempty_string(self, tmp_path: Path) -> None:
        missing = tmp_path / "none.kicad_sch"
        with pytest.raises(DocSyntaxError) as exc_info:
            parse_file(missing)
        assert exc_info.value.hint


# ===========================================================================
# 10. _check_sexp raises DocSyntaxError
# ===========================================================================


class TestCheckSexpRaisesDocSyntaxError:
    """_check_sexp() raises DocSyntaxError for balance/root issues."""

    def test_unbalanced_parens_raises_doc_syntax_error(self) -> None:
        with pytest.raises(DocSyntaxError):
            _check_sexp("(kicad_sch (version 20230121)", "kicad_sch")

    def test_wrong_root_raises_doc_syntax_error(self) -> None:
        with pytest.raises(DocSyntaxError):
            _check_sexp("(kicad_pcb)", "kicad_sch")

    def test_doc_syntax_error_message_mentions_expected_root(self) -> None:
        with pytest.raises(DocSyntaxError) as exc_info:
            _check_sexp("(wrong_root)", "kicad_sch")
        assert "kicad_sch" in str(exc_info.value)

    def test_doc_syntax_error_caught_by_parse_error(self) -> None:
        with pytest.raises(ParseError):
            _check_sexp("(kicad_pcb (unclosed)", "kicad_pcb")

    def test_valid_content_does_not_raise(self) -> None:
        _check_sexp("(kicad_sch (version 20230121))", "kicad_sch")  # Should not raise

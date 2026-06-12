from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb.errors import (
    DocSyntaxError,
    ParseError,
    SExprParseError,
    SExprTokenizeError,
)
from kicad_pcb.fs import _check_sexp
from kicad_pcb.sexpr.parser import parse, parse_file
from kicad_pcb.sexpr.tokenizer import tokenize

pytestmark = pytest.mark.unit


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

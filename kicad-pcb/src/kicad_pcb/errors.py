"""Typed exception hierarchy for the kicad-pcb skill.

Hierarchy
---------
::

    KiCadError
    ├── UserError                    — bad user input / missing project
    ├── ToolError                    — external tool failed / unavailable
    │   └── KicadCliValidationError  — kicad-cli ERC/DRC reported failures
    └── ParseError                   — S-expression file is malformed
        ├── SExprTokenizeError       — tokenizer: unterminated string / bad char
        ├── SExprParseError          — parser: structural error (unmatched parens …)
        └── DocSyntaxError           — document-level syntax failure (wrong root …)

Deprecated aliases
------------------
None — all names in this module are stable public API.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path


class ErrorCode(StrEnum):
    """Stable machine-readable error codes for JSON output."""

    IR_SCHEMA_INVALID = "IR_SCHEMA_INVALID"
    IR_SEMANTIC_INVALID = "IR_SEMANTIC_INVALID"
    SYMBOL_NOT_FOUND = "SYMBOL_NOT_FOUND"
    PIN_INVALID = "PIN_INVALID"
    MULTI_UNIT_UNSUPPORTED = "MULTI_UNIT_UNSUPPORTED"
    KICAD_CLI_MISSING = "KICAD_CLI_MISSING"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    NOT_OWNED = "NOT_OWNED"
    PROJECT_NOT_OPEN = "PROJECT_NOT_OPEN"
    IO_ERROR = "IO_ERROR"
    USER_ERROR = "USER_ERROR"
    TOOL_ERROR = "TOOL_ERROR"
    PARSE_ERROR = "PARSE_ERROR"
    EMPTY_GENERATION = "EMPTY_GENERATION"
    SYMBOL_DIR_MISSING = "SYMBOL_DIR_MISSING"


class KiCadError(RuntimeError):
    """Base class for all kicad-pcb errors."""

    code: str = ErrorCode.IO_ERROR

    def __init__(
        self,
        message: str,
        *,
        code: str | ErrorCode | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code or self.code)
        self.details: dict[str, object] = details or {}

    def as_dict(self) -> dict[str, object]:
        """Return stable JSON-serialisable error payload."""
        return {
            "code": self.code,
            "message": str(self),
            "details": self.details,
        }

    def to_json(self) -> str:
        """Return a JSON string for ``--json`` failure output."""
        return json.dumps(self.as_dict(), indent=2)


class UserError(KiCadError):
    """Invalid user input or missing project."""

    code: str = ErrorCode.USER_ERROR


class ToolError(KiCadError):
    """External tool (kicad-cli, Java, …) failed or is unavailable."""

    code: str = ErrorCode.TOOL_ERROR


class ParseError(KiCadError):
    """KiCad S-expression file is malformed."""

    code: str = ErrorCode.PARSE_ERROR


# ---------------------------------------------------------------------------
# S-expression layer
# ---------------------------------------------------------------------------


class SExprTokenizeError(ParseError):
    """Raised by the tokenizer for lexical errors (unterminated string, …).

    Attributes
    ----------
    line:
        1-based source line where the error begins.
    col:
        1-based source column where the error begins.
    hint:
        Short suggestion for fixing the problem.
    """

    hint: str = "Check that all string literals are closed with a double-quote."

    def __init__(self, message: str, *, line: int, col: int) -> None:
        super().__init__(message)
        self.line: int = line
        self.col: int = col

    def __str__(self) -> str:
        return f"{self.line}:{self.col}: {self.args[0]}"


class SExprParseError(ParseError):
    """Raised by the parser for structural S-expression errors.

    Attributes
    ----------
    line:
        1-based source line of the problem token, or ``None`` if unavailable.
    col:
        1-based source column of the problem token, or ``None`` if unavailable.
    hint:
        Short suggestion for fixing the problem.
    """

    hint: str = "Validate the file with: openclaw lint --path <file>"

    def __init__(
        self,
        message: str,
        *,
        line: int | None = None,
        col: int | None = None,
    ) -> None:
        super().__init__(message)
        self.line: int | None = line
        self.col: int | None = col


# ---------------------------------------------------------------------------
# Document layer
# ---------------------------------------------------------------------------


class DocSyntaxError(ParseError):
    """Raised when a KiCad document file fails syntactic validation.

    This exception is used when the parser can identify *which file* is
    malformed (as opposed to :class:`SExprParseError`, which is raised during
    pure in-memory parsing without file context).

    Attributes
    ----------
    path:
        :class:`~pathlib.Path` of the file that failed to parse, or ``None``
        when the path is not available.
    hint:
        Short suggestion for fixing the problem.
    """

    hint: str = "Run 'openclaw lint --path <file>' to see detailed diagnostics."

    def __init__(self, message: str, *, path: Path | None = None) -> None:
        super().__init__(message)
        self.path: Path | None = path


class DocLintError(KiCadError):
    """Raised when error-level structural lint issues are found in a document.

    Attributes
    ----------
    path:
        :class:`~pathlib.Path` of the file that failed linting, or ``None``
        when the path is not available.
    issue_count:
        Total number of error-level lint issues found.
    hint:
        Short suggestion for fixing the problem.
    """

    def __init__(
        self,
        message: str,
        *,
        path: Path | None = None,
        issue_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.path: Path | None = path
        self.issue_count: int = issue_count

    @property
    def hint(self) -> str:
        n = self.issue_count
        noun = "issue" if n == 1 else "issues"
        return f"Fix {n} lint {noun} reported above, then retry."


class KicadCliValidationError(ToolError):
    """Raised when kicad-cli ERC / DRC reports validation failures.

    Attributes
    ----------
    path:
        :class:`~pathlib.Path` of the validated file, or ``None`` when not
        available.
    issue_count:
        Number of ERC / DRC violations reported by kicad-cli.
    hint:
        Short suggestion for fixing the problem.
    """

    hint: str = "Run 'kicad-cli sch erc' or 'kicad-cli pcb drc' for detailed errors."

    def __init__(
        self,
        message: str,
        *,
        path: Path | None = None,
        issue_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.path: Path | None = path
        self.issue_count: int = issue_count

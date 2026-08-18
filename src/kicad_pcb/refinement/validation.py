"""Structural and KiCad ERC validation for refinement candidates."""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.errors import ToolError, UserError
from kicad_pcb.lint import LintSeverity, lint_schematic
from kicad_pcb.sch_doc import SchematicDoc

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CandidateStructuralValidationReport:
    schema_version: str
    status: str
    lint_error_codes: tuple[str, ...]
    erc_violation_count: int

    @property
    def passed(self) -> bool:
        return self.status == "passed"


@dataclass(frozen=True)
class _ErcViolationSummary:
    total_count: int
    blocking_count: int


def validate_candidate_structure(
    candidate: Path,
    *,
    adapter: KicadCliAdapter,
    work_dir: Path | None = None,
) -> CandidateStructuralValidationReport:
    doc = SchematicDoc.load(candidate)
    lint_errors = tuple(
        sorted(
            issue.code for issue in lint_schematic(doc.root) if issue.severity is LintSeverity.ERROR
        )
    )
    if lint_errors:
        return CandidateStructuralValidationReport("1.0", "failed", lint_errors, 0)
    directory = work_dir or candidate.parent
    fd, raw = tempfile.mkstemp(prefix="refinement-erc-", suffix=".json", dir=directory)
    Path(raw).unlink(missing_ok=True)
    try:
        result, report = adapter.erc(candidate, Path(raw))
        if not result.ok:
            raise ToolError(f"KiCad ERC failed with exit code {result.returncode}")
        summary = _erc_violation_summary(report)
        return CandidateStructuralValidationReport(
            "1.0",
            "passed" if summary.blocking_count == 0 else "failed",
            (),
            summary.total_count,
        )
    finally:
        try:
            Path(raw).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            LOGGER.warning(
                "failed to remove temporary refinement ERC report",
                extra={"error_type": type(exc).__name__},
            )


def _erc_violation_count(report: dict[str, object] | None) -> int:
    return _erc_violation_summary(report).total_count


def _erc_violation_summary(report: dict[str, object] | None) -> _ErcViolationSummary:
    violations = _erc_violations(report)
    return _ErcViolationSummary(
        total_count=len(violations),
        blocking_count=sum(_erc_violation_is_blocking(item) for item in violations),
    )


def _erc_violations(report: dict[str, object] | None) -> list[dict[str, object]]:
    if not isinstance(report, dict):
        raise _invalid_erc_report()

    if "violations" in report:
        direct_violations = report["violations"]
        if not _valid_violation_list(direct_violations):
            raise _invalid_erc_report()
        return direct_violations

    sheets = report.get("sheets")
    if not isinstance(sheets, list):
        raise _invalid_erc_report()

    all_violations: list[dict[str, object]] = []
    for sheet in sheets:
        if not isinstance(sheet, dict):
            raise _invalid_erc_report()
        sheet_violations = sheet.get("violations")
        if not _valid_violation_list(sheet_violations):
            raise _invalid_erc_report()
        all_violations.extend(sheet_violations)
    return all_violations


def _erc_violation_is_blocking(violation: dict[str, object]) -> bool:
    severity = violation.get("severity")
    if severity is None:
        # Legacy reports did not consistently include severity. Preserve the old
        # fail-closed behavior for an unclassified violation.
        return True
    if not isinstance(severity, str):
        raise _invalid_erc_report()
    return severity.strip().lower() != "warning"


def _valid_violation_list(value: object) -> TypeGuard[list[dict[str, object]]]:
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


def _invalid_erc_report() -> UserError:
    return UserError(
        "KiCad ERC did not produce a usable violation report.",
        code="REFINEMENT_ERC_INVALID_REPORT",
    )

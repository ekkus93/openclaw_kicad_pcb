"""Structural and KiCad ERC validation for refinement candidates."""

from __future__ import annotations

import logging
import os
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.errors import ToolError, UserError
from kicad_pcb.lint import LintSeverity, lint_schematic
from kicad_pcb.refinement.transaction import accepted_path_for_refinement_candidate
from kicad_pcb.sch_doc import SchematicDoc

LOGGER = logging.getLogger(__name__)
_LEGACY_ERC_TYPE = "<legacy-unclassified>"


@dataclass(frozen=True)
class CandidateStructuralValidationReport:
    schema_version: str
    status: str
    lint_error_codes: tuple[str, ...]
    erc_violation_count: int
    erc_blocking_violation_count: int = 0
    erc_blocking_violation_types: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status == "passed"


@dataclass(frozen=True)
class _ErcViolationSummary:
    total_count: int
    blocking_count: int
    blocking_types: tuple[str, ...]


def validate_candidate_structure(
    candidate: Path,
    *,
    adapter: KicadCliAdapter,
    work_dir: Path | None = None,
    baseline: CandidateStructuralValidationReport | None = None,
) -> CandidateStructuralValidationReport:
    """Validate structure, allowing no regression from a transaction's accepted baseline."""

    if baseline is None:
        accepted_path = accepted_path_for_refinement_candidate(candidate)
        if accepted_path is not None:
            baseline = validate_candidate_structure(
                accepted_path,
                adapter=adapter,
                work_dir=work_dir,
            )

    doc = SchematicDoc.load(candidate)
    lint_errors = tuple(
        sorted(
            issue.code for issue in lint_schematic(doc.root) if issue.severity is LintSeverity.ERROR
        )
    )
    directory = work_dir or candidate.parent
    fd, raw = tempfile.mkstemp(prefix="refinement-erc-", suffix=".json", dir=directory)
    os.close(fd)
    Path(raw).unlink(missing_ok=True)
    try:
        result, report = adapter.erc(candidate, Path(raw))
        if not result.ok:
            raise ToolError(f"KiCad ERC failed with exit code {result.returncode}")
        summary = _erc_violation_summary(report)
        status = _structural_status(
            lint_errors=lint_errors,
            blocking_erc_types=summary.blocking_types,
            baseline=baseline,
        )
        return CandidateStructuralValidationReport(
            "1.0",
            status,
            lint_errors,
            summary.total_count,
            summary.blocking_count,
            summary.blocking_types,
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


def _structural_status(
    *,
    lint_errors: tuple[str, ...],
    blocking_erc_types: tuple[str, ...],
    baseline: CandidateStructuralValidationReport | None,
) -> str:
    if baseline is None:
        return "passed" if not lint_errors and not blocking_erc_types else "failed"
    no_new_lint = _multiset_subset(lint_errors, baseline.lint_error_codes)
    no_new_erc = _multiset_subset(
        blocking_erc_types,
        baseline.erc_blocking_violation_types,
    )
    return "passed" if no_new_lint and no_new_erc else "failed"


def _multiset_subset(candidate: tuple[str, ...], baseline: tuple[str, ...]) -> bool:
    candidate_counts = Counter(candidate)
    baseline_counts = Counter(baseline)
    return all(count <= baseline_counts[name] for name, count in candidate_counts.items())


def _erc_violation_count(report: dict[str, object] | None) -> int:
    return _erc_violation_summary(report).total_count


def _erc_violation_summary(report: dict[str, object] | None) -> _ErcViolationSummary:
    violations = _erc_violations(report)
    blocking = [item for item in violations if _erc_violation_is_blocking(item)]
    blocking_types = tuple(sorted(_erc_violation_type(item) for item in blocking))
    return _ErcViolationSummary(
        total_count=len(violations),
        blocking_count=len(blocking),
        blocking_types=blocking_types,
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
    excluded = violation.get("excluded", False)
    if not isinstance(excluded, bool):
        raise _invalid_erc_report()
    if excluded:
        return False
    severity = violation.get("severity")
    if severity is None:
        # Legacy reports did not consistently include severity. Preserve the old
        # fail-closed behavior for an unclassified violation.
        return True
    if not isinstance(severity, str):
        raise _invalid_erc_report()
    return severity.strip().lower() != "warning"


def _erc_violation_type(violation: dict[str, object]) -> str:
    value = violation.get("type")
    if value is None:
        return _LEGACY_ERC_TYPE
    if not isinstance(value, str) or not value.strip():
        raise _invalid_erc_report()
    return value.strip()


def _valid_violation_list(value: object) -> TypeGuard[list[dict[str, object]]]:
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


def _invalid_erc_report() -> UserError:
    return UserError(
        "KiCad ERC did not produce a usable violation report.",
        code="REFINEMENT_ERC_INVALID_REPORT",
    )

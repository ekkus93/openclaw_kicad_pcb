"""Structural and KiCad ERC validation for refinement candidates."""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

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
        count = _erc_violation_count(report)
        return CandidateStructuralValidationReport(
            "1.0",
            "passed" if count == 0 else "failed",
            (),
            count,
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
    if not isinstance(report, dict):
        raise _invalid_erc_report()

    if "violations" in report:
        violations = report["violations"]
        if not _valid_violation_list(violations):
            raise _invalid_erc_report()
        return len(violations)

    sheets = report.get("sheets")
    if not isinstance(sheets, list):
        raise _invalid_erc_report()

    count = 0
    for sheet in sheets:
        if not isinstance(sheet, dict):
            raise _invalid_erc_report()
        violations = sheet.get("violations")
        if not _valid_violation_list(violations):
            raise _invalid_erc_report()
        count += len(violations)
    return count


def _valid_violation_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


def _invalid_erc_report() -> UserError:
    return UserError(
        "KiCad ERC did not produce a usable violation report.",
        code="REFINEMENT_ERC_INVALID_REPORT",
    )

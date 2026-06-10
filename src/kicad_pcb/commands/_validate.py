"""Shared validation helpers for netlist commands.

Two public functions are provided:

:func:`full_validate`       — run all three IR validation layers and return
                              the parsed :class:`~kicad_pcb.circuit_ir.CircuitIR`.
:func:`advisory_warnings`   — return non-blocking advisory warnings for
                              common IR mistakes (floating components, single-pin
                              nets).

These are extracted from ``netlist.py`` to avoid duplicating the 3-layer
validation sequence across ``cmd_validate_netlist``, ``cmd_fix_netlist``, and
``cmd_new_from_netlist``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..circuit_ir import CircuitIR
from ..errors import ErrorCode, UserError
from ..ir.validate import validate_circuit_ir, validate_ir_symbols
from ..symbol_index import SymbolIndex
from ._validate_connectivity import _audio_connector_warnings, _generic_connectivity_warnings
from ._validate_opamp import _audio_opamp_warnings
from ._validate_timer555 import _TIMER555_HARD_FAIL_CODES, _timer555_warnings


@dataclass(frozen=True)
class AdvisoryFinding:
    family: str
    code: str
    message: str
    details: dict[str, object]
    severity: str = "warning"

    def as_dict(self) -> dict[str, object]:
        return {
            "family": self.family,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True)
class _CircuitLintRule:
    family: str
    checker: Callable[[CircuitIR, SymbolIndex | None], list[dict[str, object]]]
    default_severity: str = "warning"
    severity_overrides: tuple[tuple[str, str], ...] = ()

    def severity_for(self, code: str) -> str:
        for overridden_code, severity in self.severity_overrides:
            if overridden_code == code:
                return severity
        return self.default_severity


_CIRCUIT_LINT_RULES: tuple[_CircuitLintRule, ...] = (
    _CircuitLintRule(family="generic", checker=_generic_connectivity_warnings),
    _CircuitLintRule(family="audio_connector", checker=_audio_connector_warnings),
    _CircuitLintRule(family="audio_opamp", checker=_audio_opamp_warnings),
    _CircuitLintRule(
        family="timer555_pwm",
        checker=_timer555_warnings,
        severity_overrides=tuple((code, "hard_fail") for code in sorted(_TIMER555_HARD_FAIL_CODES)),
    ),
)


def advisory_findings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None = None,
) -> tuple[AdvisoryFinding, ...]:
    findings: list[AdvisoryFinding] = []
    for rule in _CIRCUIT_LINT_RULES:
        for finding in rule.checker(ir, symbol_index):
            code = finding.get("code")
            message = finding.get("message")
            if not isinstance(code, str) or not isinstance(message, str):
                continue
            details_obj = finding.get("details")
            details = details_obj if isinstance(details_obj, dict) else {}
            findings.append(
                AdvisoryFinding(
                    family=rule.family,
                    code=code,
                    message=message,
                    details=details,
                    severity=rule.severity_for(code),
                )
            )
    return tuple(findings)


def blocking_advisory_findings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None = None,
) -> tuple[AdvisoryFinding, ...]:
    return tuple(
        finding
        for finding in advisory_findings(ir, symbol_index)
        if finding.severity == "hard_fail"
    )


def raise_for_blocking_advisories(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None = None,
) -> None:
    blocking = blocking_advisory_findings(ir, symbol_index)
    if not blocking:
        return

    summary = "; ".join(f"{finding.code}: {finding.message}" for finding in blocking[:5])
    if len(blocking) > 5:
        summary += f" (and {len(blocking) - 5} more)"
    raise UserError(
        f"Circuit IR failed domain-specific lint validation: {summary}",
        code=ErrorCode.IR_SEMANTIC_INVALID,
        details={"blocking_lints": [finding.as_dict() for finding in blocking]},
    )


def full_validate(path: Path, symbol_index: SymbolIndex) -> CircuitIR:
    """Run all three validation layers on *path* and return the parsed IR.

    Layers (run in order; each raises on first failure):

    1. **Schema** — Pydantic validation of the JSON structure.
       Raises :class:`~kicad_pcb.errors.UserError` (``IR_SCHEMA_INVALID``).
    2. **Semantic** — duplicate refs/nets, zero-pin nets, unknown component refs.
       Raises :class:`~kicad_pcb.errors.UserError` (``IR_SEMANTIC_INVALID``).
    3. **Symbol + pin** — every symbol exists in *symbol_index*, every pin is
       valid for its symbol.
       Raises :class:`~kicad_pcb.errors.UserError` (``SYMBOL_NOT_FOUND`` /
       ``PIN_INVALID``).
    """
    ir = CircuitIR.load(path)  # Layer 1: schema
    validate_circuit_ir(ir)  # Layer 2: semantic
    # Layer 3: symbol + pin — unknown symbols become warnings; result discarded here
    validate_ir_symbols(ir, symbol_index)
    return ir


def advisory_warnings(
    ir: CircuitIR,
    symbol_index: SymbolIndex | None = None,
) -> list[dict[str, object]]:
    """Return domain lint findings as JSON-serializable warning payloads."""

    return [finding.as_dict() for finding in advisory_findings(ir, symbol_index)]

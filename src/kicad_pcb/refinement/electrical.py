"""Production electrical-invariance verification for schematic refinement."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.circuit_ir import CircuitIR, NetIR
from kicad_pcb.compat import KiCadVersion
from kicad_pcb.corpus.kicadsexpr import KicadSexprNet, parse_kicadsexpr_nets
from kicad_pcb.corpus.kicadxml import (
    kicadxml_to_circuit_ir,
    parse_kicadxml_netlist,
    schematic_symbols_by_ref,
)
from kicad_pcb.electrical_equivalence import (
    ElectricalMismatch,
    SchematicElectricalFingerprint,
    build_circuit_ir_fingerprint,
    compare_circuit_ir_equivalence,
)
from kicad_pcb.errors import ErrorCode, ParseError, ToolError, UserError
from kicad_pcb.sch_doc import SchematicDoc

from .schematic_semantics import SchematicSemanticSnapshot, extract_schematic_semantics

_MIN_REFINEMENT_KICAD = KiCadVersion(9, 0, 0)
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SchematicElectricalBaseline:
    """Immutable binding between authoritative IR and accepted schematic."""

    schema_version: str
    authoritative_fingerprint: SchematicElectricalFingerprint
    authoritative_hash: str
    accepted_schematic_hash: str
    accepted_footprints: tuple[tuple[str, str], ...]
    accepted_no_connect_terminals: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class SchematicElectricalVerificationReport:
    """Production hard-gate verification result."""

    status: str
    authoritative_hash: str
    accepted_schematic_hash: str
    candidate_schematic_hash: str
    candidate_fingerprint_hash: str | None
    mismatches: tuple[ElectricalMismatch, ...] = field(default_factory=tuple)
    kicad_version: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "passed"


def build_schematic_electrical_baseline(
    authoritative_ir: CircuitIR,
    accepted_schematic: Path,
) -> SchematicElectricalBaseline:
    """Capture presentation-adjacent electrical state from an accepted schematic."""

    semantic = extract_schematic_semantics(accepted_schematic)
    footprints = _logical_footprints(authoritative_ir, semantic)
    nc = tuple(
        (terminal.ref, terminal.pin, terminal.unit) for terminal in semantic.no_connect_terminals
    )
    fingerprint = build_circuit_ir_fingerprint(authoritative_ir)
    return SchematicElectricalBaseline(
        schema_version="1.0",
        authoritative_fingerprint=fingerprint,
        authoritative_hash=fingerprint.sha256(),
        accepted_schematic_hash=_sha256_file(accepted_schematic),
        accepted_footprints=tuple(sorted(footprints.items())),
        accepted_no_connect_terminals=tuple(sorted(nc)),
    )


def verify_schematic_electrical_invariance(
    *,
    authoritative_ir: CircuitIR,
    baseline: SchematicElectricalBaseline,
    candidate_schematic: Path,
    adapter: KicadCliAdapter,
    work_dir: Path | None = None,
) -> SchematicElectricalVerificationReport:
    """Verify the actual candidate KiCad artifact against immutable baselines."""

    _verify_baseline_binding(authoritative_ir, baseline)
    candidate_semantic = extract_schematic_semantics(candidate_schematic)
    candidate_hash = _sha256_file(candidate_schematic)

    version = adapter.detected_version
    if version is None or version < _MIN_REFINEMENT_KICAD:
        raise ToolError(
            "kicad-cli >= 9.0.0 is required for production schematic refinement.",
            code=ErrorCode.KICAD_CLI_MISSING,
            details={
                "minimum_version": str(_MIN_REFINEMENT_KICAD),
                "detected_version": str(version) if version is not None else None,
            },
        )

    export_dir = work_dir or candidate_schematic.parent
    export_dir.mkdir(parents=True, exist_ok=True)
    xml_export_path = export_dir / f".{candidate_schematic.stem}.refinement.netlist.kicadxml"
    native_export_path = export_dir / f".{candidate_schematic.stem}.refinement.netlist.kicadsexpr"
    try:
        result, xml_content = adapter.export_netlist(
            candidate_schematic,
            xml_export_path,
            netlist_format="kicadxml",
        )
        if not result.ok or not xml_content.strip():
            raise ToolError(
                "Candidate schematic XML netlist export failed during electrical verification.",
                code="ELECTRICAL_INVARIANCE_FAILED",
                details={
                    "returncode": result.returncode,
                    "stderr": result.stderr,
                    "stdout": result.stdout,
                },
            )

        native_result, native_content = adapter.export_netlist(
            candidate_schematic,
            native_export_path,
            netlist_format="kicadsexpr",
        )
        if not native_result.ok or not native_content.strip():
            raise ToolError(
                "Candidate schematic native netlist export failed during electrical verification.",
                code="ELECTRICAL_INVARIANCE_FAILED",
                details={
                    "returncode": native_result.returncode,
                    "stderr": native_result.stderr,
                    "stdout": native_result.stdout,
                },
            )
        try:
            native_nets = parse_kicadsexpr_nets(native_content)
        except (ParseError, ValueError) as exc:
            raise ToolError(
                "Candidate KiCad native netlist could not be parsed during electrical verification.",
                code="ELECTRICAL_INVARIANCE_FAILED",
                details={"reason": str(exc)},
            ) from exc

        parsed = parse_kicadxml_netlist(xml_export_path)
        fallback_symbols = schematic_symbols_by_ref(SchematicDoc.load(candidate_schematic))
        try:
            candidate_ir = kicadxml_to_circuit_ir(
                parsed,
                fallback_symbols_by_ref=fallback_symbols,
            )
        except ValidationError as exc:
            raise UserError(
                "Candidate KiCad netlist cannot be represented as Circuit IR.",
                code="ELECTRICAL_INVARIANCE_FAILED",
                details={"errors": exc.errors()},
            ) from exc

        candidate_ir = _remove_explicit_helpers(candidate_ir, candidate_semantic.helper_refs)
        expected_ir = _authoritative_with_baseline_footprints(authoritative_ir, baseline)
        candidate_ir = _supplement_xml_omitted_unnamed_nets(
            expected_ir=expected_ir,
            candidate_ir=candidate_ir,
            native_nets=native_nets,
            helper_refs=candidate_semantic.helper_refs,
        )

        electrical = compare_circuit_ir_equivalence(
            expected_ir,
            candidate_ir,
            compare_footprints=True,
        )
        extra_mismatches = _compare_schematic_semantics(
            authoritative_ir=authoritative_ir,
            baseline=baseline,
            candidate=candidate_semantic,
        )
        mismatches = tuple(
            sorted(
                (*electrical.mismatches, *extra_mismatches),
                key=lambda mismatch: (mismatch.field, mismatch.code),
            )
        )
        candidate_fingerprint = build_circuit_ir_fingerprint(candidate_ir)
        return SchematicElectricalVerificationReport(
            status="passed" if not mismatches else "failed",
            authoritative_hash=baseline.authoritative_hash,
            accepted_schematic_hash=baseline.accepted_schematic_hash,
            candidate_schematic_hash=candidate_hash,
            candidate_fingerprint_hash=candidate_fingerprint.sha256(),
            mismatches=mismatches,
            kicad_version=str(version),
        )
    finally:
        _remove_verification_artifact(xml_export_path)
        _remove_verification_artifact(native_export_path)


def require_schematic_electrical_invariance(
    *,
    authoritative_ir: CircuitIR,
    baseline: SchematicElectricalBaseline,
    candidate_schematic: Path,
    adapter: KicadCliAdapter,
    work_dir: Path | None = None,
) -> SchematicElectricalVerificationReport:
    """Run verification and raise on any semantic mismatch."""

    report = verify_schematic_electrical_invariance(
        authoritative_ir=authoritative_ir,
        baseline=baseline,
        candidate_schematic=candidate_schematic,
        adapter=adapter,
        work_dir=work_dir,
    )
    if not report.passed:
        raise UserError(
            "Candidate schematic failed electrical invariance verification.",
            code="ELECTRICAL_INVARIANCE_FAILED",
            details={
                "mismatches": [
                    {
                        "code": mismatch.code,
                        "field": mismatch.field,
                        "expected": mismatch.expected,
                        "actual": mismatch.actual,
                        "component_ref": mismatch.component_ref,
                        "net_name": mismatch.net_name,
                        "pin": mismatch.pin,
                        "details": mismatch.details,
                    }
                    for mismatch in report.mismatches
                ],
                "candidate_schematic_hash": report.candidate_schematic_hash,
            },
        )
    return report


def _verify_baseline_binding(
    authoritative_ir: CircuitIR,
    baseline: SchematicElectricalBaseline,
) -> None:
    current = build_circuit_ir_fingerprint(authoritative_ir).sha256()
    if current != baseline.authoritative_hash:
        raise UserError(
            "Electrical baseline is stale for the current authoritative Circuit IR.",
            code="REFINEMENT_STALE",
            details={
                "baseline_authoritative_hash": baseline.authoritative_hash,
                "current_authoritative_hash": current,
            },
        )


def _logical_footprints(
    authoritative_ir: CircuitIR,
    semantic: SchematicSemanticSnapshot,
) -> dict[str, str]:
    source_refs = {component.ref for component in authoritative_ir.components}
    source_units = {
        pin.ref for net in authoritative_ir.nets for pin in net.pins if pin.unit is not None
    }
    candidate_components = [
        component for component in semantic.components if component.ref not in semantic.helper_refs
    ]
    suffix_counts: dict[str, int] = {}
    for component in candidate_components:
        base = _suffix_base(component.ref, source_refs)
        if base is not None:
            suffix_counts[base] = suffix_counts.get(base, 0) + 1

    footprints: dict[str, str] = {}
    for component in candidate_components:
        logical_ref = component.ref
        base = _suffix_base(component.ref, source_refs)
        if base is not None and (base in source_units or suffix_counts.get(base, 0) > 1):
            logical_ref = base
        if logical_ref not in source_refs:
            continue
        existing = footprints.get(logical_ref)
        if existing is not None and existing != component.footprint:
            raise UserError(
                f"Placed units for {logical_ref} disagree on footprint.",
                code="ELECTRICAL_INVARIANCE_FAILED",
                details={
                    "ref": logical_ref,
                    "first_footprint": existing,
                    "other_footprint": component.footprint,
                },
            )
        footprints[logical_ref] = component.footprint
    return footprints


def _authoritative_with_baseline_footprints(
    authoritative_ir: CircuitIR,
    baseline: SchematicElectricalBaseline,
) -> CircuitIR:
    accepted = dict(baseline.accepted_footprints)
    components = [
        component.model_copy(
            update={
                "footprint": component.footprint
                if component.footprint is not None
                else accepted.get(component.ref) or None
            },
            deep=True,
        )
        for component in authoritative_ir.components
    ]
    return CircuitIR(
        version=authoritative_ir.version,
        components=components,
        nets=authoritative_ir.nets,
        options=authoritative_ir.options,
    )


def _compare_schematic_semantics(
    *,
    authoritative_ir: CircuitIR,
    baseline: SchematicElectricalBaseline,
    candidate: SchematicSemanticSnapshot,
) -> tuple[ElectricalMismatch, ...]:
    mismatches: list[ElectricalMismatch] = []
    expected_footprints = dict(baseline.accepted_footprints)
    candidate_footprints = _logical_footprints(authoritative_ir, candidate)
    for ref in sorted(set(expected_footprints) | set(candidate_footprints)):
        expected = expected_footprints.get(ref, "")
        actual = candidate_footprints.get(ref, "")
        authoritative = next(
            (component for component in authoritative_ir.components if component.ref == ref),
            None,
        )
        if authoritative is not None and authoritative.footprint is not None:
            expected = authoritative.footprint
        if expected != actual:
            mismatches.append(
                ElectricalMismatch(
                    code="COMPONENT_FOOTPRINT_MISMATCH",
                    field=f"component_footprint:{ref}",
                    expected=expected,
                    actual=actual,
                    component_ref=ref,
                )
            )

    expected_nc = baseline.accepted_no_connect_terminals
    actual_nc = tuple(
        sorted(
            (terminal.ref, terminal.pin, terminal.unit)
            for terminal in candidate.no_connect_terminals
        )
    )
    if expected_nc != actual_nc:
        mismatches.append(
            ElectricalMismatch(
                code="NO_CONNECT_STATE_MISMATCH",
                field="no_connect_terminals",
                expected=str(expected_nc),
                actual=str(actual_nc),
            )
        )
    return tuple(mismatches)


def _supplement_xml_omitted_unnamed_nets(
    *,
    expected_ir: CircuitIR,
    candidate_ir: CircuitIR,
    native_nets: tuple[KicadSexprNet, ...],
    helper_refs: tuple[str, ...],
) -> CircuitIR:
    """Recover only XML-omitted anonymous nets proven by native KiCad topology.

    This is deliberately fail-closed. A candidate net is added only when one
    unique native autogenerated net has the exact authoritative ref/pin
    partition and none of those terminals is already assigned by the XML
    export. Explicitly named native nets, ambiguous matches, changed topology,
    and repeated ref/pin identities are never supplemented.
    """

    helper_set = set(helper_refs)
    candidate_names = {net.name for net in candidate_ir.nets}
    assigned_terminals = {
        (pin.ref, pin.pin) for net in candidate_ir.nets for pin in net.pins
    }
    additions: list[NetIR] = []

    for expected_net in expected_ir.nets:
        if expected_net.name in candidate_names:
            continue

        expected_keys = {(pin.ref, pin.pin) for pin in expected_net.pins}
        if len(expected_keys) != len(expected_net.pins):
            continue
        if expected_keys & assigned_terminals:
            continue

        matches: list[KicadSexprNet] = []
        for native_net in native_nets:
            if not native_net.autogenerated:
                continue
            native_keys = {
                (pin.ref, pin.pin) for pin in native_net.pins if pin.ref not in helper_set
            }
            if native_keys == expected_keys:
                matches.append(native_net)

        if len(matches) != 1:
            continue

        native_net = matches[0]
        additions.append(
            NetIR(
                name=native_net.name,
                pins=[pin.model_copy(deep=True) for pin in expected_net.pins],
            )
        )
        assigned_terminals.update(expected_keys)
        _log.warning(
            "KiCad XML netlist omitted unnamed net; recovered exact topology "
            "from native kicadsexpr export",
            extra={
                "authoritative_net": expected_net.name,
                "native_net": native_net.name,
                "terminals": sorted(expected_keys),
            },
        )

    if not additions:
        return candidate_ir
    return CircuitIR(
        version=candidate_ir.version,
        components=candidate_ir.components,
        nets=[*candidate_ir.nets, *additions],
        options=candidate_ir.options,
    )


def _remove_explicit_helpers(ir: CircuitIR, helper_refs: tuple[str, ...]) -> CircuitIR:
    helpers = set(helper_refs)
    if not helpers:
        return ir
    components = [component for component in ir.components if component.ref not in helpers]
    nets: list[NetIR] = []
    for net in ir.nets:
        pins = [pin for pin in net.pins if pin.ref not in helpers]
        if pins:
            nets.append(NetIR(name=net.name, pins=pins))
    return CircuitIR(version=ir.version, components=components, nets=nets, options=ir.options)


def _remove_verification_artifact(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        _log.warning(
            "failed to remove refinement netlist verification artifact",
            extra={"path": str(path), "error_type": type(exc).__name__},
        )


def _suffix_base(ref: str, source_refs: set[str]) -> str | None:
    if len(ref) < 2 or not ref[-1].isalpha():
        return None
    base = ref[:-1]
    return base if base in source_refs else None


def _sha256_file(path: Path) -> str:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise UserError(
            f"Failed to read schematic for refinement verification: {path}",
            code=ErrorCode.IO_ERROR,
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    return hashlib.sha256(payload).hexdigest()

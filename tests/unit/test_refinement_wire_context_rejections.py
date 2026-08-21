from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.errors import UserError
from kicad_pcb.refinement.operations import execute_layout_operations


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _wire_fixture(tmp_path: Path) -> Path:
    path = tmp_path / "wire-context.kicad_sch"
    path.write_text(
        """(kicad_sch
  (version 20230121)
  (generator eeschema)
  (uuid "root")
  (paper "A4")
  (wire
    (pts (xy 10.16 10.16) (xy 15.24 10.16) (xy 15.24 15.24))
    (uuid "w1"))
  (sheet_instances (path "/" (page "1"))))""",
        encoding="utf-8",
    )
    return path


def _authoritative_ir() -> CircuitIR:
    return CircuitIR.model_validate(
        {
            "version": "1.0",
            "components": [{"ref": "R1", "symbol": "Device:R"}],
            "nets": [{"name": "N", "pins": [{"ref": "R1", "pin": "1"}]}],
        }
    )


def _shorten_operation(source: str, *, net_name: str = "N") -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "operation_id": "shorten-wire",
        "source_schematic_hash": source,
        "operation_type": "shorten_wire_path",
        "arguments": {
            "wire_uuid": "w1",
            "net_name": net_name,
            "expected_points_mm": [
                [10.16, 10.16],
                [15.24, 10.16],
                [15.24, 15.24],
            ],
        },
    }


def test_unproven_wire_endpoints_are_structured_rejection(tmp_path: Path) -> None:
    path = _wire_fixture(tmp_path)
    before = path.read_bytes()
    source = _hash(path)

    result = execute_layout_operations(
        path,
        [_shorten_operation(source)],
        expected_source_hash=source,
        authoritative_ir=_authoritative_ir(),
    )

    assert result.source_hash == source
    assert result.candidate_hash == source
    assert result.results[0].status == "rejected"
    assert result.results[0].details == {
        "reason_code": "REFINEMENT_OPERATION_INVALID_WIRE_CONTEXT",
        "reason": "Wire endpoints lack direct authoritative net evidence.",
        "reason_details": {
            "net_name": "N",
            "endpoints": [[10.16, 10.16], [15.24, 15.24]],
        },
    }
    assert path.read_bytes() == before


def test_missing_authoritative_ir_for_wire_operation_still_fails_closed(
    tmp_path: Path,
) -> None:
    path = _wire_fixture(tmp_path)
    before = path.read_bytes()
    source = _hash(path)

    with pytest.raises(UserError, match="requires authoritative Circuit IR") as exc_info:
        execute_layout_operations(
            path,
            [_shorten_operation(source)],
            expected_source_hash=source,
        )

    assert exc_info.value.code == "REFINEMENT_WIRE_CONTEXT_REQUIRED"
    assert path.read_bytes() == before


def test_unknown_authoritative_net_for_wire_operation_still_fails_closed(
    tmp_path: Path,
) -> None:
    path = _wire_fixture(tmp_path)
    before = path.read_bytes()
    source = _hash(path)

    with pytest.raises(UserError, match="unknown authoritative net") as exc_info:
        execute_layout_operations(
            path,
            [_shorten_operation(source, net_name="MISSING")],
            expected_source_hash=source,
            authoritative_ir=_authoritative_ir(),
        )

    assert exc_info.value.code == "REFINEMENT_WIRE_CONTEXT_REQUIRED"
    assert path.read_bytes() == before

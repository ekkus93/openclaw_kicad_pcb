from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import kicad_pcb.refinement.operations as refinement_operations
from kicad_pcb.errors import UserError
from kicad_pcb.refinement.operations import execute_layout_operations


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _collision_fixture(tmp_path: Path) -> Path:
    path = tmp_path / "collision.kicad_sch"
    path.write_text(
        """(kicad_sch
  (version 20230121)
  (generator eeschema)
  (uuid "root")
  (paper "A4")
  (lib_symbols
    (symbol "Device:R"
      (pin passive line (at 0 0 0) (length 2.54) (name "~") (number "1"))
      (pin passive line (at 7.62 0 180) (length 2.54) (name "~") (number "2"))))
  (symbol (lib_id "Device:R") (at 25.4 25.4 0) (unit 1) (uuid "r1")
    (property "Reference" "R1") (property "Value" "10k") (property "Footprint" ""))
  (symbol (lib_id "Device:R") (at 50.8 25.4 0) (unit 1) (uuid "r2")
    (property "Reference" "R2") (property "Value" "10k") (property "Footprint" ""))
  (symbol (lib_id "Device:R") (at 52.07 25.4 0) (unit 1) (uuid "r3")
    (property "Reference" "R3") (property "Value" "10k") (property "Footprint" ""))
  (label "N" (at 25.4 76.2 0) (uuid "l1"))
  (sheet_instances (path "/" (page "1"))))""",
        encoding="utf-8",
    )
    return path


def _colliding_group_operation(source: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "operation_id": "colliding-group",
        "source_schematic_hash": source,
        "operation_type": "move_component_group",
        "arguments": {
            "targets": [
                {"ref": "R1", "unit": "1"},
                {"ref": "R2", "unit": "1"},
            ],
            "dx_mm": 1.27,
            "dy_mm": 0.0,
        },
    }


def test_collision_is_structured_rejection_and_rolls_back_partial_group(tmp_path: Path) -> None:
    path = _collision_fixture(tmp_path)
    source = _hash(path)

    result = execute_layout_operations(
        path,
        [
            {
                "schema_version": "1.0",
                "operation_id": "move-label",
                "source_schematic_hash": source,
                "operation_type": "move_label",
                "arguments": {"label_uuid": "l1", "x_mm": 26.67, "y_mm": 76.2},
            },
            _colliding_group_operation(source),
        ],
        expected_source_hash=source,
    )

    assert [item.status for item in result.results] == ["applied", "rejected"]
    assert result.results[1].details == {
        "reason_code": "REFINEMENT_OPERATION_COLLISION",
        "reason": "Component move would collide with a stationary pin.",
    }

    semantic = refinement_operations.extract_schematic_semantics_from_doc(
        refinement_operations.SchematicDoc.load(path)
    )
    positions = {item.ref: (item.x, item.y) for item in semantic.components}
    assert positions == {
        "R1": (25.4, 25.4),
        "R2": (50.8, 25.4),
        "R3": (52.07, 25.4),
    }
    doc = refinement_operations.SchematicDoc.load(path)
    label = refinement_operations._find_top_level_uuid(doc, "l1", {"label"})
    assert refinement_operations._node_at(label) == (26.67, 76.2)


def test_all_rejected_operations_leave_candidate_bytes_unchanged(tmp_path: Path) -> None:
    path = _collision_fixture(tmp_path)
    before = path.read_bytes()
    source = _hash(path)

    result = execute_layout_operations(
        path,
        [_colliding_group_operation(source)],
        expected_source_hash=source,
    )

    assert result.source_hash == source
    assert result.candidate_hash == source
    assert result.results[0].status == "rejected"
    assert result.results[0].details["reason_code"] == "REFINEMENT_OPERATION_COLLISION"
    assert path.read_bytes() == before


def test_unclassified_executor_error_still_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _collision_fixture(tmp_path)
    before = path.read_bytes()
    source = _hash(path)

    def fail_unexpected(*_args, **_kwargs):
        raise UserError("Unexpected executor failure.", code="REFINEMENT_INTERNAL_SENTINEL")

    monkeypatch.setattr(refinement_operations, "_apply_operation", fail_unexpected)

    with pytest.raises(UserError, match="Unexpected executor failure") as exc_info:
        execute_layout_operations(
            path,
            [
                {
                    "schema_version": "1.0",
                    "operation_id": "move-label",
                    "source_schematic_hash": source,
                    "operation_type": "move_label",
                    "arguments": {"label_uuid": "l1", "x_mm": 26.67, "y_mm": 76.2},
                }
            ],
            expected_source_hash=source,
        )

    assert exc_info.value.code == "REFINEMENT_INTERNAL_SENTINEL"
    assert path.read_bytes() == before

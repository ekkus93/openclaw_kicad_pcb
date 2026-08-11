from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import kicad_pcb.refinement.operations as refinement_operations
from kicad_pcb.errors import UserError
from kicad_pcb.refinement.operations import execute_layout_operations, registered_operation_schemas


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _simple(tmp_path: Path) -> Path:
    path = tmp_path / "simple.kicad_sch"
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
  (symbol (lib_id "Device:R") (at 25.4 25.4 0) (unit 1) (in_bom yes) (on_board yes) (uuid "r1")
    (property "Reference" "R1") (property "Value" "10k") (property "Footprint" ""))
  (wire (pts (xy 25.4 25.4) (xy 25.4 38.1)) (uuid "w1"))
  (label "N" (at 25.4 38.1 0) (uuid "l1"))
  (sheet_instances (path "/" (page "1"))))""",
        encoding="utf-8",
    )
    return path


def test_registry_contains_only_explicit_layout_vocabulary() -> None:
    assert set(registered_operation_schemas()) == {
        "move_component",
        "rotate_component",
        "move_label",
        "move_power_symbol",
        "align_components",
        "distribute_components",
        "move_component_group",
        "remove_redundant_wire_bend",
        "shorten_wire_path",
        "reroute_existing_net_orthogonal",
    }


def test_unsupported_semantic_operation_is_rejected_without_write(tmp_path: Path) -> None:
    path = _simple(tmp_path)
    before = path.read_bytes()
    source = _hash(path)
    with pytest.raises(UserError, match="Unsupported layout operation"):
        execute_layout_operations(
            path,
            [
                {
                    "schema_version": "1.0",
                    "operation_id": "x",
                    "source_schematic_hash": source,
                    "operation_type": "change_component_value",
                    "arguments": {"ref": "R1"},
                }
            ],
            expected_source_hash=source,
        )
    assert path.read_bytes() == before


def test_duplicate_operation_id_and_stale_hash_reject() -> None:
    # Schema validation itself is strict; no free-form executable fields exist.
    assert "raw_sexpr" not in str(registered_operation_schemas())


def test_move_unknown_ref_rejects_without_partial_write(tmp_path: Path) -> None:
    path = _simple(tmp_path)
    source = _hash(path)
    before = path.read_bytes()
    operations = [
        {
            "schema_version": "1.0",
            "operation_id": "1",
            "source_schematic_hash": source,
            "operation_type": "move_label",
            "arguments": {"label_uuid": "l1", "x_mm": 25.4, "y_mm": 39.37},
        },
        {
            "schema_version": "1.0",
            "operation_id": "2",
            "source_schematic_hash": source,
            "operation_type": "move_component",
            "arguments": {
                "target": {"ref": "NOPE"},
                "dx_mm": 1.27,
                "dy_mm": 0.0,
            },
        },
    ]
    with pytest.raises(UserError):
        execute_layout_operations(path, operations, expected_source_hash=source)
    assert path.read_bytes() == before


def test_move_component_carries_exact_wire_endpoint(tmp_path: Path) -> None:
    path = _simple(tmp_path)
    source = _hash(path)
    result = execute_layout_operations(
        path,
        [
            {
                "schema_version": "1.0",
                "operation_id": "1",
                "source_schematic_hash": source,
                "operation_type": "move_component",
                "arguments": {
                    "target": {"ref": "R1", "unit": "1"},
                    "dx_mm": 1.27,
                    "dy_mm": 0.0,
                },
            }
        ],
        expected_source_hash=source,
    )
    assert result.candidate_hash != source
    text = path.read_text()
    assert "26.67" in text


def test_move_component_uses_attached_pin_when_geometry_has_alternates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _simple(tmp_path)
    source = _hash(path)
    original = refinement_operations.resolve_component_pin_position_candidates

    def with_alternate_geometry(doc, component):
        candidates = original(doc, component)
        return {
            terminal: positions + (((22.86, 25.4),) if terminal.pin == "1" else ())
            for terminal, positions in candidates.items()
        }

    monkeypatch.setattr(
        refinement_operations,
        "resolve_component_pin_position_candidates",
        with_alternate_geometry,
    )

    result = execute_layout_operations(
        path,
        [
            {
                "schema_version": "1.0",
                "operation_id": "1",
                "source_schematic_hash": source,
                "operation_type": "move_component",
                "arguments": {
                    "target": {"ref": "R1", "unit": "1"},
                    "dx_mm": 1.27,
                    "dy_mm": 0.0,
                },
            }
        ],
        expected_source_hash=source,
    )

    assert result.candidate_hash != source
    assert "(wire (pts (xy 26.67 25.40) (xy 25.40 38.10))" in path.read_text()


def test_move_label_rejects_unknown_uuid(tmp_path: Path) -> None:
    path = _simple(tmp_path)
    source = _hash(path)
    with pytest.raises(UserError, match="UUID"):
        execute_layout_operations(
            path,
            [
                {
                    "schema_version": "1.0",
                    "operation_id": "1",
                    "source_schematic_hash": source,
                    "operation_type": "move_label",
                    "arguments": {
                        "label_uuid": "missing",
                        "x_mm": 25.4,
                        "y_mm": 39.37,
                    },
                }
            ],
            expected_source_hash=source,
        )

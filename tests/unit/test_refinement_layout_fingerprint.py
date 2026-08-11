from __future__ import annotations

import hashlib
from pathlib import Path

from kicad_pcb.refinement.layout_fingerprint import compute_schematic_layout_fingerprint
from kicad_pcb.refinement.operations import execute_layout_operations


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _simple(tmp_path: Path, name: str = "simple.kicad_sch") -> Path:
    path = tmp_path / name
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


def test_layout_fingerprint_ignores_byte_only_formatting_change(tmp_path: Path) -> None:
    first = _simple(tmp_path, "first.kicad_sch")
    second = tmp_path / "second.kicad_sch"
    second.write_bytes(first.read_bytes() + b"\n\n")

    assert _hash(first) != _hash(second)
    assert (
        compute_schematic_layout_fingerprint(first).digest
        == compute_schematic_layout_fingerprint(second).digest
    )


def test_layout_fingerprint_is_distinct_from_semantic_change_detection(tmp_path: Path) -> None:
    first = _simple(tmp_path, "first.kicad_sch")
    second = tmp_path / "second.kicad_sch"
    second.write_text(
        first.read_text(encoding="utf-8").replace(
            '(property "Value" "10k")',
            '(property "Value" "12k")',
        ),
        encoding="utf-8",
    )

    assert _hash(first) != _hash(second)
    assert (
        compute_schematic_layout_fingerprint(first).digest
        == compute_schematic_layout_fingerprint(second).digest
    )


def test_layout_fingerprint_ignores_wire_point_direction(tmp_path: Path) -> None:
    first = _simple(tmp_path, "first.kicad_sch")
    second = tmp_path / "second.kicad_sch"
    second.write_text(
        first.read_text(encoding="utf-8").replace(
            "(wire (pts (xy 25.4 25.4) (xy 25.4 38.1))",
            "(wire (pts (xy 25.4 38.1) (xy 25.4 25.4))",
        ),
        encoding="utf-8",
    )

    assert _hash(first) != _hash(second)
    assert (
        compute_schematic_layout_fingerprint(first).digest
        == compute_schematic_layout_fingerprint(second).digest
    )


def test_layout_fingerprint_changes_for_deterministic_geometry_edit(tmp_path: Path) -> None:
    path = _simple(tmp_path)
    source_hash = _hash(path)
    before = compute_schematic_layout_fingerprint(path)

    execute_layout_operations(
        path,
        [
            {
                "schema_version": "1.0",
                "operation_id": "move-label",
                "source_schematic_hash": source_hash,
                "operation_type": "move_label",
                "arguments": {
                    "label_uuid": "l1",
                    "x_mm": 26.67,
                    "y_mm": 38.1,
                },
            }
        ],
        expected_source_hash=source_hash,
    )
    after = compute_schematic_layout_fingerprint(path)

    assert after.digest != before.digest
    assert after.labels != before.labels

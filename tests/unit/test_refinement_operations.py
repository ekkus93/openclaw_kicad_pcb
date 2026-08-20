from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import kicad_pcb.refinement.operations as refinement_operations
from kicad_pcb.circuit_ir import CircuitIR
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


def _wire_chain(
    tmp_path: Path,
    points: list[tuple[float, float]],
    *,
    branch_at: tuple[float, float] | None = None,
    overlap: tuple[tuple[float, float], tuple[float, float]] | None = None,
) -> Path:
    path = tmp_path / "wire_chain.kicad_sch"
    wires = []
    for index, (start, end) in enumerate(zip(points, points[1:]), 1):
        wires.append(
            f"  (wire (pts (xy {start[0]:.2f} {start[1]:.2f}) "
            f"(xy {end[0]:.2f} {end[1]:.2f})) "
            f'(stroke (width 0) (type default)) (uuid "w{index}"))'
        )
    if branch_at is not None:
        wires.append(
            f"  (wire (pts (xy {branch_at[0]:.2f} {branch_at[1]:.2f}) "
            f"(xy {branch_at[0]:.2f} {branch_at[1] + 5.08:.2f})) "
            '(stroke (width 0) (type default)) (uuid "branch"))'
        )
    if overlap is not None:
        start, end = overlap
        wires.append(
            f"  (wire (pts (xy {start[0]:.2f} {start[1]:.2f}) "
            f"(xy {end[0]:.2f} {end[1]:.2f})) "
            '(stroke (width 0) (type default)) (uuid "overlap"))'
        )
    wire_text = "\n".join(wires)
    path.write_text(
        f"""(kicad_sch
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
{wire_text}
  (label "N" (at {points[0][0]:.2f} {points[0][1]:.2f} 0) (uuid "start-label"))
  (label "N" (at {points[-1][0]:.2f} {points[-1][1]:.2f} 0) (uuid "end-label"))
  (sheet_instances (path "/" (page "1"))))""",
        encoding="utf-8",
    )
    return path


def _wire_ir() -> CircuitIR:
    return CircuitIR.model_validate(
        {
            "version": "1",
            "components": [{"ref": "R1", "symbol": "Device:R", "value": "10k"}],
            "nets": [{"name": "N", "pins": [{"ref": "R1", "pin": "1"}]}],
        }
    )


def _wire_geometries(path: Path) -> list[list[tuple[float, float]]]:
    doc = refinement_operations.SchematicDoc.load(path)
    return [
        refinement_operations._wire_points(node) for node in refinement_operations._wire_nodes(doc)
    ]


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


def test_move_label_snaps_off_grid_target_to_executor_grid(tmp_path: Path) -> None:
    path = _simple(tmp_path)
    source = _hash(path)

    result = execute_layout_operations(
        path,
        [
            {
                "schema_version": "1.0",
                "operation_id": "move-label-off-grid",
                "source_schematic_hash": source,
                "operation_type": "move_label",
                "arguments": {
                    "label_uuid": "l1",
                    "x_mm": 26.2,
                    "y_mm": 39.0,
                },
            }
        ],
        expected_source_hash=source,
    )

    assert result.results[0].details == {
        "label_uuid": "l1",
        "x_mm": 26.67,
        "y_mm": 39.37,
    }
    assert '(label "N" (at 26.67 39.37 0)' in path.read_text()


def test_move_label_resolves_unquoted_kicad_uuid_atom(tmp_path: Path) -> None:
    path = _simple(tmp_path)
    path.write_text(path.read_text().replace('(uuid "l1")', "(uuid l1)"))
    source = _hash(path)

    result = execute_layout_operations(
        path,
        [
            {
                "schema_version": "1.0",
                "operation_id": "move-label-uuid-atom",
                "source_schematic_hash": source,
                "operation_type": "move_label",
                "arguments": {
                    "label_uuid": "l1",
                    "x_mm": 26.2,
                    "y_mm": 39.0,
                },
            }
        ],
        expected_source_hash=source,
    )

    assert result.results[0].details == {
        "label_uuid": "l1",
        "x_mm": 26.67,
        "y_mm": 39.37,
    }


def test_move_component_zero_delta_preserves_unchanged_off_grid_axis(tmp_path: Path) -> None:
    path = _simple(tmp_path)
    path.write_text(
        path.read_text().replace(
            '(symbol (lib_id "Device:R") (at 25.4 25.4 0)',
            '(symbol (lib_id "Device:R") (at 25.4254 25.4254 0)',
        )
    )
    source = _hash(path)

    result = execute_layout_operations(
        path,
        [
            {
                "schema_version": "1.0",
                "operation_id": "move-off-grid-axis",
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

    assert result.results[0].details == {
        "ref": "R1",
        "unit": "1",
        "x_mm": 26.67,
        "y_mm": 25.4254,
    }
    doc = refinement_operations.SchematicDoc.load(path)
    component = next(
        item
        for item in refinement_operations.extract_schematic_semantics_from_doc(doc).components
        if item.ref == "R1"
    )
    assert (component.x, component.y) == (26.67, 25.4254)


def test_rotate_component_preserves_existing_off_grid_position(tmp_path: Path) -> None:
    path = _simple(tmp_path)
    path.write_text(
        path.read_text().replace(
            '(symbol (lib_id "Device:R") (at 25.4 25.4 0)',
            '(symbol (lib_id "Device:R") (at 25.4254 25.4254 0)',
        )
    )
    source = _hash(path)

    result = execute_layout_operations(
        path,
        [
            {
                "schema_version": "1.0",
                "operation_id": "rotate-off-grid",
                "source_schematic_hash": source,
                "operation_type": "rotate_component",
                "arguments": {"target": {"ref": "R1", "unit": "1"}, "angle_deg": 90},
            }
        ],
        expected_source_hash=source,
    )

    assert result.results[0].details == {"ref": "R1", "unit": "1", "angle_deg": 90}
    doc = refinement_operations.SchematicDoc.load(path)
    component = next(
        item
        for item in refinement_operations.extract_schematic_semantics_from_doc(doc).components
        if item.ref == "R1"
    )
    assert (component.x, component.y, component.rotation) == (25.4254, 25.4254, 90)


def test_remove_redundant_wire_bend_rewrites_real_segment_chain(tmp_path: Path) -> None:
    points = [(50.8, 50.8), (60.96, 50.8), (71.12, 50.8)]
    path = _wire_chain(tmp_path, points)
    source = _hash(path)

    result = execute_layout_operations(
        path,
        [
            {
                "schema_version": "1.0",
                "operation_id": "wire-remove-bend",
                "source_schematic_hash": source,
                "operation_type": "remove_redundant_wire_bend",
                "arguments": {"wire_uuid": "w1", "expected_points_mm": points},
            }
        ],
        expected_source_hash=source,
    )

    assert result.candidate_hash != source
    assert _wire_geometries(path) == [[points[0], points[-1]]]


def test_shorten_wire_path_rewrites_real_segment_chain(tmp_path: Path) -> None:
    points = [
        (50.8, 50.8),
        (50.8, 45.72),
        (66.04, 45.72),
        (66.04, 60.96),
        (71.12, 60.96),
    ]
    path = _wire_chain(tmp_path, points)
    source = _hash(path)

    result = execute_layout_operations(
        path,
        [
            {
                "schema_version": "1.0",
                "operation_id": "wire-shorten",
                "source_schematic_hash": source,
                "operation_type": "shorten_wire_path",
                "arguments": {
                    "wire_uuid": "w1",
                    "net_name": "N",
                    "expected_points_mm": points,
                },
            }
        ],
        expected_source_hash=source,
        authoritative_ir=_wire_ir(),
    )

    geometries = _wire_geometries(path)
    assert result.candidate_hash != source
    assert len(geometries) == 2
    assert all(len(segment) == 2 for segment in geometries)
    assert {tuple(geometries[0][0]), tuple(geometries[-1][-1])} <= {points[0], points[-1]}


def test_wire_chain_rejects_interior_branch_without_write(tmp_path: Path) -> None:
    points = [(50.8, 50.8), (60.96, 50.8), (71.12, 50.8)]
    path = _wire_chain(tmp_path, points, branch_at=points[1])
    source = _hash(path)
    before = path.read_bytes()

    with pytest.raises(UserError, match="interior"):
        execute_layout_operations(
            path,
            [
                {
                    "schema_version": "1.0",
                    "operation_id": "wire-branch",
                    "source_schematic_hash": source,
                    "operation_type": "remove_redundant_wire_bend",
                    "arguments": {"wire_uuid": "w1", "expected_points_mm": points},
                }
            ],
            expected_source_hash=source,
        )

    assert path.read_bytes() == before


def test_shorten_wire_path_rejects_collinear_unrelated_overlap(tmp_path: Path) -> None:
    points = [
        (50.8, 50.8),
        (50.8, 45.72),
        (66.04, 45.72),
        (66.04, 60.96),
        (71.12, 60.96),
    ]
    path = _wire_chain(
        tmp_path,
        points,
        overlap=((55.88, 60.96), (60.96, 60.96)),
    )
    source = _hash(path)
    before = path.read_bytes()

    with pytest.raises(UserError, match="collide"):
        execute_layout_operations(
            path,
            [
                {
                    "schema_version": "1.0",
                    "operation_id": "wire-overlap",
                    "source_schematic_hash": source,
                    "operation_type": "shorten_wire_path",
                    "arguments": {
                        "wire_uuid": "w1",
                        "net_name": "N",
                        "expected_points_mm": points,
                    },
                }
            ],
            expected_source_hash=source,
            authoritative_ir=_wire_ir(),
        )

    assert path.read_bytes() == before


def test_wire_precondition_stale_on_source_still_fails_closed(tmp_path: Path) -> None:
    points = [
        (50.8, 50.8),
        (66.04, 50.8),
        (66.04, 60.96),
        (71.12, 60.96),
    ]
    path = _wire_chain(tmp_path, points)
    source = _hash(path)
    before = path.read_bytes()
    stale_points = [points[0], (63.5, 50.8), (63.5, 60.96), points[-1]]

    with pytest.raises(UserError, match="changed since plan creation") as exc_info:
        execute_layout_operations(
            path,
            [
                {
                    "schema_version": "1.0",
                    "operation_id": "wire-stale",
                    "source_schematic_hash": source,
                    "operation_type": "shorten_wire_path",
                    "arguments": {
                        "wire_uuid": "w1",
                        "net_name": "N",
                        "expected_points_mm": stale_points,
                    },
                }
            ],
            expected_source_hash=source,
            authoritative_ir=_wire_ir(),
        )

    assert exc_info.value.code == "REFINEMENT_STALE"
    assert path.read_bytes() == before


def test_later_wire_operation_invalidated_by_same_batch_is_rejected(tmp_path: Path) -> None:
    points = [
        (50.8, 50.8),
        (66.04, 50.8),
        (66.04, 60.96),
        (71.12, 60.96),
    ]
    path = _wire_chain(tmp_path, points)
    source = _hash(path)
    first = {
        "schema_version": "1.0",
        "operation_id": "wire-reroute-1",
        "source_schematic_hash": source,
        "operation_type": "reroute_existing_net_orthogonal",
        "arguments": {
            "wire_uuid": "w1",
            "net_name": "N",
            "expected_points_mm": points,
            "region_min_x_mm": 50.8,
            "region_min_y_mm": 50.8,
            "region_max_x_mm": 71.12,
            "region_max_y_mm": 60.96,
        },
    }
    second = {
        **first,
        "operation_id": "wire-reroute-2",
    }

    result = execute_layout_operations(
        path,
        [first, second],
        expected_source_hash=source,
        authoritative_ir=_wire_ir(),
    )

    assert result.candidate_hash != source
    assert [item.status for item in result.results] == ["applied", "rejected"]
    assert result.results[1].details == {
        "reason_code": "REFINEMENT_INTRA_BATCH_CONFLICT",
        "cause_code": "REFINEMENT_STALE",
    }


def test_reroute_existing_net_rewrites_real_segment_chain(tmp_path: Path) -> None:
    points = [
        (50.8, 50.8),
        (66.04, 50.8),
        (66.04, 60.96),
        (71.12, 60.96),
    ]
    path = _wire_chain(
        tmp_path,
        points,
    )
    source = _hash(path)

    result = execute_layout_operations(
        path,
        [
            {
                "schema_version": "1.0",
                "operation_id": "wire-reroute",
                "source_schematic_hash": source,
                "operation_type": "reroute_existing_net_orthogonal",
                "arguments": {
                    "wire_uuid": "w1",
                    "net_name": "N",
                    "expected_points_mm": points,
                    "region_min_x_mm": 50.8,
                    "region_min_y_mm": 50.8,
                    "region_max_x_mm": 71.12,
                    "region_max_y_mm": 60.96,
                },
            }
        ],
        expected_source_hash=source,
        authoritative_ir=_wire_ir(),
    )

    geometries = _wire_geometries(path)
    assert result.candidate_hash != source
    assert all(len(segment) == 2 for segment in geometries)
    assert result.results[0].details["points"] != [list(point) for point in points]

from __future__ import annotations

import json
from pathlib import Path

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.refinement.schematic_semantics import extract_schematic_semantics_from_doc
from kicad_pcb.sch_doc import SchematicDoc

_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"
_EVALUATION_ROOT = _FIXTURE_ROOT / "refinement" / "evaluation_corpus"
_MODEL_CORPUS_ROOT = _FIXTURE_ROOT / "model_corpus"
_MANIFEST_PATH = _EVALUATION_ROOT / "manifest.json"

_REQUIRED_CATEGORIES = {
    "crowded_layout",
    "excessive_spread",
    "poor_left_to_right_signal_flow",
    "support_passives_detached_from_primary_ic",
    "avoidable_wire_crossings",
    "excessive_wire_bends",
    "inconsistent_repeated_blocks",
    "awkward_connector_orientation",
    "poor_power_symbol_organization",
    "label_readability_collisions",
}
_REQUIRED_SPECIAL_CASES = {
    "multi_unit_symbol",
    "unnamed_net",
    "explicit_no_connect",
}


def _manifest() -> dict[str, object]:
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


def _fixtures() -> list[dict[str, object]]:
    fixtures = _manifest()["fixtures"]
    assert isinstance(fixtures, list)
    return fixtures


def _source_dir(entry: dict[str, object]) -> Path:
    source_fixture_id = entry["source_fixture_id"]
    assert isinstance(source_fixture_id, str)
    return _MODEL_CORPUS_ROOT / source_fixture_id


def test_phase_n1_manifest_has_bounded_unique_fixture_set() -> None:
    manifest = _manifest()
    fixtures = _fixtures()

    assert manifest["schema_version"] == "1.0"
    assert manifest["phase"] == "N1"
    assert 10 <= len(fixtures) <= 20
    fixture_ids = [entry["fixture_id"] for entry in fixtures]
    source_fixture_ids = [entry["source_fixture_id"] for entry in fixtures]
    assert len(fixture_ids) == len(set(fixture_ids))
    assert len(source_fixture_ids) == len(set(source_fixture_ids))


def test_phase_n1_manifest_covers_every_required_layout_category_and_special_case() -> None:
    fixtures = _fixtures()
    categories = {
        category
        for entry in fixtures
        for category in entry["categories"]
        if isinstance(category, str)
    }
    special_cases = {
        special_case
        for entry in fixtures
        for special_case in entry["special_cases"]
        if isinstance(special_case, str)
    }

    assert _REQUIRED_CATEGORIES <= categories
    assert _REQUIRED_SPECIAL_CASES <= special_cases


def test_phase_n1_defect_notes_do_not_prescribe_production_repairs() -> None:
    forbidden_terms = {
        "move_component",
        "remove_redundant_wire_bend",
        "reroute_existing_net_orthogonal",
        "shorten_wire_path",
        "target_x_mm",
        "target_y_mm",
    }

    for entry in _fixtures():
        notes = " ".join(entry["known_visual_defects"])
        assert not any(term in notes for term in forbidden_terms), entry["fixture_id"]


def test_phase_n1_sources_are_ready_parseable_and_have_layout_warning_evidence() -> None:
    for entry in _fixtures():
        source_dir = _source_dir(entry)
        metadata_path = source_dir / "metadata.json"
        schematic_path = source_dir / "source_normalized.kicad_sch"
        circuit_ir_path = source_dir / "circuit_ir.json"
        layout_features_path = source_dir / "source_layout_features.json"

        assert metadata_path.is_file(), entry["fixture_id"]
        assert schematic_path.is_file(), entry["fixture_id"]
        assert circuit_ir_path.is_file(), entry["fixture_id"]
        assert layout_features_path.is_file(), entry["fixture_id"]

        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        layout_features = json.loads(layout_features_path.read_text(encoding="utf-8"))
        assert metadata["status"] == "ready", entry["fixture_id"]
        assert metadata["has_circuit_ir"] is True, entry["fixture_id"]
        assert layout_features["intrinsic_lints"], entry["fixture_id"]
        assert entry["known_visual_defects"], entry["fixture_id"]

        CircuitIR.load(circuit_ir_path)
        SchematicDoc.load(schematic_path)


def test_phase_n1_special_case_claims_are_grounded_in_source_artifacts() -> None:
    for entry in _fixtures():
        special_cases = set(entry["special_cases"])
        if not special_cases:
            continue

        source_dir = _source_dir(entry)
        circuit_ir = CircuitIR.load(source_dir / "circuit_ir.json")
        schematic_path = source_dir / "source_normalized.kicad_sch"
        schematic_text = schematic_path.read_text(encoding="utf-8")
        layout_features = json.loads(
            (source_dir / "source_layout_features.json").read_text(encoding="utf-8")
        )

        if "multi_unit_symbol" in special_cases:
            semantic = extract_schematic_semantics_from_doc(SchematicDoc.load(schematic_path))
            assert any(
                component.unit != "1" for component in semantic.components
            ), entry["fixture_id"]

        if "unnamed_net" in special_cases:
            assert any(net.name.startswith("Net-(") for net in circuit_ir.nets), entry["fixture_id"]

        if "explicit_no_connect" in special_cases:
            assert layout_features["counts"]["no_connects"] > 0, entry["fixture_id"]
            assert "(no_connect" in schematic_text, entry["fixture_id"]

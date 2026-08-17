from __future__ import annotations

import hashlib
import json
from pathlib import Path

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.electrical_equivalence import build_circuit_ir_fingerprint
from kicad_pcb.refinement.metrics import compute_refinement_metrics
from kicad_pcb.refinement.page_geometry import schematic_page_bounds
from kicad_pcb.sch_doc import SchematicDoc

_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"
_EVALUATION_ROOT = _FIXTURE_ROOT / "refinement" / "evaluation_corpus"
_MODEL_CORPUS_ROOT = _FIXTURE_ROOT / "model_corpus"


def _load(name: str) -> dict[str, object]:
    return json.loads((_EVALUATION_ROOT / name).read_text(encoding="utf-8"))


def test_phase_n2_expectations_cover_exactly_the_n1_fixture_set() -> None:
    manifest = _load("manifest.json")
    expectations = _load("baseline_expectations.json")

    assert expectations["schema_version"] == "1.0"
    assert expectations["phase"] == "N2"
    manifest_ids = [entry["fixture_id"] for entry in manifest["fixtures"]]
    expectation_ids = [entry["fixture_id"] for entry in expectations["fixtures"]]
    assert expectation_ids == manifest_ids


def test_phase_n2_deterministic_baselines_match_source_artifacts() -> None:
    expectations = _load("baseline_expectations.json")

    for entry in expectations["fixtures"]:
        source_dir = _MODEL_CORPUS_ROOT / entry["source_fixture_id"]
        schematic = source_dir / "source_normalized.kicad_sch"
        authoritative = CircuitIR.load(source_dir / "circuit_ir.json")
        page = schematic_page_bounds(SchematicDoc.load(schematic))

        assert (
            hashlib.sha256(schematic.read_bytes()).hexdigest() == entry["source_schematic_sha256"]
        ), entry["fixture_id"]
        assert (
            build_circuit_ir_fingerprint(authoritative).sha256()
            == entry["authoritative_fingerprint_sha256"]
        ), entry["fixture_id"]
        assert compute_refinement_metrics(schematic).to_dict() == entry["metrics"], entry[
            "fixture_id"
        ]
        assert [page.width_mm, page.height_mm] == [
            entry["page_mm"]["width"],
            entry["page_mm"]["height"],
        ], entry["fixture_id"]

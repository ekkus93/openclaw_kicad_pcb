from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.commands.model_corpus import cmd_model_corpus_evaluate
from kicad_pcb.corpus.kicadxml import kicadxml_to_circuit_ir, parse_kicadxml_netlist
from kicad_pcb.corpus.metadata import CorpusFixtureMetadata, write_fixture_metadata
from kicad_pcb.runner import find_kicad_cli
from tests.conftest import requires_kicad

SOURCE_SCHEMATIC = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "readability"
    / "ne5532_headphone_amp_left_current"
    / "baseline_generated.kicad_sch"
)


@requires_kicad
def test_model_corpus_evaluate_runs_electrical_equivalence_with_kicad(home_tmp: Path) -> None:
    corpus_dir = home_tmp / "corpus"
    fixture_dir = corpus_dir / "fixture-1"
    fixture_dir.mkdir(parents=True)
    source_xml = _create_roundtrippable_source_xml(home_tmp)
    parsed = parse_kicadxml_netlist(source_xml)
    circuit_ir = kicadxml_to_circuit_ir(parsed)

    write_fixture_metadata(
        CorpusFixtureMetadata(
            fixture_id="fixture-1",
            source_file_name="fixture-1.kicad_sch",
            source_path="fixture-1.kicad_sch",
            status="ready",
            status_reasons=[],
            has_circuit_ir=True,
        ),
        fixture_dir / "metadata.json",
    )
    (fixture_dir / "source_layout_features.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "source": {"fixture_id": "fixture-1", "file": "fixture-1.kicad_sch"},
                "counts": {
                    "symbols": 1,
                    "non_power_symbols": 1,
                    "power_symbols": 0,
                    "wires": 1,
                    "labels": 0,
                    "global_labels": 0,
                    "junctions": 0,
                    "no_connects": 0,
                },
                "symbols": {},
                "role_counts": {"passive": 1},
                "net_label_strategy": {
                    "local_label_count": 0,
                    "global_label_count": 0,
                    "power_symbol_count": 0,
                },
                "geometry": {
                    "min_x": 0.0,
                    "max_x": 100.0,
                    "min_y": 0.0,
                    "max_y": 100.0,
                    "distinct_x_columns": 1,
                    "average_symbol_spacing_mm": 10.0,
                    "wire_stub_ratio": 0.0,
                },
                "relative_positions": [],
                "intrinsic_lints": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (fixture_dir / "source_netlist.kicadxml").write_text(
        source_xml.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (fixture_dir / "circuit_ir.json").write_text(circuit_ir.dumps(), encoding="utf-8")

    cmd_model_corpus_evaluate(
        SimpleNamespace(
            corpus_dir=corpus_dir,
            out_dir=home_tmp / "eval",
            fixture=None,
            require_kicad=True,
            heuristic_profile=None,
            label_mode=None,
        )
    )

    payload = json.loads(
        (home_tmp / "eval" / "fixture-1" / "evaluation_report.json").read_text(encoding="utf-8")
    )
    assert payload["electrical_equivalence"]["status"] == "failed"
    assert payload["electrical_equivalence"]["mismatches"]
    assert (home_tmp / "eval" / "fixture-1" / "generated_netlist.kicadxml").exists()

def _create_roundtrippable_source_xml(home_tmp: Path) -> Path:
    adapter = KicadCliAdapter(kicad_cli=find_kicad_cli())
    xml_path = home_tmp / "fixture-source.kicadxml"
    export_result, _ = adapter.export_netlist(SOURCE_SCHEMATIC, xml_path)
    assert export_result.ok
    return xml_path

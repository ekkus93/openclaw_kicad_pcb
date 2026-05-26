from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kicad_pcb import config as config_mod
from kicad_pcb.commands.model_corpus import cmd_model_corpus_evaluate
from kicad_pcb.corpus.kicadxml import kicadxml_to_circuit_ir, parse_kicadxml_netlist
from kicad_pcb.corpus.metadata import CorpusFixtureMetadata, write_fixture_metadata
from kicad_pcb.errors import UserError


def test_model_corpus_evaluate_generates_partial_reports_without_repo_kicad(tmp_path: Path) -> None:
    fixture_dir = _write_fixture(tmp_path)

    result = cmd_model_corpus_evaluate(
        SimpleNamespace(
            corpus_dir=fixture_dir.parent,
            out_dir=tmp_path / "eval",
            fixture=None,
            require_kicad=False,
            heuristic_profile=None,
            label_mode=None,
        )
    )

    assert result.evaluated_count == 1
    assert result.summary_json_path.exists()
    report_path = tmp_path / "eval" / fixture_dir.name / "evaluation_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["fixture_id"] == fixture_dir.name
    assert report["generated_artifacts"]["schematic_path"].endswith("generated.kicad_sch")
    assert report["result"] in {"partial", "pass_with_warnings", "pass"}


def test_model_corpus_evaluate_rejects_selected_fixture_without_circuit_ir(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "corpus" / "fixture-1"
    fixture_dir.mkdir(parents=True)
    write_fixture_metadata(
        CorpusFixtureMetadata(
            fixture_id="fixture-1",
            source_file_name="fixture-1.kicad_sch",
            source_path="fixture-1.kicad_sch",
            status="pending_netlist_export",
            status_reasons=["missing"],
        ),
        fixture_dir / "metadata.json",
    )

    with pytest.raises(UserError):
        cmd_model_corpus_evaluate(
            SimpleNamespace(
                corpus_dir=fixture_dir.parent,
                out_dir=tmp_path / "eval",
                fixture="fixture-1",
                require_kicad=False,
                heuristic_profile=None,
                label_mode=None,
            )
        )


def test_model_corpus_evaluate_skips_fixtures_without_circuit_ir_in_all_fixtures_mode(
    tmp_path: Path,
) -> None:
    ready_fixture = _write_fixture(tmp_path)
    skipped_fixture = tmp_path / "corpus" / "fixture-2"
    skipped_fixture.mkdir(parents=True)
    write_fixture_metadata(
        CorpusFixtureMetadata(
            fixture_id="fixture-2",
            source_file_name="fixture-2.kicad_sch",
            source_path="fixture-2.kicad_sch",
            status="pending_netlist_export",
            status_reasons=["missing"],
        ),
        skipped_fixture / "metadata.json",
    )

    result = cmd_model_corpus_evaluate(
        SimpleNamespace(
            corpus_dir=ready_fixture.parent,
            out_dir=tmp_path / "eval",
            fixture=None,
            require_kicad=False,
            heuristic_profile=None,
            label_mode=None,
        )
    )

    assert result.evaluated_count == 1
    assert result.skipped_count == 1


def test_model_corpus_evaluate_does_not_write_current_project_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_dir = _write_fixture(tmp_path)
    current_project_file = tmp_path / "current_project.json"
    monkeypatch.setattr(config_mod, "CURRENT_PROJECT_FILE", current_project_file)

    cmd_model_corpus_evaluate(
        SimpleNamespace(
            corpus_dir=fixture_dir.parent,
            out_dir=tmp_path / "eval",
            fixture=None,
            require_kicad=False,
            heuristic_profile=None,
            label_mode=None,
        )
    )

    assert not current_project_file.exists()


def _write_fixture(tmp_path: Path) -> Path:
    fixture_dir = tmp_path / "corpus" / "fixture-1"
    fixture_dir.mkdir(parents=True)
    netlist_path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "model_corpus_xml"
        / "minimal_netlist.xml"
    )
    parsed = parse_kicadxml_netlist(netlist_path)
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
                    "symbols": 3,
                    "non_power_symbols": 2,
                    "power_symbols": 1,
                    "wires": 2,
                    "labels": 0,
                    "global_labels": 0,
                    "junctions": 0,
                    "no_connects": 0,
                },
                "symbols": {},
                "role_counts": {"passive": 1, "major_ic": 1},
                "net_label_strategy": {
                    "local_label_count": 0,
                    "global_label_count": 0,
                    "power_symbol_count": 1,
                },
                "geometry": {
                    "min_x": 0.0,
                    "max_x": 100.0,
                    "min_y": 0.0,
                    "max_y": 100.0,
                    "distinct_x_columns": 2,
                    "average_symbol_spacing_mm": 20.0,
                    "wire_stub_ratio": 0.1,
                },
                "relative_positions": [],
                "intrinsic_lints": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (fixture_dir / "source_netlist.kicadxml").write_text(
        netlist_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (fixture_dir / "circuit_ir.json").write_text(circuit_ir.dumps(), encoding="utf-8")
    return fixture_dir

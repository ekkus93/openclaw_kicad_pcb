from __future__ import annotations

import shutil
from pathlib import Path

from kicad_pcb.corpus.ingestion import ingest_model_corpus
from tests.conftest import requires_kicad

SOURCE_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "readability"
    / "ne5532_headphone_amp_left_current"
    / "baseline_generated.kicad_sch"
)


@requires_kicad
def test_model_corpus_ingest_exports_kicad_netlist_and_ir(home_tmp: Path) -> None:
    source_dir = home_tmp / "model_kicad_files"
    out_dir = home_tmp / "model_corpus"
    source_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE_FIXTURE, source_dir / SOURCE_FIXTURE.name)

    summary = ingest_model_corpus(
        source_dir=source_dir,
        out_dir=out_dir,
        refresh=True,
        require_kicad=True,
    )

    assert summary.accepted_count == 1
    fixture_dir = out_dir / "baseline-generated"
    assert (fixture_dir / "source_netlist.kicadxml").exists()
    assert (fixture_dir / "circuit_ir.json").exists()

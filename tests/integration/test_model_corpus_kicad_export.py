from __future__ import annotations

import shutil
from pathlib import Path

from kicad_pcb.corpus.ingestion import ingest_model_corpus
from tests.conftest import requires_kicad

MODEL_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "model_kicad_files"
    / "mcp2551-can-transciever.kicad_sch"
)


@requires_kicad
def test_model_corpus_ingest_exports_kicad_netlist_and_ir(home_tmp: Path) -> None:
    source_dir = home_tmp / "model_kicad_files"
    out_dir = home_tmp / "model_corpus"
    source_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(MODEL_FIXTURE, source_dir / MODEL_FIXTURE.name)

    summary = ingest_model_corpus(
        source_dir=source_dir,
        out_dir=out_dir,
        refresh=True,
        require_kicad=True,
    )

    assert summary.accepted_count == 1
    fixture_dir = out_dir / "mcp2551-can-transciever"
    assert (fixture_dir / "source_netlist.kicadxml").exists()
    assert (fixture_dir / "circuit_ir.json").exists()

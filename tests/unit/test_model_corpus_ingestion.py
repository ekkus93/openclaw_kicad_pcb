from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from kicad_pcb.adapters import FakeRunner, KicadCliAdapter, RunResult
from kicad_pcb.commands.model_corpus import cmd_model_corpus_ingest, cmd_model_corpus_list
from kicad_pcb.corpus.ingestion import ingest_model_corpus, list_model_corpus
from kicad_pcb.corpus.metadata import CorpusFixtureMetadata
from kicad_pcb.errors import ToolError, UserError

MODEL_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "model_kicad_files"
    / "mcp2551-can-transciever.kicad_sch"
)


def test_ingest_real_model_file_without_kicad_cli(tmp_path: Path) -> None:
    source_dir = tmp_path / "model_kicad_files"
    source_dir.mkdir()
    fixture_copy = source_dir / MODEL_FIXTURE.name
    shutil.copyfile(MODEL_FIXTURE, fixture_copy)
    original_content = fixture_copy.read_text(encoding="utf-8")

    adapter = KicadCliAdapter(
        runner=FakeRunner({"--version": RunResult(returncode=1, stdout="", stderr="missing")})
    )
    summary = ingest_model_corpus(
        source_dir=source_dir,
        out_dir=tmp_path / "corpus",
        adapter=adapter,
    )

    assert summary.accepted_count == 0
    assert summary.partial_count == 1
    metadata_path = tmp_path / "corpus" / "mcp2551-can-transciever" / "metadata.json"
    metadata = CorpusFixtureMetadata.model_validate_json(metadata_path.read_text(encoding="utf-8"))
    assert metadata.status == "pending_netlist_export"
    assert metadata.has_circuit_ir is False
    assert fixture_copy.read_text(encoding="utf-8") == original_content


def test_ingest_refresh_preserves_hand_edited_metadata(tmp_path: Path) -> None:
    source_dir = tmp_path / "model_kicad_files"
    source_dir.mkdir()
    shutil.copyfile(MODEL_FIXTURE, source_dir / MODEL_FIXTURE.name)
    adapter = KicadCliAdapter(
        runner=FakeRunner({"--version": RunResult(returncode=1, stdout="", stderr="missing")})
    )
    out_dir = tmp_path / "corpus"

    ingest_model_corpus(source_dir=source_dir, out_dir=out_dir, adapter=adapter)
    metadata_path = out_dir / "mcp2551-can-transciever" / "metadata.json"
    metadata = CorpusFixtureMetadata.model_validate_json(metadata_path.read_text(encoding="utf-8"))
    edited = metadata.model_copy(update={"license": "CERN-OHL-S-2.0", "notes": ["reviewed"]})
    metadata_path.write_text(edited.model_dump_json(indent=2), encoding="utf-8")

    ingest_model_corpus(source_dir=source_dir, out_dir=out_dir, refresh=True, adapter=adapter)
    refreshed = CorpusFixtureMetadata.model_validate_json(metadata_path.read_text(encoding="utf-8"))
    assert refreshed.license == "CERN-OHL-S-2.0"
    assert refreshed.notes == ["reviewed"]


def test_ingest_without_refresh_rejects_existing_fixture(tmp_path: Path) -> None:
    source_dir = tmp_path / "model_kicad_files"
    source_dir.mkdir()
    shutil.copyfile(MODEL_FIXTURE, source_dir / MODEL_FIXTURE.name)
    adapter = KicadCliAdapter(
        runner=FakeRunner({"--version": RunResult(returncode=1, stdout="", stderr="missing")})
    )
    out_dir = tmp_path / "corpus"
    ingest_model_corpus(source_dir=source_dir, out_dir=out_dir, adapter=adapter)

    with pytest.raises(UserError):
        ingest_model_corpus(source_dir=source_dir, out_dir=out_dir, adapter=adapter)


def test_ingest_require_kicad_raises_when_cli_missing(tmp_path: Path) -> None:
    source_dir = tmp_path / "model_kicad_files"
    source_dir.mkdir()
    shutil.copyfile(MODEL_FIXTURE, source_dir / MODEL_FIXTURE.name)
    adapter = KicadCliAdapter(
        runner=FakeRunner({"--version": RunResult(returncode=1, stdout="", stderr="missing")})
    )

    with pytest.raises(ToolError):
        ingest_model_corpus(
            source_dir=source_dir,
            out_dir=tmp_path / "corpus",
            require_kicad=True,
            adapter=adapter,
        )


def test_list_command_reads_fixture_metadata(tmp_path: Path) -> None:
    source_dir = tmp_path / "model_kicad_files"
    source_dir.mkdir()
    shutil.copyfile(MODEL_FIXTURE, source_dir / MODEL_FIXTURE.name)
    adapter = KicadCliAdapter(
        runner=FakeRunner({"--version": RunResult(returncode=1, stdout="", stderr="missing")})
    )
    out_dir = tmp_path / "corpus"
    ingest_model_corpus(source_dir=source_dir, out_dir=out_dir, adapter=adapter)

    summaries = list_model_corpus(corpus_dir=out_dir)
    assert summaries[0].fixture_id == "mcp2551-can-transciever"

    ingest_args = SimpleNamespace(
        source_dir=source_dir,
        out_dir=out_dir,
        refresh=True,
        require_kicad=False,
    )
    list_args = SimpleNamespace(corpus_dir=out_dir)

    ingest_result = cmd_model_corpus_ingest(ingest_args)
    list_result = cmd_model_corpus_list(list_args)
    assert ingest_result.fixture_count == 1
    assert ingest_result.accepted_count + ingest_result.partial_count == 1
    assert list_result.fixtures[0].fixture_id == "mcp2551-can-transciever"

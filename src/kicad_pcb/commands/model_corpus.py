"""CLI command handlers for model-corpus ingestion and listing."""

from __future__ import annotations

from pathlib import Path

from kicad_pcb.corpus.ingestion import ingest_model_corpus, list_model_corpus
from kicad_pcb.results import (
    ModelCorpusFixtureResult,
    ModelCorpusIngestResult,
    ModelCorpusListResult,
)


def cmd_model_corpus_ingest(args) -> ModelCorpusIngestResult:
    """Ingest raw source schematics into deterministic corpus fixtures."""

    summary = ingest_model_corpus(
        source_dir=Path(args.source_dir),
        out_dir=Path(args.out_dir),
        refresh=bool(getattr(args, "refresh", False)),
        require_kicad=bool(getattr(args, "require_kicad", False)),
    )
    fixtures = tuple(
        ModelCorpusFixtureResult(
            fixture_id=item.fixture_id,
            status=item.status,
            source_file_name=item.source_file_name,
            symbol_count=item.symbol_count,
            wire_count=item.wire_count,
            label_count=item.label_count,
            has_circuit_ir=item.has_circuit_ir,
        )
        for item in summary.fixture_summaries
    )
    return ModelCorpusIngestResult(
        source_dir=Path(args.source_dir),
        out_dir=Path(args.out_dir),
        accepted_count=summary.accepted_count,
        partial_count=summary.partial_count,
        rejected_count=summary.rejected_count,
        fixture_count=len(fixtures),
        report_path=summary.report_path,
        summary_path=summary.summary_path,
        fixtures=fixtures,
    )


def cmd_model_corpus_list(args) -> ModelCorpusListResult:
    """List existing corpus fixtures from metadata files."""

    fixtures = tuple(
        ModelCorpusFixtureResult(
            fixture_id=item.fixture_id,
            status=item.status,
            source_file_name=item.source_file_name,
            symbol_count=item.symbol_count,
            wire_count=item.wire_count,
            label_count=item.label_count,
            has_circuit_ir=item.has_circuit_ir,
        )
        for item in list_model_corpus(corpus_dir=Path(args.corpus_dir))
    )
    return ModelCorpusListResult(
        corpus_dir=Path(args.corpus_dir),
        fixtures=fixtures,
    )

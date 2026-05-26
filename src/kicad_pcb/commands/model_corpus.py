"""CLI command handlers for model-corpus ingestion and evaluation."""

from __future__ import annotations

from pathlib import Path

from kicad_pcb.corpus.ingestion import ingest_model_corpus, list_model_corpus
from kicad_pcb.evaluation.reports import EvaluationOptions, evaluate_model_corpus
from kicad_pcb.results import (
    ModelCorpusEvaluateResult,
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


def cmd_model_corpus_evaluate(args) -> ModelCorpusEvaluateResult:
    """Evaluate ingested corpus fixtures into generated-project reports."""

    summary = evaluate_model_corpus(
        corpus_dir=Path(args.corpus_dir),
        out_dir=Path(args.out_dir),
        fixture_id=getattr(args, "fixture", None),
        options=EvaluationOptions(
            require_kicad=bool(getattr(args, "require_kicad", False)),
            heuristic_profile=getattr(args, "heuristic_profile", None),
            label_mode=getattr(args, "label_mode", None),
        ),
    )
    return ModelCorpusEvaluateResult(
        corpus_dir=Path(args.corpus_dir),
        out_dir=Path(args.out_dir),
        fixture_count=len(summary.fixtures),
        evaluated_count=summary.evaluated_count,
        skipped_count=summary.skipped_count,
        failed_count=summary.failed_count,
        summary_json_path=summary.summary_json_path,
        summary_md_path=summary.summary_md_path,
    )

"""Model-corpus ingestion and listing workflow."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.compat import KiCadVersion
from kicad_pcb.errors import ErrorCode, ParseError, ToolError, UserError
from kicad_pcb.runner import find_kicad_cli
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.nodes import ListNode, StringNode
from kicad_pcb.symbol_index import resolve_symbol_dirs

from .embedded_symbols import extract_embedded_symbol_defs, write_embedded_symbol_library
from .kicadxml import (
    canonicalize_circuit_ir,
    kicadxml_to_circuit_ir,
    parse_kicadxml_netlist,
    schematic_symbols_by_ref,
)
from .layout_features import extract_layout_features, write_layout_features
from .metadata import (
    CorpusFixtureMetadata,
    detect_fixture_id_collisions,
    make_fixture_id,
    merge_preserved_metadata,
    write_fixture_metadata,
)
from .normalization import normalize_for_kicad_export, serialize_normalized_schematic
from .reports import write_json_report, write_markdown_report

MINIMUM_REPO_KICAD_VERSION = KiCadVersion(9, 0, 0)


@dataclass(frozen=True)
class IngestionReportEntry:
    source_path: str
    status: str
    reason: str | None
    message: str
    fixture_id: str | None


@dataclass(frozen=True)
class CorpusFixtureSummary:
    fixture_id: str
    status: str
    source_file_name: str
    symbol_count: int
    wire_count: int
    label_count: int
    has_circuit_ir: bool


@dataclass(frozen=True)
class IngestionSummary:
    accepted_count: int
    partial_count: int
    rejected_count: int
    entries: tuple[IngestionReportEntry, ...]
    fixture_summaries: tuple[CorpusFixtureSummary, ...]
    report_path: Path
    summary_path: Path


def ingest_model_corpus(
    *,
    source_dir: Path,
    out_dir: Path,
    refresh: bool = False,
    require_kicad: bool = False,
    adapter: KicadCliAdapter | None = None,
) -> IngestionSummary:
    """Ingest raw KiCad schematics from *source_dir* into fixture dirs under *out_dir*."""

    source_dir = source_dir.resolve()
    out_dir = out_dir.resolve()
    adapter = adapter or KicadCliAdapter(kicad_cli=find_kicad_cli())

    source_files = sorted(source_dir.rglob("*.kicad_sch"))
    collisions = detect_fixture_id_collisions(source_files, relative_to=source_dir)
    fixture_id_by_path = {
        path: make_fixture_id(
            path,
            relative_to=source_dir,
            colliding_paths=collisions.get(_base_fixture_slug(path), set()),
        )
        for path in source_files
    }

    entries: list[IngestionReportEntry] = []
    fixture_summaries: list[CorpusFixtureSummary] = []
    accepted_count = 0
    partial_count = 0
    rejected_count = 0

    out_dir.mkdir(parents=True, exist_ok=True)

    for path in source_files:
        relative_source = path.relative_to(source_dir)
        fixture_id = fixture_id_by_path[path]
        fixture_dir = out_dir / fixture_id
        if fixture_dir.exists() and not refresh:
            raise UserError(
                f"Fixture already exists and --refresh was not set: {fixture_dir}",
                code=ErrorCode.USER_ERROR,
                details={"fixture_id": fixture_id, "fixture_dir": str(fixture_dir)},
            )

        try:
            doc = SchematicDoc.load(path)
        except ParseError as exc:
            rejected_count += 1
            entries.append(
                IngestionReportEntry(
                    source_path=str(relative_source),
                    status="rejected",
                    reason="parse_error",
                    message=str(exc),
                    fixture_id=None,
                )
            )
            continue

        fixture_dir.mkdir(parents=True, exist_ok=True)
        source_copy = fixture_dir / "source.kicad_sch"
        shutil.copyfile(path, source_copy)

        layout_features = extract_layout_features(
            doc,
            fixture_id=fixture_id,
            source_file=source_copy.name,
        )
        write_layout_features(layout_features, fixture_dir / "source_layout_features.json")

        embedded_symbols = extract_embedded_symbol_defs(doc)
        if embedded_symbols:
            write_embedded_symbol_library(
                embedded_symbols,
                fixture_dir / "source_embedded_symbols.sexpr",
            )

        metadata = _build_metadata(
            doc=doc,
            fixture_id=fixture_id,
            relative_source=relative_source,
            layout_features=layout_features,
            has_embedded_symbols=bool(embedded_symbols),
        )
        existing_metadata = _load_existing_metadata(fixture_dir / "metadata.json")

        kicad_state = _ingest_normalized_kicad_artifacts(
            doc=doc,
            fixture_id=fixture_id,
            fixture_dir=fixture_dir,
            require_kicad=require_kicad,
            adapter=adapter,
        )
        metadata = metadata.model_copy(
            update={
                "status": kicad_state["status"],
                "status_reasons": (
                    list(kicad_state["status_reasons"])
                    if isinstance(kicad_state["status_reasons"], list)
                    else []
                ),
                "has_circuit_ir": bool(kicad_state["has_circuit_ir"]),
            }
        )
        metadata = merge_preserved_metadata(existing=existing_metadata, generated=metadata)
        write_fixture_metadata(metadata, fixture_dir / "metadata.json")

        if metadata.status == "ready":
            accepted_count += 1
            entry_status = "accepted"
        else:
            partial_count += 1
            entry_status = "partial"

        entries.append(
            IngestionReportEntry(
                source_path=str(relative_source),
                status=entry_status,
                reason=metadata.status,
                message="Fixture ingested successfully.",
                fixture_id=fixture_id,
            )
        )
        fixture_summaries.append(
            CorpusFixtureSummary(
                fixture_id=fixture_id,
                status=metadata.status,
                source_file_name=metadata.source_file_name,
                symbol_count=metadata.symbol_count,
                wire_count=metadata.wire_count,
                label_count=metadata.label_count,
                has_circuit_ir=metadata.has_circuit_ir,
            )
        )

    report_path = out_dir / "ingestion_report.json"
    summary_path = out_dir / "summary.md"
    write_json_report(
        report_path,
        {
            "accepted_count": accepted_count,
            "partial_count": partial_count,
            "rejected_count": rejected_count,
            "entries": entries,
        },
    )
    write_markdown_report(
        summary_path,
        _render_ingestion_summary(
            entries=entries,
            accepted_count=accepted_count,
            partial_count=partial_count,
            rejected_count=rejected_count,
        ),
    )
    return IngestionSummary(
        accepted_count=accepted_count,
        partial_count=partial_count,
        rejected_count=rejected_count,
        entries=tuple(entries),
        fixture_summaries=tuple(sorted(fixture_summaries, key=lambda item: item.fixture_id)),
        report_path=report_path,
        summary_path=summary_path,
    )


def list_model_corpus(*, corpus_dir: Path) -> tuple[CorpusFixtureSummary, ...]:
    """Return fixture summaries from existing corpus metadata files."""

    summaries: list[CorpusFixtureSummary] = []
    for metadata_path in sorted(corpus_dir.glob("*/metadata.json")):
        metadata = CorpusFixtureMetadata.model_validate_json(
            metadata_path.read_text(encoding="utf-8")
        )
        summaries.append(
            CorpusFixtureSummary(
                fixture_id=metadata.fixture_id,
                status=metadata.status,
                source_file_name=metadata.source_file_name,
                symbol_count=metadata.symbol_count,
                wire_count=metadata.wire_count,
                label_count=metadata.label_count,
                has_circuit_ir=metadata.has_circuit_ir,
            )
        )
    return tuple(summaries)


def _build_metadata(
    *,
    doc: SchematicDoc,
    fixture_id: str,
    relative_source: Path,
    layout_features,
    has_embedded_symbols: bool,
) -> CorpusFixtureMetadata:
    return CorpusFixtureMetadata(
        fixture_id=fixture_id,
        source_file_name=relative_source.name,
        source_path=str(Path("model_kicad_files") / relative_source),
        status="layout_only",
        status_reasons=[],
        kicad_schematic_version=_root_scalar(doc, "version"),
        generator=_root_string(doc, "generator"),
        title=_title_block_value(doc, "title"),
        license="unknown",
        source_url=None,
        symbol_count=layout_features.counts["symbols"],
        wire_count=layout_features.counts["wires"],
        label_count=layout_features.counts["labels"],
        global_label_count=layout_features.counts["global_labels"],
        power_symbol_count=layout_features.counts["power_symbols"],
        has_embedded_symbols=has_embedded_symbols,
        has_circuit_ir=False,
        requires_custom_symbols=_requires_custom_symbols(doc),
        notes=[],
        detected_license_hint=_find_comment_hint(doc, prefix="License:"),
        detected_source_url_hint=_find_comment_hint(doc, prefix="Source:"),
        metadata_review_required=True,
    )


def _ingest_normalized_kicad_artifacts(
    *,
    doc: SchematicDoc,
    fixture_id: str,
    fixture_dir: Path,
    require_kicad: bool,
    adapter: KicadCliAdapter,
) -> dict[str, object]:
    normalized = normalize_for_kicad_export(doc, fixture_id=fixture_id)
    normalized_source = fixture_dir / "source_normalized.kicad_sch"
    normalized_source.write_text(
        serialize_normalized_schematic(normalized),
        encoding="utf-8",
    )
    kicad_state = _ingest_optional_kicad_artifacts(
        source_for_export=normalized_source,
        fallback_symbols_by_ref=schematic_symbols_by_ref(normalized.doc),
        fixture_dir=fixture_dir,
        require_kicad=require_kicad,
        adapter=adapter,
    )
    return _append_normalization_status_reasons(
        kicad_state,
        normalized_changes=normalized.changes,
    )


def _append_normalization_status_reasons(
    kicad_state: dict[str, object],
    *,
    normalized_changes: tuple[str, ...],
) -> dict[str, object]:
    if not normalized_changes:
        return kicad_state
    reasons = (
        list(kicad_state["status_reasons"])
        if isinstance(kicad_state["status_reasons"], list)
        else []
    )
    reasons.extend(f"normalized:{change}" for change in normalized_changes)
    return {**kicad_state, "status_reasons": reasons}


def _ingest_optional_kicad_artifacts(
    *,
    source_for_export: Path,
    fallback_symbols_by_ref: dict[str, str],
    fixture_dir: Path,
    require_kicad: bool,
    adapter: KicadCliAdapter,
) -> dict[str, object]:
    netlist_path = fixture_dir / "source_netlist.kicadxml"
    ir_path = fixture_dir / "circuit_ir.json"

    try:
        version_result = adapter.version()
    except ToolError as exc:
        if require_kicad:
            raise
        return {
            "status": "pending_netlist_export",
            "status_reasons": [str(exc)],
            "has_circuit_ir": False,
        }

    if not version_result.ok:
        if require_kicad:
            raise ToolError(
                "kicad-cli is required for model-corpus ingest",
                code=ErrorCode.KICAD_CLI_MISSING,
                details={"stderr": version_result.stderr},
            )
        return {
            "status": "pending_netlist_export",
            "status_reasons": ["kicad-cli unavailable"],
            "has_circuit_ir": False,
        }

    version = adapter.detected_version
    if version is None or version < MINIMUM_REPO_KICAD_VERSION:
        if require_kicad:
            raise ToolError(
                "kicad-cli >= 9.0.0 is required for repo schematic netlist export",
                code=ErrorCode.KICAD_CLI_MISSING,
                details={"detected_version": str(version) if version is not None else None},
            )
        return {
            "status": "pending_netlist_export",
            "status_reasons": ["kicad-cli too old for repo schematic format"],
            "has_circuit_ir": False,
        }

    export_result, xml_content = adapter.export_netlist(source_for_export, netlist_path)
    if not export_result.ok or not xml_content:
        if require_kicad:
            raise ToolError(
                "kicad-cli netlist export failed during model-corpus ingest",
                code=ErrorCode.TOOL_ERROR,
                details={
                    "source": str(source_for_export),
                    "stderr": export_result.stderr,
                    "stdout": export_result.stdout,
                },
            )
        return {
            "status": "layout_only",
            "status_reasons": ["source_netlist_export_failed"],
            "has_circuit_ir": False,
        }

    netlist = parse_kicadxml_netlist(netlist_path)
    circuit_ir = canonicalize_circuit_ir(
        kicadxml_to_circuit_ir(
            netlist,
            fallback_symbols_by_ref=fallback_symbols_by_ref,
        )
    )
    write_json_report(ir_path, circuit_ir.model_dump(mode="json"))
    return {"status": "ready", "status_reasons": [], "has_circuit_ir": True}


def _render_ingestion_summary(
    *,
    entries: list[IngestionReportEntry],
    accepted_count: int,
    partial_count: int,
    rejected_count: int,
) -> str:
    lines = [
        "# Model corpus ingestion summary",
        "",
        f"- accepted: {accepted_count}",
        f"- partial: {partial_count}",
        f"- rejected: {rejected_count}",
        "",
        "## Entries",
        "",
    ]
    for entry in entries:
        fixture_text = entry.fixture_id or "n/a"
        reason_text = f" ({entry.reason})" if entry.reason else ""
        lines.append(
            f"- `{entry.source_path}` -> **{entry.status}**{reason_text}; fixture `{fixture_text}`"
        )
    return "\n".join(lines)


def _load_existing_metadata(path: Path) -> CorpusFixtureMetadata | None:
    if not path.exists():
        return None
    return CorpusFixtureMetadata.model_validate_json(path.read_text(encoding="utf-8"))


def _base_fixture_slug(path: Path) -> str:
    return make_fixture_id(path).split("--", 1)[0]


def _root_scalar(doc: SchematicDoc, key: str) -> str:
    for item in doc.root.items:
        if not isinstance(item, ListNode) or item.key != key or len(item.items) < 2:
            continue
        return getattr(item.items[1], "value", "")
    return ""


def _root_string(doc: SchematicDoc, key: str) -> str:
    for item in doc.root.items:
        if (
            isinstance(item, ListNode)
            and item.key == key
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
        ):
            return item.items[1].value
    return ""


def _title_block_value(doc: SchematicDoc, key: str) -> str:
    title_block = next(
        (
            item
            for item in doc.root.items
            if isinstance(item, ListNode) and item.key == "title_block"
        ),
        None,
    )
    if title_block is None:
        return ""
    for child in title_block.items:
        if (
            isinstance(child, ListNode)
            and child.key == key
            and len(child.items) >= 2
            and isinstance(child.items[1], StringNode)
        ):
            return child.items[1].value
    return ""


def _find_comment_hint(doc: SchematicDoc, *, prefix: str) -> str | None:
    for item in doc.root.items:
        if not isinstance(item, ListNode) or item.key != "comment" or len(item.items) < 3:
            continue
        value_node = item.items[2]
        if isinstance(value_node, StringNode) and value_node.value.startswith(prefix):
            return value_node.value.removeprefix(prefix).strip() or None
    return None


def _requires_custom_symbols(doc: SchematicDoc) -> bool:
    available_libraries = {
        library.stem
        for directory in resolve_symbol_dirs().dirs
        for library in directory.glob("*.kicad_sym")
    }
    for symbol in doc.list_symbols():
        symbol_id = str(symbol.get("symbol_id", ""))
        if ":" not in symbol_id:
            continue
        lib_name = symbol_id.split(":", 1)[0]
        if lib_name not in available_libraries:
            return True
    return False

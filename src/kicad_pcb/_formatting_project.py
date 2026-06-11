"""Formatters for project, session, model-corpus, and netlist results."""

from __future__ import annotations

from ._formatting_core import _register
from .results import (
    ApplyNetlistResult,
    FixNetlistResult,
    InfoResult,
    InfoSchResult,
    ModelCorpusEvaluateResult,
    ModelCorpusIngestResult,
    ModelCorpusListResult,
    NewFromNetlistResult,
    NewProjectResult,
    NewSessionResult,
    OpenResult,
    SessionInfoResult,
    ValidateNetlistResult,
)

# ---------------------------------------------------------------------------
# project
# ---------------------------------------------------------------------------


@_register(NewProjectResult)
def _fmt_new_project(r: NewProjectResult) -> list[str]:
    lines = [f"✅ Created project: {r.name}", f"   Path: {r.path}", "   Files:"]
    for f in r.files:
        lines.append(f"     - {f}")
    if r.description:
        lines.append(f"   Description: {r.description}")
    return lines


@_register(InfoResult)
def _fmt_info(r: InfoResult) -> list[str]:
    p = r.project
    lines = [
        "╭─────────────────────────────────────╮",
        "│      🔧 KICAD PROJECT INFO          │",
        "├─────────────────────────────────────┤",
        f"│  Name: {p.name:<27} │",
        f"│  Path: {str(p.path)[:27]:<27} │",
        "╰─────────────────────────────────────╯",
    ]
    if r.files:
        lines.append("\nFiles:")
        for name, size in r.files:
            lines.append(f"  {name:<30} {size:>8} bytes")
    return lines


@_register(OpenResult)
def _fmt_open(r: OpenResult) -> list[str]:
    return [f"✅ Opened project: {r.name}", f"   Path: {r.path}"]


@_register(ModelCorpusIngestResult)
def _fmt_model_corpus_ingest(r: ModelCorpusIngestResult) -> list[str]:
    lines = [
        "✅ Model corpus ingestion complete",
        f"   Source dir: {r.source_dir}",
        f"   Output dir: {r.out_dir}",
        f"   Fixtures: {r.fixture_count}",
        f"   Accepted: {r.accepted_count}",
        f"   Partial: {r.partial_count}",
        f"   Rejected: {r.rejected_count}",
        f"   Report: {r.report_path}",
        f"   Summary: {r.summary_path}",
    ]
    for fixture in r.fixtures:
        lines.append(
            "   • "
            f"{fixture.fixture_id} [{fixture.status}] "
            f"symbols={fixture.symbol_count} wires={fixture.wire_count} "
            f"labels={fixture.label_count} circuit_ir={'yes' if fixture.has_circuit_ir else 'no'}"
        )
    return lines


@_register(ModelCorpusListResult)
def _fmt_model_corpus_list(r: ModelCorpusListResult) -> list[str]:
    lines = [f"📚 Model corpus fixtures: {r.corpus_dir}", f"   Count: {len(r.fixtures)}"]
    for fixture in r.fixtures:
        lines.append(
            "   • "
            f"{fixture.fixture_id} [{fixture.status}] "
            f"{fixture.source_file_name} "
            f"symbols={fixture.symbol_count} wires={fixture.wire_count} "
            f"labels={fixture.label_count} circuit_ir={'yes' if fixture.has_circuit_ir else 'no'}"
        )
    return lines


@_register(ModelCorpusEvaluateResult)
def _fmt_model_corpus_evaluate(r: ModelCorpusEvaluateResult) -> list[str]:
    return [
        "✅ Model corpus evaluation complete",
        f"   Corpus dir: {r.corpus_dir}",
        f"   Output dir: {r.out_dir}",
        f"   Fixture count: {r.fixture_count}",
        f"   Evaluated: {r.evaluated_count}",
        f"   Skipped: {r.skipped_count}",
        f"   Failed/partial: {r.failed_count}",
        f"   Summary JSON: {r.summary_json_path}",
        f"   Summary MD: {r.summary_md_path}",
    ]


@_register(InfoSchResult)
def _fmt_info_sch(r: InfoSchResult) -> list[str]:
    lines = [
        "📐 Schematic introspection",
        f"   Project: {r.project_path}",
        f"   Schematic: {r.schematic_path}",
        f"   Owned by OpenClaw: {'yes' if r.owned_by_openclaw else 'no'}",
        f"   Root symbols: {r.symbol_count}  labels: {r.label_count}",
    ]
    if r.managed_schematic_path is not None:
        lines.append(f"   Managed schematic: {r.managed_schematic_path}")
        managed_counts = f"{r.managed_symbol_count} symbols  {r.managed_label_count} labels"
        lines.append(f"   Managed AST nodes: {managed_counts}")
    lines += [
        f"   Total symbols (all sheets): {len(r.symbols)}",
        f"   Pin→net bindings: {len(r.pin_net_bindings)}",
    ]
    for warning in r.warnings:
        code = warning.get("code", "WARN")
        msg = warning.get("message", "")
        lines.append(f"   ⚠️  [{code}] {msg}")
    return lines


@_register(ApplyNetlistResult)
def _fmt_apply_netlist(r: ApplyNetlistResult) -> list[str]:
    if r.dry_run:
        header = "🔍 DRY RUN — Netlist validated (nothing written)"
        items_label = "Managed items validated"
    else:
        header = "✅ Netlist applied"
        items_label = "Managed items written"
    lines = [
        header,
        f"   Root schematic: {r.schematic_path}",
        f"   Managed schematic: {r.managed_schematic_path}",
        f"   Symbols added: {r.symbols_added}",
        f"   Nets applied: {r.nets_applied}",
        f"   {items_label}: {r.managed_items_written}",
        f"   KiCad CLI used: {'yes' if r.kicad_cli_used else 'no'}",
        f"   Heuristic profile: {r.heuristic_profile_name}",
        f"   Label mode: {r.label_mode_name}",
    ]
    if r.generated_schematic_diagnostics is not None:
        diag = r.generated_schematic_diagnostics
        lines.append(
            "   Generated structure: "
            f"{diag.symbol_count} symbol(s), {diag.wire_count} wire(s), "
            f"{diag.label_count} label(s), {diag.junction_count} junction(s)"
        )
    if r.warning_report_path is not None:
        lines.append(f"   Warning report: {r.warning_report_path}")
    if r.debug_dump_path is not None:
        lines.append(f"   Debug dump: {r.debug_dump_path}")
    if r.symbols_dirs_used:
        lines.append("   Symbol dirs used:")
        for d in r.symbols_dirs_used:
            lines.append(f"     • {d}")
    for warning in r.warnings:
        lines.append(f"   ⚠️  [{warning.get('code', 'WARN')}] {warning.get('message', '')}")
    return lines


@_register(NewFromNetlistResult)
def _fmt_new_from_netlist(r: NewFromNetlistResult) -> list[str]:
    lines = [
        f"✅ Created project from netlist: {r.name}",
        f"   Project path: {r.path}",
        f"   Root schematic: {r.schematic_path}",
        f"   Managed schematic: {r.managed_schematic_path}",
        f"   Symbols added: {r.symbols_added}",
        f"   Nets applied: {r.nets_applied}",
        f"   KiCad CLI used: {'yes' if r.kicad_cli_used else 'no'}",
        f"   Heuristic profile: {r.heuristic_profile_name}",
        f"   Label mode: {r.label_mode_name}",
    ]
    if r.generated_schematic_diagnostics is not None:
        diag = r.generated_schematic_diagnostics
        lines.append(
            "   Generated structure: "
            f"{diag.symbol_count} symbol(s), {diag.wire_count} wire(s), "
            f"{diag.label_count} label(s), {diag.junction_count} junction(s)"
        )
    if r.warning_report_path is not None:
        lines.append(f"   Warning report: {r.warning_report_path}")
    if r.debug_dump_path is not None:
        lines.append(f"   Debug dump: {r.debug_dump_path}")
    if r.session_path is not None:
        lines.append(f"   Session: {r.session_path}")
    if r.zip_path is not None:
        lines.append(f"   Schematic zip: {r.zip_path}")
    if r.symbols_dirs_used:
        lines.append("   Symbol dirs used:")
        for d in r.symbols_dirs_used:
            lines.append(f"     • {d}")
    for warning in r.warnings:
        lines.append(f"   ⚠️  [{warning.get('code', 'WARN')}] {warning.get('message', '')}")
    return lines


@_register(NewSessionResult)
def _fmt_new_session(r: NewSessionResult) -> list[str]:
    lines = [
        f"✅ Session started: {r.name} ({r.uuid[:8]})",
        f"   Session directory: {r.path}",
        f"   Created: {r.created}",
    ]
    if r.description:
        lines.append(f"   Description: {r.description}")
    lines.append(
        "   Place netlist JSON files here, then run new-from-netlist with just the filename."
    )
    return lines


@_register(SessionInfoResult)
def _fmt_session_info(r: SessionInfoResult) -> list[str]:
    s = r.session
    lines = [
        f"📁 Current session: {s.name} ({s.short_id})",
        f"   Path: {s.path}",
        f"   Created: {s.created}",
    ]
    if s.description:
        lines.append(f"   Description: {s.description}")
    lines.append(f"   KiCad projects: {r.project_count}")
    if r.netlist_files:
        lines.append("   Netlist files:")
        for f in r.netlist_files:
            lines.append(f"     • {f}")
    else:
        lines.append("   Netlist files: (none)")
    if r.zip_files:
        lines.append("   Zip files:")
        for f in r.zip_files:
            lines.append(f"     • {f}")
    return lines


@_register(ValidateNetlistResult)
def _fmt_validate_netlist(r: ValidateNetlistResult) -> list[str]:
    status = "✅ Circuit IR valid" if r.valid else "❌ Circuit IR invalid"
    lines = [
        f"{status}: {r.netlist_path}",
        f"   Components: {r.component_count}",
        f"   Nets: {r.net_count}",
    ]
    if r.symbols_dirs_used:
        lines.append("   Symbol dirs used:")
        for d in r.symbols_dirs_used:
            lines.append(f"     • {d}")
    if r.warnings:
        for warning in r.warnings:
            lines.append(f"   ⚠️  [{warning.get('code', 'WARN')}] {warning.get('message', '')}")
    else:
        lines.append("   No warnings.")
    return lines


@_register(FixNetlistResult)
def _fmt_fix_netlist(r: FixNetlistResult) -> list[str]:
    if r.fixed and not r.pin_validation_skipped:
        status = "✅ Circuit IR fixed"
    elif r.fixed and r.pin_validation_skipped:
        status = "✅ Schema/semantic fixed (pin aliases not checked)"
    else:
        status = "⚠️  Partial fix — errors remain"
    lines = [status, f"   Fixed JSON: {r.output_path}"]
    if r.component_count or r.net_count:
        lines.append(f"   Components: {r.component_count}   Nets: {r.net_count}")
    if r.fixes_applied:
        lines.append(f"   Fixes applied ({len(r.fixes_applied)}):")
        for fix in r.fixes_applied:
            lines.append(f"     • {fix}")
    else:
        lines.append("   No changes applied (JSON was already valid or unfixable).")
    if r.remaining_errors:
        lines.append(f"   Remaining errors ({len(r.remaining_errors)}) — fix manually:")
        for err in r.remaining_errors:
            lines.append(f"     ❌ {err}")
    if r.pin_validation_skipped:
        lines.append(
            "   ⚠️  Pin alias validation skipped — add --symbols-dir to check "
            "for invalid pin names (e.g. '+'/'-' for polarized caps, 'TIP'/'RING'/'SLEEVE')."
        )
    if r.fixed and not r.remaining_errors:
        lines.append("   → Pass the fixed JSON to new-from-netlist to create the project.")
    else:
        lines.append("   → Fix remaining errors, then run fix-netlist or new-from-netlist again.")
    return lines

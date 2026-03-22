"""CLI presentation layer: format command results into printable lines.

All formatting/rendering lives here so command modules stay pure
(return structured data, no ``print()``).  The CLI entry-point calls
:func:`format_result` and prints each returned line.
"""

from __future__ import annotations

import dataclasses
import enum
import json
from pathlib import Path

from .lint import LINT_SUGGESTIONS, LintSeverity
from .results import (
    AddComponentResult,
    AddNetResult,
    ApplyNetlistResult,
    ApplyPatternResult,
    AutoPlaceResult,
    AutoRouteResult,
    ConnectResult,
    DebugSymbolResult,
    DoctorResult,
    DrcResult,
    ErcResult,
    Export3dResult,
    ExportBomResult,
    ExportDrillResult,
    ExportGerbersResult,
    ExportPosResult,
    FixNetlistResult,
    FormatFileResult,
    ImportNetlistResult,
    InfoResult,
    InfoSchResult,
    LintFileResult,
    NewFromNetlistResult,
    NewProjectResult,
    NewSessionResult,
    OpenResult,
    PackageFabResult,
    PcbwayQuoteResult,
    PreviewPcbResult,
    PreviewSchematicResult,
    SearchSymbolsResult,
    SessionInfoResult,
    SetBoardSizeResult,
    ValidateFileResult,
    ValidateNetlistResult,
)

# ---------------------------------------------------------------------------
# dispatch registry
# ---------------------------------------------------------------------------

_FORMATTERS: dict[type, object] = {}


def _register(cls: type):  # type: ignore[type-arg]
    """Class-keyed decorator that registers a formatter function."""

    def decorator(fn):  # type: ignore[no-untyped-def]
        _FORMATTERS[cls] = fn
        return fn

    return decorator


def format_result(result: object) -> list[str]:
    """Dispatch *result* to the appropriate formatter; return lines to print."""
    fn = _FORMATTERS.get(type(result))
    if fn is None:
        return [repr(result)]
    return fn(result)  # type: ignore[operator]


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


class _ResultEncoder(json.JSONEncoder):
    """Encode types not handled by the default JSON encoder."""

    def default(self, o: object) -> object:  # noqa: ANN001
        if isinstance(o, Path):
            return str(o)
        if isinstance(o, enum.Enum):
            return o.value
        return super().default(o)


def format_result_json(result: object) -> str:
    """Serialise *result* as a JSON string.

    The result must be a dataclass instance.  ``Path`` objects are converted to
    strings; ``Enum`` values are stored as their ``.value``.
    """
    return json.dumps(dataclasses.asdict(result), cls=_ResultEncoder, indent=2)  # type: ignore[call-overload]


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
    ]
    if r.warning_report_path is not None:
        lines.append(f"   Warning report: {r.warning_report_path}")
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
    ]
    if r.warning_report_path is not None:
        lines.append(f"   Warning report: {r.warning_report_path}")
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


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


@_register(DrcResult)
def _fmt_drc(r: DrcResult) -> list[str]:
    lines: list[str] = []
    if r.passed:
        lines.append("✅ DRC passed!")
    else:
        lines.append("⚠️  DRC completed with issues")
        if r.stderr:
            lines.append(r.stderr)
    if r.validation is not None:
        if r.validation.issues:
            lines.append(f"\n📋 Found {len(r.validation.issues)} issues:")
            for issue in r.validation.issues[:10]:
                lines.append(f"  [{issue.severity}] {issue.description}")
            if len(r.validation.issues) > 10:
                lines.append(f"  ... and {len(r.validation.issues) - 10} more")
        else:
            lines.append("\n✅ No violations found!")
    return lines


@_register(ErcResult)
def _fmt_erc(r: ErcResult) -> list[str]:
    if r.passed:
        return ["✅ ERC passed!"]
    lines = ["⚠️  ERC completed with issues"]
    if r.stderr:
        lines.append(r.stderr)
    return lines


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@_register(ExportGerbersResult)
def _fmt_export_gerbers(r: ExportGerbersResult) -> list[str]:
    lines = [f"✅ Exported {len(r.files)} Gerber files to {r.output_dir}"]
    for f in r.files:
        lines.append(f"   {f.name}")
    return lines


@_register(ExportDrillResult)
def _fmt_export_drill(r: ExportDrillResult) -> list[str]:
    return [f"✅ Drill files exported to {r.output_dir}"]


@_register(ExportBomResult)
def _fmt_export_bom(r: ExportBomResult) -> list[str]:
    lines = [
        f"✅ BOM exported: {r.output_file}",
        f"   {max(0, len(r.lines) - 1)} component line(s)",
    ]
    for line in r.lines[:20]:
        lines.append(f"  {line.rstrip()}")
    return lines


@_register(PackageFabResult)
def _fmt_package_fab(r: PackageFabResult) -> list[str]:
    size_kb = r.size_bytes / 1024
    return [
        f"✅ Created: {r.output_path}",
        f"   Size: {size_kb:.1f} KB",
        "\n📤 Ready to upload to PCBWay!",
    ]


@_register(ExportPosResult)
def _fmt_export_pos(r: ExportPosResult) -> list[str]:
    return [
        f"✅ Position file: {r.output_file}",
        f"   {r.component_count} component(s)",
    ]


@_register(Export3dResult)
def _fmt_export_3d(r: Export3dResult) -> list[str]:
    size_kb = r.size_bytes / 1024
    return [
        f"✅ STEP model: {r.output_file}",
        f"   Size: {size_kb:.1f} KB",
        "   Open with FreeCAD, Fusion 360, or any STEP viewer.",
    ]


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


@_register(PreviewSchematicResult)
def _fmt_preview_sch(r: PreviewSchematicResult) -> list[str]:
    lines = [f"✅ Preview saved: {r.svg_file}"]
    if r.png_file:
        lines.append(f"   PNG: {r.png_file}")
    else:
        lines.append("   (Install cairosvg for PNG conversion)")
    return lines


@_register(PreviewPcbResult)
def _fmt_preview_pcb(r: PreviewPcbResult) -> list[str]:
    lines: list[str] = []
    for layer, path in r.layer_files:
        lines.append(f"   ✅ {layer}: {path.name}")
    if r.glb_file:
        lines.append(f"   ✅ 3D: {r.glb_file.name}")
    return lines


# ---------------------------------------------------------------------------
# pcb
# ---------------------------------------------------------------------------


@_register(SetBoardSizeResult)
def _fmt_set_board_size(r: SetBoardSizeResult) -> list[str]:
    prefix = "🔍 DRY RUN — " if r.dry_run else ""
    return [
        f"{prefix}✅ Board outline: {r.width} mm × {r.height} mm",
        f"   Edge.Cuts rectangle written to {r.pcb_file_name}",
    ]


@_register(ImportNetlistResult)
def _fmt_import_netlist(r: ImportNetlistResult) -> list[str]:
    lines = [f"✅ Netlist: {r.netlist_file}"]
    if r.components:
        lines.append(f"\n📦 Components ({len(r.components)}):")
        for ref, value, fp in r.components:
            tag = f"  [{fp}]" if fp else "  [NO FOOTPRINT ⚠️]"
            lines.append(f"  {ref:<6} {value:<20}{tag}")
        missing = [ref for ref, _, fp in r.components if not fp]
        if missing:
            lines.append(f"\n⚠️  Assign footprints to: {', '.join(missing)}")
    lines.append("\n💡 Open PCB editor → Tools → Update PCB from Schematic to sync.")
    return lines


@_register(AutoPlaceResult)
def _fmt_auto_place(r: AutoPlaceResult) -> list[str]:
    prefix = "🔍 DRY RUN — " if r.dry_run else ""
    lines = [f"{prefix}✅ Placed {len(r.placed)} footprint(s) (spacing {r.spacing} mm):"]
    for spec in r.placed:
        label = spec.ref.split(":")[-1] if ":" in spec.ref else spec.ref
        lines.append(f"   {label:<30} → ({spec.x:.1f}, {spec.y:.1f})")
    lines.append("\n💡 Run `drc` to check, then route with `auto-route` or KiCad PCB editor.")
    return lines


@_register(AutoRouteResult)
def _fmt_auto_route(r: AutoRouteResult) -> list[str]:
    lines = [f"✅ Routes complete: {r.ses_file_name}"]
    if r.routes_imported:
        lines.append("✅ Routes imported into PCB")
    else:
        lines.append("⚠️  Manual import: File → Import → Specctra Session in KiCad PCB editor")
    return lines


# ---------------------------------------------------------------------------
# sch
# ---------------------------------------------------------------------------


@_register(AddComponentResult)
def _fmt_add_component(r: AddComponentResult) -> list[str]:
    prefix = "🔍 DRY RUN — " if r.dry_run else ""
    lines = [
        f"{prefix}✅ Added {r.ref} ({r.lib_sym})  value={r.value}",
        f"   Position: ({r.x:.1f}, {r.y:.1f}) mm  |  Pins: {', '.join(r.pins)}",
    ]
    if not r.has_footprint:
        lines.append("   ⚠️  No footprint — assign in KiCad or use --footprint")
    lines.append("\n💡 Run `preview-schematic` to verify, then wire with `connect`.")
    return lines


@_register(AddNetResult)
def _fmt_add_net(r: AddNetResult) -> list[str]:
    prefix = "🔍 DRY RUN — " if r.dry_run else ""
    return [f"{prefix}✅ Net label '{r.name}' added at ({r.x}, {r.y})"]


@_register(ConnectResult)
def _fmt_connect(r: ConnectResult) -> list[str]:
    prefix = "🔍 DRY RUN — " if r.dry_run else ""
    return [f"{prefix}✅ Wire added: ({r.x1}, {r.y1}) → ({r.x2}, {r.y2})"]


@_register(ApplyPatternResult)
def _fmt_apply_pattern(r: ApplyPatternResult) -> list[str]:
    prefix = "🔍 DRY RUN — " if r.dry_run else ""
    lines = [
        f"{prefix}✅ Applied pattern '{r.pattern}'",
        f"   Components: {', '.join(r.components)}",
        f"   Nets:       {', '.join(r.nets)}",
        "",
        "💡 Run `preview-schematic` to verify, then assign footprints and run `erc`.",
    ]
    return lines


# ---------------------------------------------------------------------------
# lint / validate / format (Phase 6)
# ---------------------------------------------------------------------------


def _fmt_issue_lines(issues: tuple) -> list[str]:  # type: ignore[type-arg]
    """Return formatted lines for a sequence of :class:`~kicad_pcb.lint.LintIssue` objects."""
    lines: list[str] = []
    for issue in issues:
        icon = "❌" if issue.severity is LintSeverity.ERROR else "⚠️ "
        lines.append(f"  {icon} [{issue.code}] {issue.message}")
        if issue.path:
            lines.append(f"       path: {issue.path}")
        suggestion = LINT_SUGGESTIONS.get(issue.code)
        if suggestion:
            lines.append(f"       💡 {suggestion}")
    return lines


@_register(LintFileResult)
def _fmt_lint_file(r: LintFileResult) -> list[str]:
    status = "✅" if r.ok else "❌"
    lines = [
        f"{status} {r.path}",
        f"   {r.error_count} error(s), {r.warning_count} warning(s)",
    ]
    lines.extend(_fmt_issue_lines(r.issues))
    return lines


@_register(ValidateFileResult)
def _fmt_validate_file(r: ValidateFileResult) -> list[str]:
    overall = "✅" if r.ok else "❌"
    syntax_icon = "✅" if r.syntax_ok else "❌"
    lines = [
        f"{overall} {r.path}",
        (
            f"   {syntax_icon} Syntax OK"
            if r.syntax_ok
            else "   \u274c Syntax error \u2014 file could not be parsed"
        ),
        f"   Lint: {r.lint_error_count} error(s), {r.lint_warning_count} warning(s)",
    ]
    lines.extend(_fmt_issue_lines(r.lint_issues))
    if r.kicad_checked:
        kicad_icon = "✅" if r.kicad_ok else "❌"
        lines.append(f"   {kicad_icon} KiCad check {'passed' if r.kicad_ok else 'failed'}")
    else:
        lines.append("   ℹ️  KiCad DRC/ERC not run (use `drc`/`erc` for full check)")
    return lines


@_register(FormatFileResult)
def _fmt_format_file(r: FormatFileResult) -> list[str]:
    if r.changed:
        return [f"✅ Reformatted {r.path} ({r.size_bytes} bytes)"]
    return [f"✅ {r.path} already canonical ({r.size_bytes} bytes)"]


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

_STATUS_ICON: dict[str, str] = {
    "ok": "✅",
    "warn": "⚠️ ",
    "error": "❌",
    "info": "ℹ️ ",
}


@_register(DoctorResult)
def _fmt_doctor(r: DoctorResult) -> list[str]:
    lines = ["\U0001fa7a kicad-pcb doctor\n"]
    for item in r.checks:
        icon = _STATUS_ICON.get(item.status, "  ")
        lines.append(f"  {icon} {item.label}: {item.message}")
        if item.detail:
            lines.append(f"     {item.detail}")
    lines.append("")
    if r.overall_ok:
        lines.append("✅ All checks passed")
    return lines


# ---------------------------------------------------------------------------
# search-symbols / debug-symbol
# ---------------------------------------------------------------------------


@_register(SearchSymbolsResult)
def _fmt_search_symbols(r: SearchSymbolsResult) -> list[str]:
    lines: list[str] = []
    dirs_str = ", ".join(r.symbols_dirs) if r.symbols_dirs else "(none)"
    lines.append(f"🔍 search-symbols: {r.query!r}  (searched: {dirs_str})")
    if not r.matches:
        lines.append("  No symbols found matching query.")
        lines.append("  Tip: use broader keywords, e.g. 'capacitor' instead of 'electrolytic'.")
        return lines
    lines.append(f"  Found {len(r.matches)} match(es):")
    lines.append("")
    for m in r.matches:
        desc = f"  — {m.description}" if m.description else ""
        lines.append(f"  {m.symbol_id}  ({m.pin_count} pins){desc}")
    lines.append("")
    lines.append('Use these symbol IDs directly in Circuit IR JSON  ("symbol": "Lib:Name").')
    return lines


@_register(DebugSymbolResult)
def _fmt_debug_symbol(r: DebugSymbolResult) -> list[str]:
    lines: list[str] = [f"🔬 debug-symbol: {r.symbol_id}"]
    if r.extends_base:
        lines.append(f"  Extends: {r.extends_base}")
    else:
        lines.append("  Extends: (none — standalone symbol)")
    pin_str = "  ".join(
        sorted(r.pin_numbers, key=lambda p: (int(p) if p.isdigit() else float("inf"), p))
    )
    lines.append(f"  Pins ({r.pin_count}): {pin_str}")
    return lines


# ---------------------------------------------------------------------------
# external
# ---------------------------------------------------------------------------


@_register(PcbwayQuoteResult)
def _fmt_pcbway_quote(r: PcbwayQuoteResult) -> list[str]:
    lines = [
        "╭─────────────────────────────────────╮",
        "│       💰 PCBWAY QUOTE ESTIMATE      │",
        "├─────────────────────────────────────┤",
        f"│  Quantity:    {r.quantity:>4} pcs              │",
        f"│  Layers:      {r.layers:>4}                   │",
        f"│  Thickness:   {r.thickness:>4} mm              │",
        "├─────────────────────────────────────┤",
        f"│  Board cost:  ${r.board_cost:>7.2f}              │",
        f"│  Shipping:    ${r.shipping:>7.2f} (DHL est.)   │",
        "│  ─────────────────────────          │",
        f"│  TOTAL:       ${r.total:>7.2f}              │",
        "╰─────────────────────────────────────╯",
        "",
        "⚠️  This is an estimate. Actual price may vary.",
        "📤 To order: Upload Gerbers at pcbway.com/orderonline.aspx",
    ]
    if r.gerber_zip:
        lines.append(f"\n✅ Gerber package ready: {r.gerber_zip}")
    else:
        lines.append("\n💡 Run `package-for-fab` first to create Gerber ZIP")
    return lines

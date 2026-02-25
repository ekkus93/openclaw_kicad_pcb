"""CLI presentation layer: format command results into printable lines.

All formatting/rendering lives here so command modules stay pure
(return structured data, no ``print()``).  The CLI entry-point calls
:func:`format_result` and prints each returned line.
"""
from __future__ import annotations

from .results import (
    AddComponentResult,
    AddNetResult,
    AutoPlaceResult,
    AutoRouteResult,
    ConnectResult,
    DoctorResult,
    DrcResult,
    ErcResult,
    Export3dResult,
    ExportBomResult,
    ExportDrillResult,
    ExportGerbersResult,
    ExportPosResult,
    ImportNetlistResult,
    InfoResult,
    NewProjectResult,
    OpenResult,
    PackageFabResult,
    PcbwayQuoteResult,
    PreviewPcbResult,
    PreviewSchematicResult,
    SetBoardSizeResult,
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
    return [
        f"✅ Board outline: {r.width} mm × {r.height} mm",
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
    lines = [f"✅ Placed {len(r.placed)} footprint(s) (spacing {r.spacing} mm):"]
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
        lines.append(
            "⚠️  Manual import: File → Import → Specctra Session in KiCad PCB editor"
        )
    return lines


# ---------------------------------------------------------------------------
# sch
# ---------------------------------------------------------------------------


@_register(AddComponentResult)
def _fmt_add_component(r: AddComponentResult) -> list[str]:
    lines = [
        f"✅ Added {r.ref} ({r.lib_sym})  value={r.value}",
        f"   Position: ({r.x:.1f}, {r.y:.1f}) mm  |  Pins: {', '.join(r.pins)}",
    ]
    if not r.has_footprint:
        lines.append("   ⚠️  No footprint — assign in KiCad or use --footprint")
    lines.append("\n💡 Run `preview-schematic` to verify, then wire with `connect`.")
    return lines


@_register(AddNetResult)
def _fmt_add_net(r: AddNetResult) -> list[str]:
    return [f"✅ Net label '{r.name}' added at ({r.x}, {r.y})"]


@_register(ConnectResult)
def _fmt_connect(r: ConnectResult) -> list[str]:
    return [f"✅ Wire added: ({r.x1}, {r.y1}) → ({r.x2}, {r.y2})"]


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

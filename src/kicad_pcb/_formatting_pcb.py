"""Formatters for PCB validation, export, preview, and hardware operations."""

from __future__ import annotations

from ._formatting_core import _register
from .results import (
    AutoPlaceResult,
    AutoRouteResult,
    DrcResult,
    ErcResult,
    Export3dResult,
    ExportBomResult,
    ExportDrillResult,
    ExportGerbersResult,
    ExportPosResult,
    ImportNetlistResult,
    PackageFabResult,
    PreviewPcbResult,
    PreviewSchematicResult,
    SetBoardSizeResult,
)

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

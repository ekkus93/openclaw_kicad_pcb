"""Formatters for schematic design, lint, doctor, search, and external results."""

from __future__ import annotations

from ._formatting_core import _register
from .lint import LINT_SUGGESTIONS, LintSeverity
from .results import (
    AddComponentResult,
    AddNetResult,
    ApplyPatternResult,
    ConnectResult,
    DebugSymbolResult,
    DoctorResult,
    FormatFileResult,
    LintFileResult,
    PcbwayQuoteResult,
    SearchSymbolsResult,
    ValidateFileResult,
)

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
            else "   ❌ Syntax error — file could not be parsed"
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

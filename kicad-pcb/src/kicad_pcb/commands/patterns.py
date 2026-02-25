"""Pattern application command: ``apply-pattern``.

Dispatches to individual pattern functions in :mod:`kicad_pcb.patterns` and
wraps the full mutation in :func:`~kicad_pcb.pipeline.mutate_and_validate_sch`
so every pattern is validated before being committed to disk.

Pattern functions each operate on a :class:`~kicad_pcb.sch_doc.SchematicDoc`
directly, allowing multiple components, labels, and wires to be added in a
single atomic transaction.
"""

from __future__ import annotations

from pathlib import Path

from ..config import SymbolsDir, discover_symbols_dir, get_current_project
from ..errors import UserError
from ..patterns import (
    PATTERNS,
    PatternOutcome,
    pattern_connector_breakout,
    pattern_decoupling_cap,
    pattern_led_resistor,
    pattern_resistor_divider,
)
from ..pipeline import mutate_and_validate_sch
from ..results import ApplyPatternResult
from ..sch_doc import SchematicDoc

# Fallback if symbol library discovery fails (kept consistent with commands/sch.py).
_DEFAULT_SYMBOLS_DIR = Path("/usr/share/kicad/symbols")


# ---------------------------------------------------------------------------
# Public command
# ---------------------------------------------------------------------------


def cmd_apply_pattern(args) -> ApplyPatternResult:  # noqa: ANN001 — argparse Namespace
    """Apply a named circuit pattern to the current project's schematic.

    Usage::

        kicad_pcb apply-pattern --pattern resistor-divider \\
            --r1 R1 --r2 R2 --r1-value 10k --r2-value 10k \\
            --vin-net VIN --vout-net VOUT --gnd-net GND

        kicad_pcb apply-pattern --pattern led-resistor \\
            --r R1 --d D1 --r-value 330 \\
            --vcc-net VCC --gnd-net GND

        kicad_pcb apply-pattern --pattern connector-breakout \\
            --conn J1 --n-pins 4 --net-prefix IO

        kicad_pcb apply-pattern --pattern decoupling-cap \\
            --c C1 --c-value 100nF --vcc-net VCC --gnd-net GND
    """
    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    sch_file = project.sch_file
    if not sch_file.exists():
        raise UserError(f"Schematic not found: {sch_file}")

    pattern_name: str = args.pattern
    if pattern_name not in PATTERNS:
        names = ", ".join(sorted(PATTERNS))
        raise UserError(f"Unknown pattern {pattern_name!r}. Available: {names}")

    # Resolve symbol library directory (same strategy as cmd_add_component).
    explicit_path = Path(args.symbols_dir) if getattr(args, "symbols_dir", None) else None
    sym_dir_result: SymbolsDir | None = discover_symbols_dir(explicit=explicit_path)
    sym_dir: Path | None = sym_dir_result.path if sym_dir_result is not None else None

    dry_run: bool = getattr(args, "dry_run", False)
    require_footprints: bool = getattr(args, "require_footprints", False)

    # This dict captures the pattern outcome from inside the mutator closure.
    _outcome: dict[str, PatternOutcome] = {}

    def _mutate(doc: SchematicDoc) -> None:
        ox, oy = doc.next_component_position()
        outcome = _dispatch_pattern(
            pattern_name,
            doc,
            ox,
            oy,
            args,
            sym_dir=sym_dir,
            project_name=project.name,
            require_footprints=require_footprints,
        )
        _outcome["result"] = outcome

    mutate_and_validate_sch(sch_file, _mutate, operation="apply-pattern", dry_run=dry_run)

    outcome = _outcome["result"]
    return ApplyPatternResult(
        pattern=pattern_name,
        components=tuple(c.ref for c in outcome.components),
        nets=outcome.nets,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# Pattern dispatcher
# ---------------------------------------------------------------------------


def _dispatch_pattern(  # noqa: PLR0913
    name: str,
    doc: SchematicDoc,
    ox: float,
    oy: float,
    args,  # noqa: ANN001 — argparse Namespace
    *,
    sym_dir: Path | None,
    project_name: str,
    require_footprints: bool = False,
) -> PatternOutcome:
    """Call the matching pattern function, pulling arguments from *args*."""
    if name == "resistor-divider":
        return pattern_resistor_divider(
            doc,
            ox,
            oy,
            r1_ref=getattr(args, "r1", "R1"),
            r2_ref=getattr(args, "r2", "R2"),
            r1_value=getattr(args, "r1_value", "10k"),
            r2_value=getattr(args, "r2_value", "10k"),
            vin_net=getattr(args, "vin_net", "VIN"),
            vout_net=getattr(args, "vout_net", "VOUT"),
            gnd_net=getattr(args, "gnd_net", "GND"),
            symbols_dir=sym_dir,
            project_name=project_name,
            require_footprints=require_footprints,
        )

    if name == "led-resistor":
        return pattern_led_resistor(
            doc,
            ox,
            oy,
            r_ref=getattr(args, "r", "R1"),
            d_ref=getattr(args, "d", "D1"),
            r_value=getattr(args, "r_value", "330"),
            led_value=getattr(args, "d_value", "LED"),
            vcc_net=getattr(args, "vcc_net", "VCC"),
            gnd_net=getattr(args, "gnd_net", "GND"),
            symbols_dir=sym_dir,
            project_name=project_name,
            require_footprints=require_footprints,
        )

    if name == "connector-breakout":
        return pattern_connector_breakout(
            doc,
            ox,
            oy,
            conn_ref=getattr(args, "conn", "J1"),
            n_pins=int(getattr(args, "n_pins", 4)),
            net_prefix=getattr(args, "net_prefix", "IO"),
            symbols_dir=sym_dir,
            project_name=project_name,
            require_footprints=require_footprints,
        )

    if name == "decoupling-cap":
        return pattern_decoupling_cap(
            doc,
            ox,
            oy,
            c_ref=getattr(args, "c", "C1"),
            c_value=getattr(args, "c_value", "100nF"),
            vcc_net=getattr(args, "vcc_net", "VCC"),
            gnd_net=getattr(args, "gnd_net", "GND"),
            symbols_dir=sym_dir,
            project_name=project_name,
            require_footprints=require_footprints,
        )

    # Should never reach here because cmd_apply_pattern checks the registry.
    raise UserError(f"Unhandled pattern: {name!r}")

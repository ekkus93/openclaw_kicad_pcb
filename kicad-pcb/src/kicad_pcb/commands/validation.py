"""Design/electrical rules check commands: drc, erc."""
from __future__ import annotations

from ..adapters import KicadCliAdapter
from ..config import get_current_project
from ..errors import UserError
from ..models import ValidationResult
from ..results import DrcResult, ErcResult
from ..runner import check_kicad, find_kicad_cli


def cmd_drc(args, *, cli: KicadCliAdapter | None = None) -> DrcResult:
    """Run design rules check on PCB.

    *cli* is an optional injectable :class:`~kicad_pcb.adapters.KicadCliAdapter`.
    When ``None``, a default adapter using the system ``kicad-cli`` is created.
    """
    if cli is None:
        check_kicad()
        cli = KicadCliAdapter(kicad_cli=find_kicad_cli())

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    pcb_file = project.pcb_file
    if not pcb_file.exists():
        raise UserError(f"PCB file not found: {pcb_file}")

    output_file = project.path / "drc_report.json"

    result, report = cli.drc(pcb_file, output_file)

    validation = (
        ValidationResult.from_report(report, result.returncode)
        if report is not None
        else None
    )
    return DrcResult(passed=result.returncode == 0, stderr=result.stderr, validation=validation)


def cmd_erc(args, *, cli: KicadCliAdapter | None = None) -> ErcResult:
    """Run electrical rules check on schematic.

    *cli* is an optional injectable :class:`~kicad_pcb.adapters.KicadCliAdapter`.
    When ``None``, a default adapter using the system ``kicad-cli`` is created.
    """
    if cli is None:
        check_kicad()
        cli = KicadCliAdapter(kicad_cli=find_kicad_cli())

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    sch_file = project.sch_file
    if not sch_file.exists():
        raise UserError(f"Schematic file not found: {sch_file}")

    output_file = project.path / "erc_report.json"

    result, _ = cli.erc(sch_file, output_file)

    return ErcResult(passed=result.returncode == 0, stderr=result.stderr)

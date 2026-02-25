"""Design/electrical rules check commands: drc, erc."""
from __future__ import annotations

from ..adapters import KicadCliAdapter
from ..config import get_current_project
from ..errors import UserError
from ..models import ValidationResult
from ..runner import KICAD_CLI, check_kicad


def cmd_drc(args, *, cli: KicadCliAdapter | None = None) -> None:
    """Run design rules check on PCB.

    *cli* is an optional injectable :class:`~kicad_pcb.adapters.KicadCliAdapter`.
    When ``None``, a default adapter using the system ``kicad-cli`` is created.
    """
    if cli is None:
        check_kicad()
        cli = KicadCliAdapter(kicad_cli=KICAD_CLI)

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    pcb_file = project.pcb_file
    if not pcb_file.exists():
        raise UserError(f"PCB file not found: {pcb_file}")

    output_file = project.path / "drc_report.json"

    print(f"🔍 Running DRC on {pcb_file.name}...")

    result, report = cli.drc(pcb_file, output_file)

    if result.returncode != 0:
        print("⚠️  DRC completed with issues")
        if result.stderr:
            print(result.stderr)
    else:
        print("✅ DRC passed!")

    # Display results using typed ValidationResult
    if report is not None:
        validation = ValidationResult.from_report(report, result.returncode)
        if validation.issues:
            print(f"\n📋 Found {len(validation.issues)} issues:")
            for issue in validation.issues[:10]:
                print(f"  [{issue.severity}] {issue.description}")
            if len(validation.issues) > 10:
                print(f"  ... and {len(validation.issues) - 10} more")
        else:
            print("\n✅ No violations found!")


def cmd_erc(args, *, cli: KicadCliAdapter | None = None) -> None:
    """Run electrical rules check on schematic.

    *cli* is an optional injectable :class:`~kicad_pcb.adapters.KicadCliAdapter`.
    When ``None``, a default adapter using the system ``kicad-cli`` is created.
    """
    if cli is None:
        check_kicad()
        cli = KicadCliAdapter(kicad_cli=KICAD_CLI)

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    sch_file = project.sch_file
    if not sch_file.exists():
        raise UserError(f"Schematic file not found: {sch_file}")

    output_file = project.path / "erc_report.json"

    print(f"🔍 Running ERC on {sch_file.name}...")

    result, _ = cli.erc(sch_file, output_file)

    if result.returncode != 0:
        print("⚠️  ERC completed with issues")
    else:
        print("✅ ERC passed!")

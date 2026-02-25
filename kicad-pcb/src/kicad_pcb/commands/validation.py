"""Design/electrical rules check commands: drc, erc."""
from __future__ import annotations

import json

from ..config import get_current_project
from ..errors import UserError
from ..models import ValidationResult
from ..runner import check_kicad, run_kicad_cli


def cmd_drc(args) -> None:
    """Run design rules check on PCB."""
    check_kicad()

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    pcb_file = project.pcb_file
    if not pcb_file.exists():
        raise UserError(f"PCB file not found: {pcb_file}")

    output_file = project.path / "drc_report.json"

    print(f"🔍 Running DRC on {pcb_file.name}...")

    result = run_kicad_cli([
        "pcb", "drc",
        "--format", "json",
        "--output", str(output_file),
        "--severity-all",
        str(pcb_file),
    ])

    if result.returncode != 0:
        print("⚠️  DRC completed with issues")
        if result.stderr:
            print(result.stderr)
    else:
        print("✅ DRC passed!")

    # Parse and display results using typed ValidationResult
    if output_file.exists():
        with output_file.open() as f:
            report = json.load(f)

        validation = ValidationResult.from_report(report, result.returncode)
        if validation.issues:
            print(f"\n📋 Found {len(validation.issues)} issues:")
            for issue in validation.issues[:10]:
                print(f"  [{issue.severity}] {issue.description}")
            if len(validation.issues) > 10:
                print(f"  ... and {len(validation.issues) - 10} more")
        else:
            print("\n✅ No violations found!")


def cmd_erc(args) -> None:
    """Run electrical rules check on schematic."""
    check_kicad()

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    sch_file = project.sch_file
    if not sch_file.exists():
        raise UserError(f"Schematic file not found: {sch_file}")

    output_file = project.path / "erc_report.json"

    print(f"🔍 Running ERC on {sch_file.name}...")

    result = run_kicad_cli([
        "sch", "erc",
        "--format", "json",
        "--output", str(output_file),
        "--severity-all",
        str(sch_file),
    ])

    if result.returncode != 0:
        print("⚠️  ERC completed with issues")
    else:
        print("✅ ERC passed!")

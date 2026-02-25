"""Design/electrical rules check commands: drc, erc."""
from __future__ import annotations

import json
from pathlib import Path

from ..config import get_current_project
from ..errors import UserError
from ..runner import check_kicad, run_kicad_cli


def cmd_drc(args) -> None:
    """Run design rules check on PCB."""
    check_kicad()

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    project_dir = Path(project["path"])
    pcb_file = project_dir / f"{project['name']}.kicad_pcb"

    if not pcb_file.exists():
        raise UserError(f"PCB file not found: {pcb_file}")

    output_file = project_dir / "drc_report.json"

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

    # Parse and display results
    if output_file.exists():
        with output_file.open() as f:
            report = json.load(f)

        violations = report.get("violations", [])
        if violations:
            print(f"\n📋 Found {len(violations)} issues:")
            for v in violations[:10]:
                severity = v.get("severity", "unknown")
                desc = v.get("description", "No description")
                print(f"  [{severity}] {desc}")
            if len(violations) > 10:
                print(f"  ... and {len(violations) - 10} more")
        else:
            print("\n✅ No violations found!")


def cmd_erc(args) -> None:
    """Run electrical rules check on schematic."""
    check_kicad()

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    project_dir = Path(project["path"])
    sch_file = project_dir / f"{project['name']}.kicad_sch"

    if not sch_file.exists():
        raise UserError(f"Schematic file not found: {sch_file}")

    output_file = project_dir / "erc_report.json"

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

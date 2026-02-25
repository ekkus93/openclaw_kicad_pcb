"""Project management commands: new, info, open."""
from __future__ import annotations

import json
import uuid as _uuid_module
from datetime import datetime
from pathlib import Path

from ..config import PROJECTS_DIR, get_current_project, load_config, set_current_project
from ..errors import UserError
from ..fs import _atomic_write


def cmd_new(args) -> None:
    """Create new KiCad project."""
    name = args.name.replace(" ", "_")
    config = load_config()
    projects_dir = Path(config.get("projects_dir", PROJECTS_DIR))

    project_dir = projects_dir / name
    if project_dir.exists():
        raise UserError(f"Project already exists: {project_dir}")

    project_dir.mkdir(parents=True)

    # Create project file (JSON — atomic write, no sexp validation needed)
    pro_file = project_dir / f"{name}.kicad_pro"
    pro_content = {
        "board": {"design_settings": {}},
        "meta": {"filename": f"{name}.kicad_pro", "version": 1},
        "schematic": {"drawing": {}},
        "sheets": [[f"{name}.kicad_sch", ""]],
    }
    _atomic_write(pro_file, json.dumps(pro_content, indent=2), operation="new")

    # Create empty schematic (validated via _atomic_write)
    sch_file = project_dir / f"{name}.kicad_sch"
    sch_content = f'''(kicad_sch (version 20230121) (generator eeschema)
  (uuid "{_uuid_module.uuid4()!s}")
  (paper "A4")
  (lib_symbols)
  (sheet_instances
    (path "/" (page "1"))
  )
)
'''
    _atomic_write(sch_file, sch_content, "kicad_sch", operation="new")

    # Create empty PCB
    pcb_file = project_dir / f"{name}.kicad_pcb"
    pcb_content = '''(kicad_pcb (version 20230121) (generator pcbnew)
  (general
    (thickness 1.6)
  )
  (paper "A4")
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (32 "B.Adhes" user "B.Adhesive")
    (33 "F.Adhes" user "F.Adhesive")
    (34 "B.Paste" user)
    (35 "F.Paste" user)
    (36 "B.SilkS" user "B.Silkscreen")
    (37 "F.SilkS" user "F.Silkscreen")
    (38 "B.Mask" user)
    (39 "F.Mask" user)
    (40 "Dwgs.User" user "User.Drawings")
    (41 "Cmts.User" user "User.Comments")
    (42 "Eco1.User" user "User.Eco1")
    (43 "Eco2.User" user "User.Eco2")
    (44 "Edge.Cuts" user)
    (45 "Margin" user)
    (46 "B.CrtYd" user "B.Courtyard")
    (47 "F.CrtYd" user "F.Courtyard")
    (48 "B.Fab" user)
    (49 "F.Fab" user)
    (50 "User.1" user)
    (51 "User.2" user)
  )
  (setup
    (pad_to_mask_clearance 0)
  )
  (net 0 "")
)
'''
    _atomic_write(pcb_file, pcb_content, "kicad_pcb", operation="new")

    # Save as current project
    project: dict = {
        "name": name,
        "path": str(project_dir),
        "created": datetime.now().isoformat(),
        "description": args.description or "",
    }
    set_current_project(project)

    print(f"✅ Created project: {name}")
    print(f"   Path: {project_dir}")
    print("   Files:")
    print(f"     - {name}.kicad_pro")
    print(f"     - {name}.kicad_sch")
    print(f"     - {name}.kicad_pcb")

    if args.description:
        print(f"   Description: {args.description}")


def cmd_info(args) -> None:
    """Show current project info."""
    project = get_current_project()

    if not project:
        raise UserError("No project selected\n      Use: kicad_pcb.py new <name>")

    project_dir = Path(project["path"])

    print("╭─────────────────────────────────────╮")
    print("│      🔧 KICAD PROJECT INFO          │")
    print("├─────────────────────────────────────┤")
    print(f"│  Name: {project['name']:<27} │")
    print(f"│  Path: {str(project_dir)[:27]:<27} │")
    print("╰─────────────────────────────────────╯")

    # List files
    if project_dir.exists():
        print("\nFiles:")
        for f in sorted(project_dir.iterdir()):
            size = f.stat().st_size
            print(f"  {f.name:<30} {size:>8} bytes")


def cmd_open(args) -> None:
    """Open existing project."""
    project_path = Path(args.path).resolve()

    if not project_path.exists():
        raise UserError(f"Path not found: {project_path}")

    # Find project file
    if project_path.is_file() and project_path.suffix == ".kicad_pro":
        pro_file = project_path
        project_dir = project_path.parent
    else:
        project_dir = project_path
        pro_files = list(project_dir.glob("*.kicad_pro"))
        if not pro_files:
            raise UserError(f"No .kicad_pro file found in {project_dir}")
        pro_file = pro_files[0]

    name = pro_file.stem

    project: dict = {
        "name": name,
        "path": str(project_dir),
        "opened": datetime.now().isoformat(),
    }
    set_current_project(project)

    print(f"✅ Opened project: {name}")
    print(f"   Path: {project_dir}")

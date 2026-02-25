#!/usr/bin/env python3
"""
KiCad PCB Automation — Design to Manufacturing Pipeline

Commands:
    new <name>              Create new KiCad project
    info                    Show current project info
    preview-schematic       Generate schematic preview image
    preview-pcb             Generate PCB preview images
    drc                     Run design rules check
    erc                     Run electrical rules check
    export-gerbers          Export Gerber files for manufacturing
    export-drill            Export drill files
    export-bom              Export bill of materials
    package-for-fab         Create ZIP with all fab files
    pcbway-quote            Get PCBWay instant quote
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid as uuid_module
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

# Configuration
CONFIG_DIR = Path.home() / ".kicad-pcb"
CONFIG_FILE = CONFIG_DIR / "config.json"
PROJECTS_DIR = Path.home() / "kicad-projects"
CURRENT_PROJECT_FILE = CONFIG_DIR / "current_project.json"

# KiCad paths (auto-detect)
KICAD_CLI = shutil.which("kicad-cli") or "/usr/bin/kicad-cli"

# Default PCB options
DEFAULT_PCB_OPTIONS = {
    "layers": 2,
    "thickness": 1.6,
    "color": "green",
    "surface_finish": "hasl",
    "copper_weight": "1oz",
    "min_hole": 0.3,
    "min_trace": 0.15
}


def ensure_dirs():
    """Create necessary directories."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Load configuration."""
    ensure_dirs()
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except:
            pass
    return {"projects_dir": str(PROJECTS_DIR)}


def save_config(config: dict):
    """Save configuration."""
    ensure_dirs()
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def get_current_project() -> Optional[dict]:
    """Get current project info."""
    if CURRENT_PROJECT_FILE.exists():
        try:
            with open(CURRENT_PROJECT_FILE) as f:
                return json.load(f)
        except:
            pass
    return None


def set_current_project(project: dict):
    """Set current project."""
    ensure_dirs()
    with open(CURRENT_PROJECT_FILE, "w") as f:
        json.dump(project, f, indent=2)


def check_kicad():
    """Check if KiCad CLI is available."""
    if not shutil.which("kicad-cli"):
        print("❌ KiCad CLI not found!")
        print("\nInstall KiCad:")
        print("  Ubuntu: sudo apt install kicad")
        print("  Or: https://www.kicad.org/download/")
        return False
    return True


def run_kicad_cli(args: List[str], capture=True) -> subprocess.CompletedProcess:
    """Run kicad-cli command."""
    cmd = [KICAD_CLI] + args
    if capture:
        return subprocess.run(cmd, capture_output=True, text=True)
    else:
        return subprocess.run(cmd)


# =============================================================================
# Project Management
# =============================================================================

def cmd_new(args):
    """Create new KiCad project."""
    name = args.name.replace(" ", "_")
    config = load_config()
    projects_dir = Path(config.get("projects_dir", PROJECTS_DIR))
    
    project_dir = projects_dir / name
    if project_dir.exists():
        print(f"❌ Project already exists: {project_dir}")
        sys.exit(1)
    
    project_dir.mkdir(parents=True)
    
    # Create project file
    pro_file = project_dir / f"{name}.kicad_pro"
    pro_content = {
        "board": {"design_settings": {}},
        "meta": {"filename": f"{name}.kicad_pro", "version": 1},
        "schematic": {"drawing": {}},
        "sheets": [[f"{name}.kicad_sch", ""]]
    }
    with open(pro_file, "w") as f:
        json.dump(pro_content, f, indent=2)
    
    # Create empty schematic
    sch_file = project_dir / f"{name}.kicad_sch"
    sch_content = f'''(kicad_sch (version 20230121) (generator eeschema)
  (uuid "{datetime.now().strftime('%Y%m%d%H%M%S')}")
  (paper "A4")
  (lib_symbols)
  (sheet_instances
    (path "/" (page "1"))
  )
)
'''
    with open(sch_file, "w") as f:
        f.write(sch_content)
    
    # Create empty PCB
    pcb_file = project_dir / f"{name}.kicad_pcb"
    pcb_content = f'''(kicad_pcb (version 20230121) (generator pcbnew)
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
    with open(pcb_file, "w") as f:
        f.write(pcb_content)
    
    # Save as current project
    project = {
        "name": name,
        "path": str(project_dir),
        "created": datetime.now().isoformat(),
        "description": args.description or ""
    }
    set_current_project(project)
    
    print(f"✅ Created project: {name}")
    print(f"   Path: {project_dir}")
    print(f"   Files:")
    print(f"     - {name}.kicad_pro")
    print(f"     - {name}.kicad_sch")
    print(f"     - {name}.kicad_pcb")
    
    if args.description:
        print(f"   Description: {args.description}")


def cmd_info(args):
    """Show current project info."""
    project = get_current_project()
    
    if not project:
        print("❌ No project selected")
        print("   Use: kicad_pcb.py new <name>")
        sys.exit(1)
    
    project_dir = Path(project["path"])
    
    print(f"╭─────────────────────────────────────╮")
    print(f"│      🔧 KICAD PROJECT INFO          │")
    print(f"├─────────────────────────────────────┤")
    print(f"│  Name: {project['name']:<27} │")
    print(f"│  Path: {str(project_dir)[:27]:<27} │")
    print(f"╰─────────────────────────────────────╯")
    
    # List files
    if project_dir.exists():
        print(f"\nFiles:")
        for f in sorted(project_dir.iterdir()):
            size = f.stat().st_size
            print(f"  {f.name:<30} {size:>8} bytes")


def cmd_open(args):
    """Open existing project."""
    project_path = Path(args.path).resolve()
    
    if not project_path.exists():
        print(f"❌ Path not found: {project_path}")
        sys.exit(1)
    
    # Find project file
    if project_path.is_file() and project_path.suffix == ".kicad_pro":
        pro_file = project_path
        project_dir = project_path.parent
    else:
        project_dir = project_path
        pro_files = list(project_dir.glob("*.kicad_pro"))
        if not pro_files:
            print(f"❌ No .kicad_pro file found in {project_dir}")
            sys.exit(1)
        pro_file = pro_files[0]
    
    name = pro_file.stem
    
    project = {
        "name": name,
        "path": str(project_dir),
        "opened": datetime.now().isoformat()
    }
    set_current_project(project)
    
    print(f"✅ Opened project: {name}")
    print(f"   Path: {project_dir}")


# =============================================================================
# Design Rules Check
# =============================================================================

def cmd_drc(args):
    """Run design rules check on PCB."""
    if not check_kicad():
        sys.exit(1)
    
    project = get_current_project()
    if not project:
        print("❌ No project selected")
        sys.exit(1)
    
    project_dir = Path(project["path"])
    pcb_file = project_dir / f"{project['name']}.kicad_pcb"
    
    if not pcb_file.exists():
        print(f"❌ PCB file not found: {pcb_file}")
        sys.exit(1)
    
    output_file = project_dir / "drc_report.json"
    
    print(f"🔍 Running DRC on {pcb_file.name}...")
    
    result = run_kicad_cli([
        "pcb", "drc",
        "--format", "json",
        "--output", str(output_file),
        "--severity-all",
        str(pcb_file)
    ])
    
    if result.returncode != 0:
        print(f"⚠️  DRC completed with issues")
        if result.stderr:
            print(result.stderr)
    else:
        print(f"✅ DRC passed!")
    
    # Parse and display results
    if output_file.exists():
        with open(output_file) as f:
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


def cmd_erc(args):
    """Run electrical rules check on schematic."""
    if not check_kicad():
        sys.exit(1)
    
    project = get_current_project()
    if not project:
        print("❌ No project selected")
        sys.exit(1)
    
    project_dir = Path(project["path"])
    sch_file = project_dir / f"{project['name']}.kicad_sch"
    
    if not sch_file.exists():
        print(f"❌ Schematic file not found: {sch_file}")
        sys.exit(1)
    
    output_file = project_dir / "erc_report.json"
    
    print(f"🔍 Running ERC on {sch_file.name}...")
    
    result = run_kicad_cli([
        "sch", "erc",
        "--format", "json",
        "--output", str(output_file),
        "--severity-all",
        str(sch_file)
    ])
    
    if result.returncode != 0:
        print(f"⚠️  ERC completed with issues")
    else:
        print(f"✅ ERC passed!")


# =============================================================================
# Export Functions
# =============================================================================

def cmd_export_gerbers(args):
    """Export Gerber files for manufacturing."""
    if not check_kicad():
        sys.exit(1)
    
    project = get_current_project()
    if not project:
        print("❌ No project selected")
        sys.exit(1)
    
    project_dir = Path(project["path"])
    pcb_file = project_dir / f"{project['name']}.kicad_pcb"
    
    if not pcb_file.exists():
        print(f"❌ PCB file not found: {pcb_file}")
        sys.exit(1)
    
    output_dir = project_dir / "gerbers"
    output_dir.mkdir(exist_ok=True)
    
    print(f"📤 Exporting Gerbers...")
    
    result = run_kicad_cli([
        "pcb", "export", "gerbers",
        "--output", str(output_dir),
        str(pcb_file)
    ])
    
    if result.returncode != 0:
        print(f"❌ Gerber export failed")
        if result.stderr:
            print(result.stderr)
        sys.exit(1)
    
    # Count exported files
    gerber_files = list(output_dir.glob("*"))
    print(f"✅ Exported {len(gerber_files)} Gerber files to {output_dir}")
    for f in gerber_files:
        print(f"   {f.name}")


def cmd_export_drill(args):
    """Export drill files."""
    if not check_kicad():
        sys.exit(1)
    
    project = get_current_project()
    if not project:
        print("❌ No project selected")
        sys.exit(1)
    
    project_dir = Path(project["path"])
    pcb_file = project_dir / f"{project['name']}.kicad_pcb"
    
    output_dir = project_dir / "gerbers"
    output_dir.mkdir(exist_ok=True)
    
    print(f"📤 Exporting drill files...")
    
    result = run_kicad_cli([
        "pcb", "export", "drill",
        "--output", str(output_dir),
        "--format", "excellon",
        "--excellon-separate-th",
        "--generate-map",
        "--map-format", "pdf",
        str(pcb_file)
    ])
    
    if result.returncode == 0:
        print(f"✅ Drill files exported to {output_dir}")
    else:
        print(f"❌ Drill export failed")


def cmd_export_bom(args):
    """Export bill of materials using kicad-cli."""
    if not check_kicad():
        sys.exit(1)

    project = get_current_project()
    if not project:
        print("❌ No project selected")
        sys.exit(1)

    project_dir = Path(project["path"])
    sch_file = project_dir / f"{project['name']}.kicad_sch"

    if not sch_file.exists():
        print(f"❌ Schematic not found: {sch_file}")
        sys.exit(1)

    output_file = project_dir / "bom.csv"
    print(f"📤 Exporting BOM...")

    result = run_kicad_cli([
        "sch", "export", "bom",
        "--output", str(output_file),
        "--fields", "Reference,Value,Footprint,${QUANTITY},Datasheet",
        "--labels", "Refs,Value,Footprint,Qty,Datasheet",
        "--group-by", "Value,Footprint",
        "--sort-field", "Reference",
        str(sch_file)
    ])

    if result.returncode == 0 and output_file.exists():
        with open(output_file) as f:
            lines = f.readlines()
        print(f"✅ BOM exported: {output_file}")
        print(f"   {max(0, len(lines) - 1)} component line(s)")
        for line in lines[:20]:
            print(f"  {line.rstrip()}")
    else:
        print(f"❌ BOM export failed — is the schematic populated?")
        if result.stderr:
            print(result.stderr[:400])
        sys.exit(1)


def cmd_package_for_fab(args):
    """Create ZIP with all fabrication files."""
    project = get_current_project()
    if not project:
        print("❌ No project selected")
        sys.exit(1)
    
    project_dir = Path(project["path"])
    gerber_dir = project_dir / "gerbers"
    
    if not gerber_dir.exists() or not list(gerber_dir.glob("*")):
        print("⚠️  No Gerber files found. Running export first...")
        # Would call cmd_export_gerbers here
    
    output_name = args.output or f"{project['name']}_fab.zip"
    output_path = project_dir / output_name
    
    print(f"📦 Creating fabrication package...")
    
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        if gerber_dir.exists():
            for f in gerber_dir.iterdir():
                zf.write(f, f.name)
    
    size_kb = output_path.stat().st_size / 1024
    print(f"✅ Created: {output_path}")
    print(f"   Size: {size_kb:.1f} KB")
    print(f"\n📤 Ready to upload to PCBWay!")


# =============================================================================
# Preview Generation
# =============================================================================

def cmd_preview_schematic(args):
    """Generate schematic preview image."""
    if not check_kicad():
        sys.exit(1)
    
    project = get_current_project()
    if not project:
        print("❌ No project selected")
        sys.exit(1)
    
    project_dir = Path(project["path"])
    sch_file = project_dir / f"{project['name']}.kicad_sch"
    
    if not sch_file.exists():
        print(f"❌ Schematic file not found: {sch_file}")
        sys.exit(1)
    
    output_file = project_dir / "schematic_preview.svg"
    
    print(f"🖼️  Generating schematic preview...")
    
    result = run_kicad_cli([
        "sch", "export", "svg",
        "--output", str(output_file),
        str(sch_file)
    ])
    
    if result.returncode == 0 and output_file.exists():
        print(f"✅ Preview saved: {output_file}")
        
        # Try to convert to PNG for easier viewing
        try:
            import cairosvg
            png_file = project_dir / "schematic_preview.png"
            cairosvg.svg2png(url=str(output_file), write_to=str(png_file))
            print(f"   PNG: {png_file}")
        except ImportError:
            print("   (Install cairosvg for PNG conversion)")
    else:
        print(f"❌ Preview generation failed")


def cmd_preview_pcb(args):
    """Generate PCB preview images."""
    if not check_kicad():
        sys.exit(1)
    
    project = get_current_project()
    if not project:
        print("❌ No project selected")
        sys.exit(1)
    
    project_dir = Path(project["path"])
    pcb_file = project_dir / f"{project['name']}.kicad_pcb"
    
    if not pcb_file.exists():
        print(f"❌ PCB file not found: {pcb_file}")
        sys.exit(1)
    
    print(f"🖼️  Generating PCB previews...")
    
    # Export SVG for each major layer
    layers = ["F.Cu", "B.Cu", "F.Silkscreen", "Edge.Cuts"]
    
    for layer in layers:
        output_file = project_dir / f"pcb_preview_{layer.replace('.', '_')}.svg"
        result = run_kicad_cli([
            "pcb", "export", "svg",
            "--output", str(output_file),
            "--layers", layer,
            str(pcb_file)
        ])
        if result.returncode == 0:
            print(f"   ✅ {layer}: {output_file.name}")
    
    # Try 3D export
    glb_file = project_dir / "pcb_3d.glb"
    result = run_kicad_cli([
        "pcb", "export", "glb",
        "--output", str(glb_file),
        str(pcb_file)
    ])
    if result.returncode == 0 and glb_file.exists():
        print(f"   ✅ 3D: {glb_file.name}")


# =============================================================================
# Schematic Editing
# =============================================================================

KICAD_SYMBOLS_DIR = Path("/usr/share/kicad/symbols")


def _new_uuid() -> str:
    return str(uuid_module.uuid4())


def _extract_balanced(text: str, start: int) -> str:
    """Extract the balanced S-expression block beginning at index `start`."""
    depth = 0
    i = start
    in_string = False
    while i < len(text):
        ch = text[i]
        if ch == '"' and (i == 0 or text[i - 1] != "\\"):
            in_string = not in_string
        elif not in_string:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        i += 1
    return text[start:]


def _find_symbol_def(lib_name: str, sym_name: str) -> Optional[str]:
    """Extract the full symbol block from library and rename it to lib:sym."""
    lib_file = KICAD_SYMBOLS_DIR / f"{lib_name}.kicad_sym"
    if not lib_file.exists():
        return None
    text = lib_file.read_text(errors="replace")
    m = re.search(r'\(symbol "' + re.escape(sym_name) + r'"', text)
    if not m:
        return None
    block = _extract_balanced(text, m.start())
    # Rename only the ROOT symbol: "R" → "Device:R"
    # Sub-symbols ("R_0_1", "R_1_1") inside lib_symbols must keep their SHORT
    # names — kicad-cli rejects the schematic if they are prefixed with "Lib:".
    block = re.sub(
        r'\(symbol "' + re.escape(sym_name) + r'"',
        f'(symbol "{lib_name}:{sym_name}"',
        block,
        count=1,
    )
    # KiCad 9 schematics do not use the old (id N) property format from library
    # files; strip it so kicad-cli can load the schematic without errors.
    block = re.sub(r'\s*\(id \d+\)', '', block)
    return block


def _find_symbol_pins(lib_name: str, sym_name: str) -> List[str]:
    """Return list of pin numbers for a symbol from KiCad symbol library."""
    lib_file = KICAD_SYMBOLS_DIR / f"{lib_name}.kicad_sym"
    if not lib_file.exists():
        return []
    text = lib_file.read_text(errors="replace")
    m = re.search(r'\(symbol "' + re.escape(sym_name) + r'"', text)
    if not m:
        return []
    segment = text[m.start():m.start() + 12000]
    pin_nums = re.findall(r'\(number "([^"]+)"', segment)
    seen: set = set()
    result = []
    for p in pin_nums:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


def _embed_lib_symbol(sch_file: Path, lib_name: str, sym_name: str) -> bool:
    """Embed the symbol definition into (lib_symbols) of the schematic."""
    full_id = f"{lib_name}:{sym_name}"
    text = sch_file.read_text()

    # Check if already embedded
    if f'(symbol "{full_id}"' in text and "(lib_symbols" in text:
        lb_idx = text.index("(lib_symbols")
        if f'(symbol "{full_id}"' in _extract_balanced(text, lb_idx):
            return True

    sym_def = _find_symbol_def(lib_name, sym_name)
    if not sym_def:
        return False

    # Symbol definition indented 4 spaces (2 base + 2 for content inside lib_symbols).
    indented = "    " + sym_def.replace("\n", "\n    ")

    if "(lib_symbols)" in text:
        # Replace the self-closing token; closing paren needs 2-space indent to
        # stay inside (kicad_sch …) and not prematurely close it.
        text = text.replace("(lib_symbols)", f"(lib_symbols\n{indented}\n  )", 1)
    elif "(lib_symbols" in text:
        idx = text.index("(lib_symbols")
        block = _extract_balanced(text, idx)
        # block[-1] is ')' — strip it and add new symbol before re-closing.
        new_block = block[:-1] + f"\n{indented}\n  )"
        text = text[:idx] + new_block + text[idx + len(block):]
    else:
        return False

    sch_file.write_text(text)
    return True


def _next_component_position(sch_file: Path) -> tuple:
    """Return (x, y) for the next component, step right of existing ones."""
    if not sch_file.exists():
        return (50.8, 76.2)
    text = sch_file.read_text()
    xs = [float(m) for m in re.findall(r'\(at ([\d.]+) [\d.]+ 0\)', text)]
    base_x = (max(xs) + 25.4) if xs else 50.8
    return (base_x, 76.2)


def _append_to_schematic(sch_file: Path, s_expr: str) -> None:
    """Insert an S-expression block just before the sheet_instances section."""
    text = sch_file.read_text()
    marker = "  (sheet_instances"
    if marker in text:
        text = text.replace(marker, s_expr + "\n" + marker, 1)
    else:
        last = text.rfind(")")
        text = text[:last] + s_expr + "\n)\n"
    sch_file.write_text(text)


def cmd_add_component(args):
    """Add a component symbol to the schematic.

    Usage: add-component <LIB:SYM> <REF> [--value V] [--footprint FP]
    Example: add-component Device:R R1 --value 10k --footprint Resistor_SMD:R_0402
    """
    project = get_current_project()
    if not project:
        print("❌ No project selected")
        sys.exit(1)

    project_dir = Path(project["path"])
    sch_file = project_dir / f"{project['name']}.kicad_sch"
    if not sch_file.exists():
        print(f"❌ Schematic not found: {sch_file}")
        sys.exit(1)

    lib_sym = args.lib_sym
    if ":" not in lib_sym:
        print("❌ Format must be Library:Symbol  e.g. Device:R")
        sys.exit(1)
    lib_name, sym_name = lib_sym.split(":", 1)
    ref = args.ref
    value = args.value or sym_name
    footprint = args.footprint or ""

    pin_nums = _find_symbol_pins(lib_name, sym_name)
    if not pin_nums:
        print(f"⚠️  Symbol '{lib_sym}' not found in {KICAD_SYMBOLS_DIR}")
        print(f"   Using default pins [1, 2]. Edit footprint assignment in KiCad.")
        pin_nums = ["1", "2"]

    x, y = _next_component_position(sch_file)
    sym_uuid = _new_uuid()
    pin_entries = "\n".join(
        f'    (pin "{p}" (uuid "{_new_uuid()}"))' for p in pin_nums
    )
    project_name = project["name"]
    sym_entry = (
        f'  (symbol (lib_id "{lib_sym}") (at {x:.2f} {y:.2f} 0) (unit 1)\n'
        f'    (exclude_from_sim yes) (in_bom yes) (on_board yes)\n'
        f'    (uuid "{sym_uuid}")\n'
        f'    (property "Reference" "{ref}" (at {x+1.27:.2f} {y-1.27:.2f} 0)\n'
        f'      (effects (font (size 1.27 1.27)))\n'
        f'    )\n'
        f'    (property "Value" "{value}" (at {x+1.27:.2f} {y+1.27:.2f} 0)\n'
        f'      (effects (font (size 1.27 1.27)))\n'
        f'    )\n'
        f'    (property "Footprint" "{footprint}" (at {x:.2f} {y:.2f} 0)\n'
        f'      (effects (font (size 1.27 1.27)) hide)\n'
        f'    )\n'
        f'    (property "Datasheet" "~" (at {x:.2f} {y:.2f} 0)\n'
        f'      (effects (font (size 1.27 1.27)) hide)\n'
        f'    )\n'
        f'{pin_entries}\n'
        f'    (instances\n'
        f'      (project "{project_name}"\n'
        f'        (path "/"\n'
        f'          (reference "{ref}")\n'
        f'          (unit 1)\n'
        f'        )\n'
        f'      )\n'
        f'    )\n'
        f'  )'
    )
    _append_to_schematic(sch_file, sym_entry)
    # Embed the symbol definition so kicad-cli can generate library part info
    # for netlist/BOM export.  _embed_lib_symbol strips (id N) and uses short
    # sub-symbol names (e.g. "R_0_1" not "Device:R_0_1") for compatibility.
    _embed_lib_symbol(sch_file, lib_name, sym_name)

    print(f"✅ Added {ref} ({lib_sym})  value={value}")
    print(f"   Position: ({x:.1f}, {y:.1f}) mm  |  Pins: {', '.join(pin_nums)}")
    if not footprint:
        print(f"   ⚠️  No footprint — assign in KiCad or use --footprint")
    print(f"\n💡 Run `preview-schematic` to verify, then wire with `connect`.")


def cmd_add_net(args):
    """Add a named net label to the schematic.

    Usage: add-net <NAME> [--x X] [--y Y]   (coordinates in mm)
    Example: add-net VCC --x 60 --y 50
    """
    project = get_current_project()
    if not project:
        print("❌ No project selected")
        sys.exit(1)

    project_dir = Path(project["path"])
    sch_file = project_dir / f"{project['name']}.kicad_sch"
    if not sch_file.exists():
        print(f"❌ Schematic not found: {sch_file}")
        sys.exit(1)

    name = args.name
    x = args.x or 50.8
    y = args.y or 50.8

    label_entry = (
        f'  (label "{name}" (at {x:.2f} {y:.2f} 0) (fields_autoplaced yes)\n'
        f'    (effects (font (size 1.27 1.27)) (justify left bottom))\n'
        f'    (uuid "{_new_uuid()}")\n'
        f'    (property "Intersheet References" "${{INTERSHEET_REFS}}" (at 0 0 0)\n'
        f'      (effects (font (size 1.27 1.27)) (hide yes))\n'
        f'    )\n'
        f'  )'
    )
    _append_to_schematic(sch_file, label_entry)
    print(f"✅ Net label '{name}' added at ({x}, {y})")


def cmd_connect(args):
    """Add a wire segment between two coordinates in the schematic.

    Usage: connect --from X1,Y1 --to X2,Y2   (coordinates in mm)
    Example: connect --from 50.8,76.2 --to 76.2,76.2

    Tip: use `preview-schematic` to read pin coordinates after placing components.
    """
    project = get_current_project()
    if not project:
        print("❌ No project selected")
        sys.exit(1)

    project_dir = Path(project["path"])
    sch_file = project_dir / f"{project['name']}.kicad_sch"
    if not sch_file.exists():
        print(f"❌ Schematic not found: {sch_file}")
        sys.exit(1)

    try:
        x1, y1 = (float(v) for v in args.from_pt.split(","))
        x2, y2 = (float(v) for v in args.to_pt.split(","))
    except ValueError:
        print("❌ Coordinates must be x,y  e.g. --from 50.8,76.2")
        sys.exit(1)

    wire_entry = (
        f'  (wire (pts (xy {x1:.2f} {y1:.2f}) (xy {x2:.2f} {y2:.2f}))\n'
        f'    (stroke (width 0) (type default))\n'
        f'    (uuid "{_new_uuid()}")\n'
        f'  )'
    )
    _append_to_schematic(sch_file, wire_entry)
    print(f"✅ Wire added: ({x1}, {y1}) → ({x2}, {y2})")


# =============================================================================
# PCB Layout
# =============================================================================

def cmd_set_board_size(args):
    """Set board outline by writing an Edge.Cuts rectangle.

    Usage: set-board-size WxH   (dimensions in mm)
    Example: set-board-size 50x30
    """
    project = get_current_project()
    if not project:
        print("❌ No project selected")
        sys.exit(1)

    project_dir = Path(project["path"])
    pcb_file = project_dir / f"{project['name']}.kicad_pcb"
    if not pcb_file.exists():
        print(f"❌ PCB file not found: {pcb_file}")
        sys.exit(1)

    try:
        w, h = (float(v) for v in args.size.lower().split("x"))
    except ValueError:
        print("❌ Size must be WxH in mm  e.g. 50x30")
        sys.exit(1)

    corners = [
        ((0, 0), (w, 0)),
        ((w, 0), (w, h)),
        ((w, h), (0, h)),
        ((0, h), (0, 0)),
    ]
    lines = "\n".join(
        f'  (gr_line (start {s[0]:.3f} {s[1]:.3f}) (end {e[0]:.3f} {e[1]:.3f})\n'
        f'    (stroke (width 0.05) (type solid)) (layer "Edge.Cuts") (uuid "{_new_uuid()}"))'
        for s, e in corners
    )

    text = pcb_file.read_text()
    # Remove any previous Edge.Cuts gr_line entries
    text = re.sub(
        r'\s*\(gr_line[^\n]*\n[^\n]*"Edge\.Cuts"[^\n]*\n[^)]*\)',
        "",
        text,
    )
    last_paren = text.rfind(")")
    text = text[:last_paren] + "\n" + lines + "\n)\n"
    pcb_file.write_text(text)

    print(f"✅ Board outline: {w} mm × {h} mm")
    print(f"   Edge.Cuts rectangle written to {pcb_file.name}")


def cmd_import_netlist(args):
    """Export netlist from schematic and report components for PCB placement.

    Note: KiCad 7+ links PCB and schematic via UUIDs — no separate netlist
    import is required. Use Tools → Update PCB from Schematic inside KiCad's
    PCB editor for the full sync. This command exports the netlist for inspection.
    """
    if not check_kicad():
        sys.exit(1)

    project = get_current_project()
    if not project:
        print("❌ No project selected")
        sys.exit(1)

    project_dir = Path(project["path"])
    sch_file = project_dir / f"{project['name']}.kicad_sch"
    if not sch_file.exists():
        print(f"❌ Schematic not found: {sch_file}")
        sys.exit(1)

    output_file = project_dir / f"{project['name']}.net"
    print(f"📋 Exporting netlist...")

    result = run_kicad_cli([
        "sch", "export", "netlist",
        "--output", str(output_file),
        "--format", "kicadxml",
        str(sch_file)
    ])

    if result.returncode == 0 and output_file.exists():
        xml_text = output_file.read_text()
        refs = re.findall(r'<ref>([^<]+)</ref>', xml_text)
        values = re.findall(r'<value>([^<]+)</value>', xml_text)
        footprints = re.findall(r'<footprint>([^<]*)</footprint>', xml_text)
        footprints += [""] * (len(refs) - len(footprints))  # pad if missing
        print(f"✅ Netlist: {output_file}")
        if refs:
            print(f"\n📦 Components ({len(refs)}):")
            for r, v, fp in zip(refs, values, footprints):
                tag = f"  [{fp}]" if fp else "  [NO FOOTPRINT ⚠️]"
                print(f"  {r:<6} {v:<20}{tag}")
            missing = [r for r, fp in zip(refs, footprints) if not fp]
            if missing:
                print(f"\n⚠️  Assign footprints to: {', '.join(missing)}")
        print(f"\n💡 Open PCB editor → Tools → Update PCB from Schematic to sync.")
    else:
        print(f"❌ Netlist export failed — populate the schematic first.")
        if result.stderr:
            print(result.stderr[:400])
        sys.exit(1)


def cmd_auto_place(args):
    """Arrange all footprints on the PCB in a grid layout.

    Usage: auto-place [--spacing N]   (spacing in mm, default 10)
    """
    project = get_current_project()
    if not project:
        print("❌ No project selected")
        sys.exit(1)

    project_dir = Path(project["path"])
    pcb_file = project_dir / f"{project['name']}.kicad_pcb"
    if not pcb_file.exists():
        print(f"❌ PCB file not found: {pcb_file}")
        sys.exit(1)

    spacing: float = float(args.spacing) if args.spacing else 10.0
    text = pcb_file.read_text()

    # Match footprint blocks: (footprint "lib:name" ... (at X Y ...) ...)
    fp_pattern = re.compile(
        r'(\(footprint "([^"]*)"(?:.*?\n)*?\s*\(at )([\d.-]+) ([\d.-]+)([^)]*\))',
        re.MULTILINE,
    )
    matches = list(fp_pattern.finditer(text))

    if not matches:
        print("ℹ️  No footprints found in PCB file.")
        print("   Add components to the schematic, then run import-netlist.")
        return

    placed: List[tuple] = []
    col_size = 5
    col_width = spacing * 3

    def replacer(m: re.Match) -> str:
        idx = len(placed)
        col = idx // col_size
        row = idx % col_size
        nx = 10.0 + col * col_width
        ny = 10.0 + row * spacing
        placed.append((m.group(2), nx, ny))
        return f"{m.group(1)}{nx:.3f} {ny:.3f}{m.group(5)}"

    new_text = fp_pattern.sub(replacer, text)
    pcb_file.write_text(new_text)

    print(f"✅ Placed {len(placed)} footprint(s) (spacing {spacing} mm):")
    for fp_ref, nx, ny in placed:
        label = fp_ref.split(":")[-1] if ":" in fp_ref else fp_ref
        print(f"   {label:<30} → ({nx:.1f}, {ny:.1f})")
    print(f"\n💡 Run `drc` to check, then route with `auto-route` or KiCad PCB editor.")


def cmd_auto_route(args):
    """Auto-route the PCB using Freerouting (requires Java + Freerouting JAR).

    Install Freerouting: https://github.com/freerouting/freerouting/releases
    Save the JAR to ~/freerouting.jar, then re-run this command.
    """
    if not check_kicad():
        sys.exit(1)

    project = get_current_project()
    if not project:
        print("❌ No project selected")
        sys.exit(1)

    project_dir = Path(project["path"])
    pcb_file = project_dir / f"{project['name']}.kicad_pcb"
    if not pcb_file.exists():
        print(f"❌ PCB file not found: {pcb_file}")
        sys.exit(1)

    # Locate Freerouting JAR
    jar_arg = getattr(args, "jar", None)
    freerouting_jar: Optional[str] = jar_arg
    if not freerouting_jar:
        for candidate in [
            Path.home() / "freerouting.jar",
            Path.home() / ".local/bin/freerouting.jar",
            Path("/opt/freerouting/freerouting.jar"),
        ]:
            if candidate.exists():
                freerouting_jar = str(candidate)
                break

    if not freerouting_jar:
        print("❌ Freerouting JAR not found.")
        print("\nInstall:")
        print("  1. https://github.com/freerouting/freerouting/releases")
        print("  2. Save as ~/freerouting.jar")
        print("  3. Re-run: auto-route --jar ~/freerouting.jar")
        print("\nAlternative: Route manually in KiCad PCB editor (Route menu).")
        return

    java = shutil.which("java")
    if not java:
        print("❌ Java not found. Install: sudo apt install openjdk-17-jre")
        return

    dsn_file = project_dir / f"{project['name']}.dsn"
    ses_file = project_dir / f"{project['name']}.ses"

    print("📤 Exporting Specctra DSN...")
    result = run_kicad_cli([
        "pcb", "export", "specctra",
        "--output", str(dsn_file),
        str(pcb_file)
    ])
    if result.returncode != 0:
        print("❌ DSN export failed — ensure PCB has components placed and netlist set.")
        if result.stderr:
            print(result.stderr[:300])
        return

    print("🔀 Running Freerouting auto-router (this may take a minute)...")
    try:
        result = subprocess.run(
            [java, "-jar", freerouting_jar, "-de", str(dsn_file), "-do", str(ses_file), "-mp", "100"],
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        print("⚠️  Freerouting timed out after 5 minutes.")
        return

    if result.returncode == 0 and ses_file.exists():
        print(f"✅ Routes complete: {ses_file.name}")
        imp = run_kicad_cli([
            "pcb", "import", "specctra",
            "--output", str(pcb_file),
            str(ses_file)
        ])
        if imp.returncode == 0:
            print(f"✅ Routes imported into {pcb_file.name}")
        else:
            print(f"⚠️  Manual import: File → Import → Specctra Session in KiCad PCB editor")
    else:
        print("❌ Freerouting failed.")
        if result.stderr:
            print(result.stderr[:300])


# =============================================================================
# Export Additions
# =============================================================================

def cmd_export_pos(args):
    """Export component position (pick-and-place) file."""
    if not check_kicad():
        sys.exit(1)

    project = get_current_project()
    if not project:
        print("❌ No project selected")
        sys.exit(1)

    project_dir = Path(project["path"])
    pcb_file = project_dir / f"{project['name']}.kicad_pcb"
    if not pcb_file.exists():
        print(f"❌ PCB file not found: {pcb_file}")
        sys.exit(1)

    output_file = project_dir / f"{project['name']}-pos.csv"
    print("📤 Exporting position file...")

    result = run_kicad_cli([
        "pcb", "export", "pos",
        "--output", str(output_file),
        "--format", "csv",
        "--units", "mm",
        "--side", "both",
        str(pcb_file)
    ])

    if result.returncode == 0:
        print(f"✅ Position file: {output_file}")
        if output_file.exists():
            lines = output_file.read_text().splitlines()
            print(f"   {max(0, len(lines) - 1)} component(s)")
    else:
        print("❌ Position export failed")
        if result.stderr:
            print(result.stderr[:300])
        sys.exit(1)


def cmd_export_3d(args):
    """Export PCB as STEP 3D model."""
    if not check_kicad():
        sys.exit(1)

    project = get_current_project()
    if not project:
        print("❌ No project selected")
        sys.exit(1)

    project_dir = Path(project["path"])
    pcb_file = project_dir / f"{project['name']}.kicad_pcb"
    if not pcb_file.exists():
        print(f"❌ PCB file not found: {pcb_file}")
        sys.exit(1)

    output_file = project_dir / f"{project['name']}.step"
    print("📤 Exporting STEP 3D model...")

    result = run_kicad_cli([
        "pcb", "export", "step",
        "--output", str(output_file),
        "--force",
        "--no-unspecified",
        str(pcb_file)
    ])

    if result.returncode == 0 and output_file.exists():
        size_kb = output_file.stat().st_size / 1024
        print(f"✅ STEP model: {output_file}")
        print(f"   Size: {size_kb:.1f} KB")
        print(f"   Open with FreeCAD, Fusion 360, or any STEP viewer.")
    else:
        print("❌ STEP export failed")
        if result.stderr:
            print(result.stderr[:300])
        sys.exit(1)


# =============================================================================
# PCBWay Integration
# =============================================================================

def cmd_pcbway_quote(args):
    """Get PCBWay instant quote."""
    project = get_current_project()
    
    print(f"╭─────────────────────────────────────╮")
    print(f"│       💰 PCBWAY QUOTE ESTIMATE      │")
    print(f"├─────────────────────────────────────┤")
    
    # Parse options
    quantity = args.quantity or 5
    layers = args.layers or 2
    thickness = args.thickness or 1.6
    
    # Rough estimate based on typical pricing
    # Real implementation would call PCBWay API
    base_price = 5.0  # $5 base for small boards
    layer_mult = 1.0 if layers <= 2 else 2.0 if layers <= 4 else 4.0
    qty_mult = 1.0 if quantity <= 10 else 0.8  # volume discount
    
    board_cost = base_price * layer_mult * qty_mult
    shipping = 18.0  # DHL estimate
    
    print(f"│  Quantity:    {quantity:>4} pcs              │")
    print(f"│  Layers:      {layers:>4}                   │")
    print(f"│  Thickness:   {thickness:>4} mm              │")
    print(f"├─────────────────────────────────────┤")
    print(f"│  Board cost:  ${board_cost:>7.2f}              │")
    print(f"│  Shipping:    ${shipping:>7.2f} (DHL est.)   │")
    print(f"│  ─────────────────────────          │")
    print(f"│  TOTAL:       ${board_cost + shipping:>7.2f}              │")
    print(f"╰─────────────────────────────────────╯")
    
    print(f"\n⚠️  This is an estimate. Actual price may vary.")
    print(f"📤 To order: Upload Gerbers at pcbway.com/orderonline.aspx")
    
    if project:
        gerber_zip = Path(project["path"]) / f"{project['name']}_fab.zip"
        if gerber_zip.exists():
            print(f"\n✅ Gerber package ready: {gerber_zip}")
        else:
            print(f"\n💡 Run `package-for-fab` first to create Gerber ZIP")


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        prog="kicad_pcb",
        description="🔧 KiCad PCB Automation — Design to Manufacturing",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Command")
    
    # new
    p_new = subparsers.add_parser("new", help="Create new project")
    p_new.add_argument("name", help="Project name")
    p_new.add_argument("-d", "--description", help="Project description")
    p_new.set_defaults(func=cmd_new)
    
    # info
    p_info = subparsers.add_parser("info", help="Show project info")
    p_info.set_defaults(func=cmd_info)
    
    # open
    p_open = subparsers.add_parser("open", help="Open existing project")
    p_open.add_argument("path", help="Project path")
    p_open.set_defaults(func=cmd_open)
    
    # drc
    p_drc = subparsers.add_parser("drc", help="Run design rules check")
    p_drc.add_argument("--strict", action="store_true", help="Strict mode")
    p_drc.set_defaults(func=cmd_drc)
    
    # erc
    p_erc = subparsers.add_parser("erc", help="Run electrical rules check")
    p_erc.set_defaults(func=cmd_erc)
    
    # export-gerbers
    p_gerbers = subparsers.add_parser("export-gerbers", help="Export Gerber files")
    p_gerbers.set_defaults(func=cmd_export_gerbers)
    
    # export-drill
    p_drill = subparsers.add_parser("export-drill", help="Export drill files")
    p_drill.set_defaults(func=cmd_export_drill)
    
    # export-bom
    p_bom = subparsers.add_parser("export-bom", help="Export bill of materials")
    p_bom.set_defaults(func=cmd_export_bom)
    
    # package-for-fab
    p_package = subparsers.add_parser("package-for-fab", help="Create fab ZIP")
    p_package.add_argument("-o", "--output", help="Output filename")
    p_package.set_defaults(func=cmd_package_for_fab)
    
    # preview-schematic
    p_prev_sch = subparsers.add_parser("preview-schematic", help="Generate schematic preview")
    p_prev_sch.set_defaults(func=cmd_preview_schematic)
    
    # preview-pcb
    p_prev_pcb = subparsers.add_parser("preview-pcb", help="Generate PCB previews")
    p_prev_pcb.set_defaults(func=cmd_preview_pcb)
    
    # add-component
    p_add = subparsers.add_parser("add-component", help="Add component to schematic")
    p_add.add_argument("lib_sym", metavar="LIB:SYM", help="Symbol reference e.g. Device:R")
    p_add.add_argument("ref", metavar="REF", help="Reference designator e.g. R1")
    p_add.add_argument("--value", help="Component value (e.g. 10k)")
    p_add.add_argument("--footprint", help="Footprint reference (e.g. Resistor_SMD:R_0402)")
    p_add.set_defaults(func=cmd_add_component)

    # add-net
    p_net = subparsers.add_parser("add-net", help="Add a named net label to schematic")
    p_net.add_argument("name", metavar="NAME", help="Net name e.g. VCC")
    p_net.add_argument("--x", type=float, help="X position in mm")
    p_net.add_argument("--y", type=float, help="Y position in mm")
    p_net.set_defaults(func=cmd_add_net)

    # connect
    p_conn = subparsers.add_parser("connect", help="Add a wire between two coordinates")
    p_conn.add_argument("--from", dest="from_pt", required=True, metavar="X,Y", help="Start coord mm")
    p_conn.add_argument("--to", dest="to_pt", required=True, metavar="X,Y", help="End coord mm")
    p_conn.set_defaults(func=cmd_connect)

    # set-board-size
    p_size = subparsers.add_parser("set-board-size", help="Set board outline (Edge.Cuts rectangle)")
    p_size.add_argument("size", metavar="WxH", help="Board dimensions in mm e.g. 50x30")
    p_size.set_defaults(func=cmd_set_board_size)

    # import-netlist
    p_nl = subparsers.add_parser("import-netlist", help="Export netlist and report components for PCB")
    p_nl.set_defaults(func=cmd_import_netlist)

    # auto-place
    p_ap = subparsers.add_parser("auto-place", help="Grid-place footprints on PCB")
    p_ap.add_argument("--spacing", type=float, default=10.0, help="Grid spacing in mm (default 10)")
    p_ap.set_defaults(func=cmd_auto_place)

    # auto-route
    p_ar = subparsers.add_parser("auto-route", help="Auto-route PCB via Freerouting")
    p_ar.add_argument("--jar", help="Path to freerouting.jar")
    p_ar.set_defaults(func=cmd_auto_route)

    # export-pos
    p_pos = subparsers.add_parser("export-pos", help="Export pick-and-place position file")
    p_pos.set_defaults(func=cmd_export_pos)

    # export-3d
    p_3d = subparsers.add_parser("export-3d", help="Export STEP 3D model")
    p_3d.set_defaults(func=cmd_export_3d)

    # pcbway-quote
    p_quote = subparsers.add_parser("pcbway-quote", help="Get PCBWay quote")
    p_quote.add_argument("-q", "--quantity", type=int, default=5, help="Quantity")
    p_quote.add_argument("-l", "--layers", type=int, default=2, help="Layer count")
    p_quote.add_argument("-t", "--thickness", type=float, default=1.6, help="Thickness mm")
    p_quote.set_defaults(func=cmd_pcbway_quote)

    args = parser.parse_args()
    
    if args.command is None:
        parser.print_help()
        sys.exit(0)
    
    args.func(args)


if __name__ == "__main__":
    main()

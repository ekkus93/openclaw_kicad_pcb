"""Project scaffolding, file templates, and zip utility."""

from __future__ import annotations

import json
import zipfile as _zipfile
from datetime import datetime
from pathlib import Path

from ..config import PROJECTS_DIR, load_config, set_current_project
from ..errors import UserError
from ..fs import _atomic_write, _new_uuid
from ..models import ProjectRef

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_MINIMAL_PCB_TEXT = """\
(kicad_pcb (version 20230121) (generator pcbnew)
  (general (thickness 1.6))
  (paper "A4")
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (setup (pad_to_mask_clearance 0))
  (net 0 "")
)
"""


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def minimal_schematic_text() -> str:
    """Return a valid, empty KiCad schematic as a string (fresh UUID each call)."""
    return f'''(kicad_sch (version 20230121) (generator eeschema)
    (uuid "{_new_uuid()}")
    (paper "A4")
    (lib_symbols)
    (sheet_instances
        (path "/" (page "1"))
    )
)
'''


# ---------------------------------------------------------------------------
# Private helpers (used by cmd_new_from_netlist; exported for _sch_apply)
# ---------------------------------------------------------------------------


def _create_project(*, name: str, out_dir: Path | None, description: str) -> ProjectRef:
    """Create a new KiCad project directory, write seed files, and register it."""
    slug = name.replace(" ", "_")
    if out_dir is None:
        cfg = load_config()
        base = Path(cfg.get("projects_dir", PROJECTS_DIR))
    else:
        base = out_dir

    project_dir = base / slug
    if project_dir.exists():
        raise UserError(f"Project already exists: {project_dir}")

    project_dir.mkdir(parents=True, exist_ok=False)
    pro_file = project_dir / f"{slug}.kicad_pro"
    sch_file = project_dir / f"{slug}.kicad_sch"
    pcb_file = project_dir / f"{slug}.kicad_pcb"

    _atomic_write(
        pro_file,
        json.dumps(
            {
                "board": {"design_settings": {}},
                "meta": {"filename": f"{slug}.kicad_pro", "version": 1},
                "schematic": {"drawing": {}},
                "sheets": [[f"{slug}.kicad_sch", ""]],
            },
            indent=2,
        ),
        operation="new-from-netlist",
    )
    _atomic_write(
        sch_file,
        minimal_schematic_text(),
        root="kicad_sch",
        operation="new-from-netlist",
    )
    _atomic_write(
        pcb_file,
        _MINIMAL_PCB_TEXT,
        root="kicad_pcb",
        operation="new-from-netlist",
    )

    project = ProjectRef(
        name=slug,
        path=project_dir,
        created=datetime.now().isoformat(),
        description=description,
    )
    set_current_project(project)
    return project


def _create_schematic_zip(project_path: Path, dest_dir: Path, name: str) -> Path:
    """Zip all ``*.kicad_sch`` files in *project_path* into *dest_dir*/<name>_schematic.zip.

    Returns the path of the created zip file.  Existing zips with the same name
    are overwritten so that re-running ``new-from-netlist`` always reflects the
    latest generation.
    """
    sch_files = sorted(project_path.glob("*.kicad_sch"))
    zip_path = dest_dir / f"{name}_schematic.zip"
    with _zipfile.ZipFile(zip_path, "w", _zipfile.ZIP_DEFLATED) as zf:
        for sch_file in sch_files:
            zf.write(sch_file, sch_file.name)
    return zip_path

"""Project management commands: new, info, open."""

from __future__ import annotations

import json
import uuid as _uuid_module
from datetime import datetime
from pathlib import Path

from ..config import PROJECTS_DIR, get_current_project, load_config, set_current_project
from ..errors import UserError
from ..fs import _atomic_write
from ..models import ProjectRef
from ..results import InfoResult, NewProjectResult, OpenResult
from ..sexpr.builder import L, atom, string
from ..sexpr.serializer import serialize

# ---------------------------------------------------------------------------
# Skeleton-file AST builders (Phase 3.2)
# ---------------------------------------------------------------------------

# Standard KiCad layer table (version 20230121).  Each entry is
# (number, canonical_name, type, nickname_or_None).
_PCB_LAYERS: tuple[tuple[int, str, str, str | None], ...] = (
    (0, "F.Cu", "signal", None),
    (31, "B.Cu", "signal", None),
    (32, "B.Adhes", "user", "B.Adhesive"),
    (33, "F.Adhes", "user", "F.Adhesive"),
    (34, "B.Paste", "user", None),
    (35, "F.Paste", "user", None),
    (36, "B.SilkS", "user", "B.Silkscreen"),
    (37, "F.SilkS", "user", "F.Silkscreen"),
    (38, "B.Mask", "user", None),
    (39, "F.Mask", "user", None),
    (40, "Dwgs.User", "user", "User.Drawings"),
    (41, "Cmts.User", "user", "User.Comments"),
    (42, "Eco1.User", "user", "User.Eco1"),
    (43, "Eco2.User", "user", "User.Eco2"),
    (44, "Edge.Cuts", "user", None),
    (45, "Margin", "user", None),
    (46, "B.CrtYd", "user", "B.Courtyard"),
    (47, "F.CrtYd", "user", "F.Courtyard"),
    (48, "B.Fab", "user", None),
    (49, "F.Fab", "user", None),
    (50, "User.1", "user", None),
    (51, "User.2", "user", None),
)


def _build_sch_skeleton(project_uuid: str) -> str:
    """Return a minimal, parseable ``kicad_sch`` S-expression string.

    Built via the in-repo AST builder to avoid hand-written template strings and
    ensure the output is structurally valid by construction.
    """
    ast = L(
        atom("kicad_sch"),
        L(atom("version"), atom("20230121")),
        L(atom("generator"), atom("eeschema")),
        L(atom("uuid"), string(project_uuid)),
        L(atom("paper"), string("A4")),
        L(atom("lib_symbols")),
        L(
            atom("sheet_instances"),
            L(atom("path"), string("/"), L(atom("page"), string("1"))),
        ),
    )
    return serialize(ast) + "\n"


def _build_pcb_skeleton() -> str:
    """Return a minimal, parseable ``kicad_pcb`` S-expression string.

    Built via the in-repo AST builder; all 22 standard layers are generated from
    the :data:`_PCB_LAYERS` table so the layer list and the template are kept in
    sync from a single source.
    """
    layer_nodes = []
    for num, name, kind, nickname in _PCB_LAYERS:
        items: list = [atom(str(num)), string(name), atom(kind)]
        if nickname:
            items.append(string(nickname))
        layer_nodes.append(L(*items))

    ast = L(
        atom("kicad_pcb"),
        L(atom("version"), atom("20230121")),
        L(atom("generator"), atom("pcbnew")),
        L(atom("general"), L(atom("thickness"), atom("1.6"))),
        L(atom("paper"), string("A4")),
        L(atom("layers"), *layer_nodes),
        L(atom("setup"), L(atom("pad_to_mask_clearance"), atom("0"))),
        L(atom("net"), atom("0"), string("")),
    )
    return serialize(ast) + "\n"


def cmd_new(args) -> NewProjectResult:
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

    # Create empty schematic (AST-built, then validated via _atomic_write)
    sch_file = project_dir / f"{name}.kicad_sch"
    sch_content = _build_sch_skeleton(str(_uuid_module.uuid4()))
    _atomic_write(sch_file, sch_content, "kicad_sch", operation="new")

    # Create empty PCB (AST-built, then validated via _atomic_write)
    pcb_file = project_dir / f"{name}.kicad_pcb"
    pcb_content = _build_pcb_skeleton()
    _atomic_write(pcb_file, pcb_content, "kicad_pcb", operation="new")

    # Save as current project
    project = ProjectRef(
        name=name,
        path=project_dir,
        created=datetime.now().isoformat(),
        description=args.description or "",
    )
    set_current_project(project)

    return NewProjectResult(
        name=name,
        path=project_dir,
        description=args.description or "",
        files=(f"{name}.kicad_pro", f"{name}.kicad_sch", f"{name}.kicad_pcb"),
    )


def cmd_info(args) -> InfoResult:
    """Show current project info."""
    project = get_current_project()

    if not project:
        raise UserError("No project selected\n      Use: kicad_pcb.py new <name>")

    files: tuple[tuple[str, int], ...] = ()
    if project.path.exists():
        files = tuple((f.name, f.stat().st_size) for f in sorted(project.path.iterdir()))
    return InfoResult(project=project, files=files)


def cmd_open(args) -> OpenResult:
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

    project = ProjectRef(
        name=name,
        path=project_dir,
        created=datetime.now().isoformat(),
    )
    set_current_project(project)

    return OpenResult(name=name, path=project_dir)

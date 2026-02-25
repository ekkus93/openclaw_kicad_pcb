"""System health check command: doctor."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..commands.sch import KICAD_SYMBOLS_DIR
from ..config import CONFIG_DIR, PROJECTS_DIR, get_current_project
from ..errors import UserError


def cmd_doctor(args) -> None:  # noqa: PLR0912, PLR0915
    """Check system configuration and diagnose common issues."""
    overall_ok = True

    print("\U0001fa7a kicad-pcb doctor\n")

    # kicad-cli
    cli_path = shutil.which("kicad-cli")
    if cli_path:
        try:
            r = subprocess.run(
                [cli_path, "--version"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            version = (r.stdout.strip() or r.stderr.strip()).splitlines()[0]
            print(f"  \u2705 kicad-cli: {cli_path}")
            if version:
                print(f"     version: {version}")
        except Exception as exc:
            print(f"  \u26a0\ufe0f  kicad-cli found but could not query version: {exc}")
    else:
        print("  \u274c kicad-cli: not found in PATH")
        overall_ok = False

    # KiCad symbol libraries
    if KICAD_SYMBOLS_DIR.exists():
        n = sum(1 for _ in KICAD_SYMBOLS_DIR.glob("*.kicad_sym"))
        if n:
            print(f"  \u2705 Symbol libraries: {KICAD_SYMBOLS_DIR}  ({n} libs)")
        else:
            print(
                f"  \u274c Symbol libraries: directory exists"
                f" but no .kicad_sym files: {KICAD_SYMBOLS_DIR}"
            )
            overall_ok = False
    else:
        print(f"  \u274c Symbol libraries: not found at {KICAD_SYMBOLS_DIR}")
        overall_ok = False

    # Config dir
    cfg_mark = "\u2705" if CONFIG_DIR.exists() else "\u26a0\ufe0f "
    print(f"  {cfg_mark} Config dir: {CONFIG_DIR}")

    # Current project
    project = get_current_project()
    if project:
        pdir = Path(project["path"])
        pmark = "\u2705" if pdir.exists() else "\u26a0\ufe0f "
        print(f"  {pmark} Current project: {project['name']}  ({pdir})")
    else:
        print("  \u2139\ufe0f  No current project selected  (run: new <name>  or  open <path>)")

    # Projects dir writable
    try:
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        probe = PROJECTS_DIR / ".write_probe"
        probe.write_text("probe")
        probe.unlink()
        print(f"  \u2705 Projects dir writable: {PROJECTS_DIR}")
    except OSError as exc:
        print(f"  \u274c Projects dir not writable: {PROJECTS_DIR}  ({exc})")
        overall_ok = False

    # Optional tools: Java + Freerouting JAR (required for auto-route)
    java_path = shutil.which("java")
    if java_path:
        try:
            r = subprocess.run(
                [java_path, "-version"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            version_line = (r.stderr.strip() or r.stdout.strip()).splitlines()[0]
            print(f"  \u2705 java: {java_path}")
            if version_line:
                print(f"     {version_line}")
        except Exception as exc:
            print(f"  \u26a0\ufe0f  java found but could not query version: {exc}")
    else:
        print("  \u2139\ufe0f  java: not found  (auto-route command will not work)")

    freerouting_candidates = [
        Path.home() / "freerouting.jar",
        Path.home() / ".local/bin/freerouting.jar",
        Path("/opt/freerouting/freerouting.jar"),
    ]
    freerouting_jar: Path | None = next(
        (p for p in freerouting_candidates if p.exists()), None
    )
    if freerouting_jar:
        print(f"  \u2705 Freerouting JAR: {freerouting_jar}")
    else:
        print(
            "  \u2139\ufe0f  Freerouting JAR: not found  "
            "(auto-route will not work; save JAR to ~/freerouting.jar)"
        )

    print()
    if overall_ok:
        print("\u2705 All checks passed")
    else:
        raise UserError("doctor: one or more checks failed (see above)")

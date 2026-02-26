"""System health check command: doctor."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal

from ..adapters import RunnerProtocol, SubprocessRunner
from ..compat import MINIMUM_VERSION, parse_version
from ..config import CONFIG_DIR, PROJECTS_DIR, discover_symbols_dir, get_current_project
from ..results import DoctorCheckItem, DoctorResult


def cmd_doctor(args, *, runner: RunnerProtocol | None = None) -> DoctorResult:  # noqa: PLR0912, PLR0915
    """Check system configuration and diagnose common issues.

    *runner* is an optional injectable :class:`~kicad_pcb.adapters.RunnerProtocol`
    used for subprocess calls (kicad-cli version, java version).  When ``None``,
    a real :class:`~kicad_pcb.adapters.SubprocessRunner` is used.
    """
    _runner: RunnerProtocol = runner or SubprocessRunner()
    checks: list[DoctorCheckItem] = []
    overall_ok = True

    # kicad-cli
    cli_path = shutil.which("kicad-cli")
    if cli_path:
        try:
            r = _runner.run([cli_path, "--version"])
            raw = (r.stdout.strip() or r.stderr.strip()).splitlines()[0]
            version_str = raw
            try:
                ver = parse_version(raw)
                supported = ver >= MINIMUM_VERSION
                support_note = (
                    f"supported (>= {MINIMUM_VERSION})"
                    if supported
                    else f"UNSUPPORTED — minimum required: {MINIMUM_VERSION}"
                )
                detail = f"version: {ver}  [{support_note}]"
                status: Literal["ok", "warn"] = "ok" if supported else "warn"
            except ValueError:
                detail = f"version string unrecognised: {version_str!r}"
                status = "warn"
            checks.append(
                DoctorCheckItem(
                    status=status,
                    label="kicad-cli",
                    message=cli_path,
                    detail=detail,
                )
            )
            if status == "warn" and "UNSUPPORTED" in (detail or ""):
                overall_ok = False
        except Exception as exc:  # noqa: BLE001
            checks.append(
                DoctorCheckItem(
                    status="warn",
                    label="kicad-cli",
                    message=f"found but could not query version: {exc}",
                )
            )
    else:
        checks.append(
            DoctorCheckItem(status="error", label="kicad-cli", message="not found in PATH")
        )
        overall_ok = False

    # KiCad symbol libraries
    sym_dir_result = discover_symbols_dir()
    if sym_dir_result is not None:
        sym_dir = sym_dir_result.path
        n = sum(1 for _ in sym_dir.glob("*.kicad_sym"))
        if n:
            checks.append(
                DoctorCheckItem(
                    status="ok",
                    label="Symbol libraries",
                    message=str(sym_dir),
                    detail=f"{n} libs  [{sym_dir_result.source}]",
                )
            )
        else:
            checks.append(
                DoctorCheckItem(
                    status="error",
                    label="Symbol libraries",
                    message=f"directory exists but no .kicad_sym files: {sym_dir}",
                    detail=f"[{sym_dir_result.source}]",
                )
            )
            overall_ok = False
    else:
        checks.append(
            DoctorCheckItem(
                status="error",
                label="Symbol libraries",
                message="not found — set KICAD_SYMBOLS_DIR or symbols_dir in config",
            )
        )
        overall_ok = False

    # Config dir
    checks.append(
        DoctorCheckItem(
            status="ok" if CONFIG_DIR.exists() else "warn",
            label="Config dir",
            message=str(CONFIG_DIR),
        )
    )

    # Current project
    project = get_current_project()
    if project:
        pdir = project.path
        checks.append(
            DoctorCheckItem(
                status="ok" if pdir.exists() else "warn",
                label="Current project",
                message=f"{project.name}  ({pdir})",
            )
        )
    else:
        checks.append(
            DoctorCheckItem(
                status="info",
                label="Current project",
                message="No current project selected  (run: new <name>  or  open <path>)",
            )
        )

    # Projects dir writable
    try:
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        probe = PROJECTS_DIR / ".write_probe"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
        checks.append(
            DoctorCheckItem(status="ok", label="Projects dir writable", message=str(PROJECTS_DIR))
        )
    except OSError as exc:
        checks.append(
            DoctorCheckItem(
                status="error",
                label="Projects dir writable",
                message=f"{PROJECTS_DIR}  ({exc})",
            )
        )
        overall_ok = False

    # Optional tools: Java + Freerouting JAR (required for auto-route)
    java_path = shutil.which("java")
    if java_path:
        try:
            r = _runner.run([java_path, "-version"])
            version_line = (r.stderr.strip() or r.stdout.strip()).splitlines()[0]
            checks.append(
                DoctorCheckItem(
                    status="ok",
                    label="java",
                    message=java_path,
                    detail=version_line or None,
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(
                DoctorCheckItem(
                    status="warn",
                    label="java",
                    message=f"found but could not query version: {exc}",
                )
            )
    else:
        checks.append(
            DoctorCheckItem(
                status="info",
                label="java",
                message="not found  (auto-route command will not work)",
            )
        )

    freerouting_candidates = [
        Path.home() / "freerouting.jar",
        Path.home() / ".local/bin/freerouting.jar",
        Path("/opt/freerouting/freerouting.jar"),
    ]
    freerouting_jar: Path | None = next((p for p in freerouting_candidates if p.exists()), None)
    if freerouting_jar:
        checks.append(
            DoctorCheckItem(status="ok", label="Freerouting JAR", message=str(freerouting_jar))
        )
    else:
        checks.append(
            DoctorCheckItem(
                status="info",
                label="Freerouting JAR",
                message="not found  (auto-route will not work; save JAR to ~/freerouting.jar)",
            )
        )

    return DoctorResult(overall_ok=overall_ok, checks=tuple(checks))

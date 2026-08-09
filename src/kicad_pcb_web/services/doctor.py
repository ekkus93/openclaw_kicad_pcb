"""Environment and dependency diagnostic services."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from kicad_pcb.config import discover_symbols_dir
from kicad_pcb.graphviz_layout import find_dot_source

from ..schemas import DoctorCheck, DoctorResponse
from ..settings import WebSettings


def _jobs_dir_writable(path: Path) -> bool:
    """Return whether the jobs dir is writable."""

    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def run_doctor(settings: WebSettings) -> DoctorResponse:
    """Return a lightweight backend health report."""

    symbol_dir_result = discover_symbols_dir()
    symbol_dir = symbol_dir_result.path if symbol_dir_result is not None else None
    symbol_lib_count = (
        sum(1 for _ in symbol_dir.glob("*.kicad_sym"))
        if symbol_dir is not None and symbol_dir.is_dir()
        else 0
    )
    dot_result = find_dot_source()
    kicad_cli_path = shutil.which("kicad-cli")
    rsvg_convert_path = shutil.which("rsvg-convert")
    preview_ready = kicad_cli_path is not None and rsvg_convert_path is not None
    missing_preview_tools = [
        name
        for name, path in (("kicad-cli", kicad_cli_path), ("rsvg-convert", rsvg_convert_path))
        if path is None
    ]

    checks = [
        DoctorCheck(
            name="python",
            ok=sys.version_info >= (3, 11),
            detail=(
                f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
            ),
        ),
        DoctorCheck(
            name="jobs_dir",
            ok=_jobs_dir_writable(settings.jobs_dir),
            detail=str(settings.jobs_dir),
        ),
        DoctorCheck(
            name="symbols_dir",
            ok=symbol_dir is not None and symbol_lib_count > 0,
            detail=(
                f"{symbol_dir} ({symbol_lib_count} libs)"
                if symbol_dir is not None
                else "No KiCad symbol directory discovered."
            ),
        ),
        DoctorCheck(
            name="graphviz_dot",
            ok=dot_result is not None,
            detail=dot_result[0] if dot_result is not None else "Graphviz dot not found.",
        ),
        DoctorCheck(
            name="kicad_cli",
            ok=kicad_cli_path is not None,
            detail=kicad_cli_path or "kicad-cli not found; internal validation still works.",
        ),
        DoctorCheck(
            name="rsvg_convert",
            ok=rsvg_convert_path is not None,
            detail=rsvg_convert_path or "rsvg-convert not found; schematic PNG previews are unavailable.",
        ),
        DoctorCheck(
            name="preview_tooling",
            ok=preview_ready,
            detail=(
                f"Ready: kicad-cli={kicad_cli_path}; rsvg-convert={rsvg_convert_path}"
                if preview_ready
                else "Preview unavailable; missing: " + ", ".join(missing_preview_tools)
            ),
        ),
        DoctorCheck(
            name="llm_provider",
            ok=True,
            detail=(f"Provider: {settings.llm.provider}; model: {settings.llm.model or 'not set'}"),
        ),
        DoctorCheck(
            name="llm_configuration",
            ok=(
                not settings.llm.enabled
                or (
                    settings.llm.model is not None
                    and (settings.llm.provider == "openai" or settings.llm.base_url is not None)
                )
            ),
            detail=(
                "LLM disabled."
                if not settings.llm.enabled
                else (
                    f"Configured for {settings.llm.provider}; "
                    f"base URL: {settings.llm.base_url or 'default'}; "
                    f"prompt version: {settings.llm.system_prompt_version}"
                )
            ),
        ),
        DoctorCheck(
            name="llm_network_probe",
            ok=True,
            detail="Active LLM network probing is not supported; the enable setting is rejected at startup.",
        ),
    ]
    required_checks = {"python", "jobs_dir", "symbols_dir", "graphviz_dot"}
    overall_ok = all(check.ok for check in checks if check.name in required_checks)
    return DoctorResponse(ok=overall_ok, checks=checks)

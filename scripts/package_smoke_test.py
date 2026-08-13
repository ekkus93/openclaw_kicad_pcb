#!/usr/bin/env python3
"""Inspect distributions and verify the wheel serves its bundled SPA."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from configparser import ConfigParser
from email.parser import Parser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
_REQUIRED_WHEEL_SUFFIXES = (
    "kicad_pcb_web/static/spa/index.html",
    "kicad_pcb_web/static/spa/favicon.svg",
)
_FORBIDDEN_PARTS = ("/data/", "kicad_pcb_web.toml", "node_modules/", "debug_artifacts/")
_REQUIRED_RUNTIME_REQUIREMENTS = frozenset({"pydantic"})
_REQUIRED_WEB_REQUIREMENTS = frozenset(
    {"fastapi", "httpx", "jinja2", "python-multipart", "uvicorn"}
)
_REFINEMENT_CONSOLE_SCRIPT = "kicad_pcb_web.refinement_cli:main"


def _single(pattern: str) -> Path:
    matches = sorted(DIST.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one {pattern} in {DIST}, found {matches}")
    return matches[0]


def _normalized_requirement_name(requirement: str) -> str:
    name = re.split(r"[\s(;<>=!~\[]", requirement, maxsplit=1)[0]
    return name.lower().replace("_", "-")


def _assert_dependency_metadata(metadata_text: str) -> None:
    metadata = Parser().parsestr(metadata_text)
    requirements = metadata.get_all("Requires-Dist", [])
    runtime_requirements = {
        _normalized_requirement_name(requirement)
        for requirement in requirements
        if "extra ==" not in requirement
    }
    web_requirements = {
        _normalized_requirement_name(requirement)
        for requirement in requirements
        if 'extra == "web"' in requirement or "extra == 'web'" in requirement
    }

    missing_runtime = _REQUIRED_RUNTIME_REQUIREMENTS - runtime_requirements
    if missing_runtime:
        raise RuntimeError(
            f"Wheel metadata is missing runtime dependencies: {sorted(missing_runtime)}"
        )
    missing_web = _REQUIRED_WEB_REQUIREMENTS - web_requirements
    if missing_web:
        raise RuntimeError(f"Wheel metadata is missing web dependencies: {sorted(missing_web)}")


def _assert_console_scripts(entry_points_text: str) -> None:
    parser = ConfigParser()
    parser.read_string(entry_points_text)
    actual = parser.get("console_scripts", "kicad-refine", fallback=None)
    if actual != _REFINEMENT_CONSOLE_SCRIPT:
        raise RuntimeError(
            "Wheel is missing the trusted refinement console script: "
            f"expected {_REFINEMENT_CONSOLE_SCRIPT!r}, found {actual!r}"
        )


def _assert_distribution_contents(wheel: Path, sdist: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise RuntimeError(f"Expected one wheel METADATA file, found {metadata_names}")
        metadata_text = archive.read(metadata_names[0]).decode("utf-8")
        entry_points_names = [
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        ]
        if len(entry_points_names) != 1:
            raise RuntimeError(
                f"Expected one wheel entry_points.txt file, found {entry_points_names}"
            )
        entry_points_text = archive.read(entry_points_names[0]).decode("utf-8")

    _assert_dependency_metadata(metadata_text)
    _assert_console_scripts(entry_points_text)
    for suffix in _REQUIRED_WHEEL_SUFFIXES:
        if not any(name.endswith(suffix) for name in names):
            raise RuntimeError(f"Wheel is missing required package data: {suffix}")
    if not any(re.search(r"kicad_pcb_web/static/spa/assets/.+\.(js|css)$", name) for name in names):
        raise RuntimeError("Wheel contains no bundled SPA JavaScript/CSS assets")
    for forbidden in _FORBIDDEN_PARTS:
        if any(forbidden in f"/{name}" for name in names):
            raise RuntimeError(f"Wheel contains forbidden path fragment: {forbidden}")

    with tarfile.open(sdist, "r:gz") as archive:
        sdist_names = archive.getnames()
    if not any(name.endswith("src/kicad_pcb_web/static/spa/index.html") for name in sdist_names):
        raise RuntimeError("sdist is missing the bundled SPA index")
    if not any(
        re.search(r"src/kicad_pcb_web/static/spa/assets/.+\.(js|css)$", name)
        for name in sdist_names
    ):
        raise RuntimeError("sdist contains no bundled SPA JavaScript/CSS assets")
    for forbidden in _FORBIDDEN_PARTS:
        if any(forbidden in f"/{name}" for name in sdist_names):
            raise RuntimeError(f"sdist contains forbidden path fragment: {forbidden}")


def _extract_and_probe(wheel: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="kicad-pcb-wheel-smoke-") as temp:
        temp_path = Path(temp)
        install_root = temp_path / "site-packages"
        install_root.mkdir()
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(install_root)

        probe = r"""
import os
import re
from pathlib import Path

from fastapi.testclient import TestClient
import kicad_pcb_web
from kicad_pcb_web.main import app
from kicad_pcb_web.refinement_cli import main as refinement_cli_main

install_root = Path(os.environ["KICAD_PCB_SMOKE_INSTALL_ROOT"]).resolve()
package_file = Path(kicad_pcb_web.__file__).resolve()
assert package_file.is_relative_to(install_root), (package_file, install_root)
assert callable(refinement_cli_main)

client = TestClient(app)
bootstrap = client.get("/api/ui/bootstrap")
assert bootstrap.status_code == 200, bootstrap.text
index = client.get("/")
assert index.status_code == 200, index.text
assert "KiCad PCB Web App" in index.text
for route in (
    "/wizard",
    "/wizard/wiz_smoke/describe",
    "/generate-json",
    "/jobs",
    "/jobs/job_smoke",
    "/symbols",
    "/setup",
):
    response = client.get(route)
    assert response.status_code == 200, (route, response.text)
asset_paths = set(re.findall(r'(?:src|href)="([^"]+)"', index.text))
asset_paths.add("/static/spa/favicon.svg")
assert any(path.endswith(".js") for path in asset_paths), asset_paths
assert any(path.endswith(".css") for path in asset_paths), asset_paths
for asset in sorted(asset_paths):
    if not asset.startswith("/static/spa/"):
        continue
    response = client.get(asset)
    assert response.status_code == 200, (asset, response.text)
"""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(install_root)
        env["KICAD_PCB_SMOKE_INSTALL_ROOT"] = str(install_root)
        env["KICAD_PCB_WEB_DATA_DIR"] = str(temp_path / "data")
        subprocess.run([sys.executable, "-c", probe], check=True, cwd=temp_path, env=env)


def main() -> int:
    wheel = _single("*.whl")
    sdist = _single("*.tar.gz")
    _assert_distribution_contents(wheel, sdist)
    _extract_and_probe(wheel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

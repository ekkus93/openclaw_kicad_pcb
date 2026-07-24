#!/usr/bin/env python3
"""Inspect distributions and verify the installed wheel serves its bundled SPA."""

from __future__ import annotations

import os
import re
import subprocess
import tarfile
import tempfile
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
_REQUIRED_WHEEL_SUFFIXES = (
    "kicad_pcb_web/static/spa/index.html",
    "kicad_pcb_web/static/spa/favicon.svg",
)
_FORBIDDEN_PARTS = ("/data/", "kicad_pcb_web.toml", "node_modules/", "debug_artifacts/")


def _single(pattern: str) -> Path:
    matches = sorted(DIST.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one {pattern} in {DIST}, found {matches}")
    return matches[0]


def _assert_distribution_contents(wheel: Path, sdist: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
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


def _venv_python(directory: Path) -> Path:
    if os.name == "nt":
        return directory / "Scripts" / "python.exe"
    return directory / "bin" / "python"


def _install_and_probe(wheel: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="kicad-pcb-wheel-smoke-") as temp:
        temp_path = Path(temp)
        env_dir = temp_path / "venv"
        venv.EnvBuilder(with_pip=True).create(env_dir)
        python = _venv_python(env_dir)
        subprocess.run(
            [str(python), "-m", "pip", "install", f"{wheel}[web]"],
            check=True,
            cwd=temp_path,
        )
        probe = r"""
from fastapi.testclient import TestClient
from kicad_pcb_web.main import app

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
        subprocess.run([str(python), "-c", probe], check=True, cwd=temp_path)


def main() -> int:
    wheel = _single("*.whl")
    sdist = _single("*.tar.gz")
    _assert_distribution_contents(wheel, sdist)
    _install_and_probe(wheel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

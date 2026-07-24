"""Regression tests for RESTART1 repository-policy scripts."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Tests"], cwd=path, check=True)


def test_ci_configuration_contract_is_current() -> None:
    result = _run("python3", "scripts/check_ci_config.py")
    assert result.returncode == 0, result.stderr


def test_generated_tree_guard_allows_curated_fixtures_and_bundled_spa(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    fixture = tmp_path / "tests/fixtures/model_corpus/demo/source.kicad_sch"
    bundle = tmp_path / "src/kicad_pcb_web/static/spa/assets/app.js"
    fixture.parent.mkdir(parents=True)
    bundle.parent.mkdir(parents=True)
    fixture.write_text("fixture", encoding="utf-8")
    bundle.write_text("bundle", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)

    result = _run("bash", str(ROOT / "scripts/check-generated-tree.sh"), cwd=tmp_path)

    assert result.returncode == 0, result.stderr


def test_generated_tree_guard_rejects_archived_generated_output(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    generated = tmp_path / "code_review/archive/2026-06-09/generated/demo/report.json"
    generated.parent.mkdir(parents=True)
    generated.write_text("{}", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)

    result = _run("bash", str(ROOT / "scripts/check-generated-tree.sh"), cwd=tmp_path)

    assert result.returncode == 1
    assert "Forbidden generated files are tracked" in result.stderr
    assert "report.json" in result.stderr

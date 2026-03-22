"""Tests for the readability review helper script."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_review_script_module():
    test_root = Path(__file__).resolve().parent.parent
    repo_root = test_root.parent
    script_path = repo_root / "scripts" / "review_schematic_readability.py"
    spec = importlib.util.spec_from_file_location("review_schematic_readability", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_generate_review_report_writes_summary_bundle(tmp_path: Path) -> None:
    module = _load_review_script_module()
    fixture_dir = (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "readability"
        / "ne5532_headphone_amp_left_current"
    )
    regressed_fixture_dir = (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "readability"
        / "ne5532_headphone_amp_left_regressed"
    )
    symbols_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    artifacts = module.generate_review_report(
        module.ReviewRequest(
            fixture_dir=fixture_dir,
            regressed_fixture_dir=regressed_fixture_dir,
            symbols_dir=symbols_dir,
            output_dir=tmp_path / "review_bundle",
            project_name="ReviewScriptFixture",
        )
    )

    assert artifacts.output_dir.exists()
    assert artifacts.managed_schematic.exists()
    assert artifacts.root_schematic.exists()
    assert artifacts.report_json.exists()
    assert artifacts.summary_txt.exists()

    report = json.loads(artifacts.report_json.read_text(encoding="utf-8"))
    assert report["fixture_name"] == "ne5532_headphone_amp_left_current"

    assert isinstance(report["validation_warnings"], list)

    apply_codes = {warning["code"] for warning in report["apply_warnings"]}
    assert "VALIDATION_MODE_INTERNAL" in apply_codes

    metrics = report["metrics"]
    assert metrics["symbol_count"] >= 13
    assert "current_baseline_delta" in report
    assert "regressed_baseline_delta" in report

    summary = artifacts.summary_txt.read_text(encoding="utf-8")
    assert "Validation warnings" in summary
    assert "Apply warnings" in summary
    assert "Current metrics" in summary

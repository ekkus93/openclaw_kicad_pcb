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


class _FakePreviewResult:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode

    def output_text(self) -> str:
        return ""


class _FakePreviewCli:
    def export_svg_sch(self, sch_file: Path, output_file: Path) -> _FakePreviewResult:
        output_file.mkdir(parents=True, exist_ok=True)
        (output_file / f"{sch_file.stem}.svg").write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg"><text>{sch_file.name}</text></svg>',
            encoding="utf-8",
        )
        return _FakePreviewResult()


class _FailingPreviewCli:
    def export_svg_sch(self, sch_file: Path, output_file: Path) -> _FakePreviewResult:
        del sch_file, output_file
        return _FakePreviewResult(returncode=3)


def _write_fake_png(_svg_file: Path, png_file: Path) -> bool:
    png_file.write_bytes(b"fake-png")
    return True


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
        ),
        preview_cli=_FakePreviewCli(),
        png_renderer=_write_fake_png,
    )

    assert artifacts.output_dir.exists()
    assert artifacts.managed_schematic.exists()
    assert artifacts.root_schematic.exists()
    assert artifacts.report_json.exists()
    assert artifacts.summary_txt.exists()
    assert len(artifacts.preview_renders) == 2

    preview_names = {preview.name for preview in artifacts.preview_renders}
    assert preview_names == {"current_baseline_preview", "improved_output_preview"}
    for preview in artifacts.preview_renders:
        assert preview.status == "rendered"
        assert preview.svg_file is not None and preview.svg_file.exists()
        assert preview.png_file is not None and preview.png_file.exists()

    report = json.loads(artifacts.report_json.read_text(encoding="utf-8"))
    assert report["fixture_name"] == "ne5532_headphone_amp_left_current"
    assert len(report["preview_renders"]) == 2

    assert isinstance(report["validation_warnings"], list)

    apply_codes = {warning["code"] for warning in report["apply_warnings"]}
    assert "VALIDATION_MODE_INTERNAL" in apply_codes

    metrics = report["metrics"]
    assert metrics["symbol_count"] >= 13
    assert "current_baseline_delta" in report
    assert "regressed_baseline_delta" in report

    summary = artifacts.summary_txt.read_text(encoding="utf-8")
    assert "Preview renders" in summary
    assert "current_baseline_preview" in summary
    assert "improved_output_preview" in summary
    assert "Validation warnings" in summary
    assert "Apply warnings" in summary
    assert "Current metrics" in summary


def test_generate_review_report_falls_back_to_internal_svg_preview(tmp_path: Path) -> None:
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
            output_dir=tmp_path / "review_bundle_fallback",
            project_name="ReviewScriptFallbackFixture",
        ),
        preview_cli=_FailingPreviewCli(),
        png_renderer=_write_fake_png,
    )

    for preview in artifacts.preview_renders:
        assert preview.status == "rendered"
        assert preview.svg_file is not None and preview.svg_file.exists()
        assert preview.note is not None
        assert "internal schematic fallback" in preview.note

"""Phase O2 regression coverage for intentional non-fatal refinement fallbacks."""

from __future__ import annotations

import logging
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import kicad_pcb.refinement.electrical as electrical_module
import kicad_pcb.refinement.rendering as rendering_module
import kicad_pcb.refinement.transaction as transaction_module
import kicad_pcb.refinement.validation as validation_module
import kicad_pcb_web.refinement_evaluation_cli as evaluation_cli
from kicad_pcb.compat import KiCadVersion

pytestmark = pytest.mark.unit


class _CloseFailureClient:
    def close(self) -> None:
        raise RuntimeError("close failed")


def test_verification_artifact_cleanup_failure_is_warning_visible(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "candidate.xml"

    def fail_unlink(self: Path, *args: object, **kwargs: object) -> None:
        assert self == artifact
        raise OSError("cleanup failed")

    monkeypatch.setattr(Path, "unlink", fail_unlink)

    with caplog.at_level(logging.WARNING):
        electrical_module._remove_verification_artifact(artifact)

    assert "failed to remove refinement netlist verification artifact" in caplog.text


def test_atomic_promotion_directory_open_failure_is_warning_visible(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    def fail_open(*args: object, **kwargs: object) -> int:
        raise OSError("directory open failed")

    monkeypatch.setattr(transaction_module.os, "open", fail_open)

    with caplog.at_level(logging.WARNING):
        transaction_module._fsync_directory(tmp_path)

    assert "failed to open refinement directory for fsync" in caplog.text


def test_atomic_promotion_directory_fsync_failure_is_warning_visible(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(transaction_module.os, "open", lambda *args, **kwargs: 123)
    monkeypatch.setattr(transaction_module.os, "close", lambda _fd: None)

    def fail_fsync(_fd: int) -> None:
        raise OSError("directory fsync failed")

    monkeypatch.setattr(transaction_module.os, "fsync", fail_fsync)

    with caplog.at_level(logging.WARNING):
        transaction_module._fsync_directory(tmp_path)

    assert "failed to fsync refinement directory after atomic promotion" in caplog.text


def test_evaluation_cli_client_cleanup_failure_is_warning_visible(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        evaluation_cli._close_llm_client(_CloseFailureClient())  # type: ignore[arg-type]

    assert "refinement evaluation CLI LLM client cleanup failed" in caplog.text


def test_render_scratch_cleanup_failure_is_warning_visible_and_does_not_mask_success(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    class _Result:
        ok = True
        returncode = 0

    class _Adapter:
        detected_version = KiCadVersion(9, 0, 0)

        def export_svg_sch(self, _sch: Path, output: Path, *, plot_one: bool = False):
            assert plot_one
            (output / "sheet.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 297 210"></svg>',
                encoding="utf-8",
            )
            return _Result()

    class _Rasterizer:
        def rasterize(self, svg: Path, png: Path) -> None:
            root = ET.parse(svg).getroot()
            values = tuple(float(value) for value in root.attrib["viewBox"].split())
            raw_width = root.attrib.get("width")
            raw_height = root.attrib.get("height")
            width = (
                int(round(float(raw_width[:-2])))
                if raw_width is not None and raw_width.endswith("px")
                else int(round(values[2]))
            )
            height = (
                int(round(float(raw_height[:-2])))
                if raw_height is not None and raw_height.endswith("px")
                else int(round(values[3]))
            )
            png.write_bytes(
                b"\x89PNG\r\n\x1a\n"
                + b"\x00\x00\x00\x0dIHDR"
                + width.to_bytes(4, "big")
                + height.to_bytes(4, "big")
                + b"\x08\x02\x00\x00\x00"
            )

    fixture = (
        Path(__file__).parents[1]
        / "fixtures"
        / "readability"
        / "ne5532_headphone_amp_left_current"
        / "baseline_generated.kicad_sch"
    )

    def fail_rmtree(_path: Path) -> None:
        raise OSError("scratch cleanup failed")

    monkeypatch.setattr(rendering_module.shutil, "rmtree", fail_rmtree)

    with caplog.at_level(logging.WARNING):
        artifact = rendering_module.render_schematic_for_refinement(
            fixture,
            tmp_path / "out",
            adapter=_Adapter(),  # type: ignore[arg-type]
            rasterizer=_Rasterizer(),
        )

    assert artifact.png_path.is_file()
    assert "failed to clean refinement render temp directory" in caplog.text


def test_structural_validation_closes_mkstemp_descriptor_before_erc(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = (
        Path(__file__).parents[1]
        / "fixtures"
        / "readability"
        / "ne5532_headphone_amp_left_current"
        / "baseline_generated.kicad_sch"
    )
    candidate = tmp_path / "candidate.kicad_sch"
    candidate.write_bytes(fixture.read_bytes())
    observed_fd: list[int] = []
    real_mkstemp = validation_module.tempfile.mkstemp

    def tracked_mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
        fd, raw = real_mkstemp(*args, **kwargs)  # type: ignore[arg-type]
        observed_fd.append(fd)
        return fd, raw

    class _Result:
        ok = True
        returncode = 0

    class _Adapter:
        def erc(self, _candidate: Path, _report_path: Path):
            assert len(observed_fd) == 1
            with pytest.raises(OSError):
                os.fstat(observed_fd[0])
            return _Result(), {"violations": []}

    monkeypatch.setattr(validation_module.tempfile, "mkstemp", tracked_mkstemp)

    report = validation_module.validate_candidate_structure(
        candidate,
        adapter=_Adapter(),  # type: ignore[arg-type]
        work_dir=tmp_path,
    )

    assert report.passed


def test_erc_report_cleanup_failure_is_warning_visible_and_does_not_mask_report(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    fixture = (
        Path(__file__).parents[1]
        / "fixtures"
        / "readability"
        / "ne5532_headphone_amp_left_current"
        / "baseline_generated.kicad_sch"
    )
    candidate = tmp_path / "candidate.kicad_sch"
    candidate.write_bytes(fixture.read_bytes())

    class _Result:
        ok = True
        returncode = 0

    class _Adapter:
        def erc(self, _candidate: Path, report_path: Path):
            report_path.write_text("{}", encoding="utf-8")
            return _Result(), {"violations": []}

    real_unlink = Path.unlink
    erc_unlinks = 0

    def tracked_unlink(self: Path, *args: object, **kwargs: object) -> None:
        nonlocal erc_unlinks
        if self.name.startswith("refinement-erc-"):
            erc_unlinks += 1
            if erc_unlinks >= 2:
                raise OSError("cleanup failed")
        real_unlink(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "unlink", tracked_unlink)

    with caplog.at_level(logging.WARNING):
        report = validation_module.validate_candidate_structure(
            candidate,
            adapter=_Adapter(),  # type: ignore[arg-type]
            work_dir=tmp_path,
        )

    assert report.passed
    assert "failed to remove temporary refinement ERC report" in caplog.text

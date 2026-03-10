"""P5.1 structured-logging tests.

Verifies that :func:`~kicad_pcb.pipeline.mutate_and_validate_sch` and
:func:`~kicad_pcb.pipeline.mutate_and_validate_pcb` emit DEBUG-level log
records at every pipeline stage and that each record carries the expected
structured context in ``record.kicad``.

Stages covered:
  ``read``           — after file is loaded from disk
  ``mutate``         — after the mutator callable returns
  ``serialize``      — after the AST is serialised to a string
  ``parse``          — after round-trip syntax check (SYNTAX+ modes)
  ``validate.lint``  — after structural lint pass (LINT+ modes)
  ``validate.kicad`` — after kicad-cli ERC/DRC (KICAD+ modes, not tested here)
  ``write``          — after atomic write completes (skipped in dry-run mode)
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any, cast

import pytest
from kicad_pcb.pcb_doc import PcbDoc
from kicad_pcb.pipeline import (
    ValidationMode,
    _log_stage,
    mutate_and_validate_pcb,
    mutate_and_validate_sch,
)
from kicad_pcb.sch_doc import SchematicDoc

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent.parent / "fixtures" / "valid"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _KicadLogRecord(logging.LogRecord):
    """Typed view of a LogRecord that carries the ``kicad`` structured extra."""

    kicad: dict[str, Any]


LOGGER_NAME = "kicad_pcb.pipeline"


def _kicad_records(caplog: pytest.LogCaptureFixture) -> list[_KicadLogRecord]:
    """Return only records that carry the structured ``kicad`` extra dict."""
    return [cast(_KicadLogRecord, r) for r in caplog.records if hasattr(r, "kicad")]


def _stages(caplog: pytest.LogCaptureFixture) -> set[str]:
    return {r.kicad["stage"] for r in _kicad_records(caplog)}


def _sch_copy(tmp_path: Path) -> Path:
    src = FIXTURES / "minimal.kicad_sch"
    dst = tmp_path / "test.kicad_sch"
    shutil.copy(src, dst)
    return dst


def _pcb_copy(tmp_path: Path) -> Path:
    src = FIXTURES / "minimal.kicad_pcb"
    dst = tmp_path / "test.kicad_pcb"
    shutil.copy(src, dst)
    return dst


def _noop_sch(doc: SchematicDoc) -> None:
    pass


def _noop_pcb(doc: PcbDoc) -> None:
    pass


# ---------------------------------------------------------------------------
# Unit: _log_stage helper
# ---------------------------------------------------------------------------


class TestLogStageHelper:
    """Direct unit tests for the _log_stage module-level helper."""

    def test_emits_debug_record(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        path = tmp_path / "proj.kicad_sch"
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            _log_stage("read", path=path, mode=ValidationMode.LINT, operation="test-op", t0=0.0)
        assert len(caplog.records) == 1
        assert caplog.records[0].levelno == logging.DEBUG

    def test_kicad_extra_present(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        path = tmp_path / "proj.kicad_sch"
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            _log_stage("read", path=path, mode=ValidationMode.LINT, operation="op", t0=0.0)
        rec = caplog.records[0]
        assert hasattr(rec, "kicad"), "record should have 'kicad' extra attribute"
        ctx = cast(_KicadLogRecord, rec).kicad
        assert ctx["stage"] == "read"
        assert ctx["path"] == str(path)
        assert ctx["mode"] == "LINT"
        assert ctx["operation"] == "op"

    def test_elapsed_ms_is_non_negative(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = tmp_path / "proj.kicad_sch"
        t0 = time.perf_counter()
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            _log_stage("serialize", path=path, mode=ValidationMode.SYNTAX, operation=None, t0=t0)
        ctx = cast(_KicadLogRecord, caplog.records[0]).kicad
        assert ctx["elapsed_ms"] >= 0.0

    def test_operation_none_allowed(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        path = tmp_path / "proj.kicad_sch"
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            _log_stage("mutate", path=path, mode=ValidationMode.NONE, operation=None, t0=0.0)
        ctx = cast(_KicadLogRecord, caplog.records[0]).kicad
        assert ctx["operation"] is None

    def test_stage_name_in_message(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        path = tmp_path / "proj.kicad_sch"
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            _log_stage("write", path=path, mode=ValidationMode.LINT, operation=None, t0=0.0)
        assert "[write]" in caplog.records[0].getMessage()

    def test_mode_name_in_message(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        path = tmp_path / "proj.kicad_sch"
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            _log_stage("parse", path=path, mode=ValidationMode.FULL, operation=None, t0=0.0)
        assert "FULL" in caplog.records[0].getMessage()

    def test_path_name_in_message(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        path = tmp_path / "myfile.kicad_sch"
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            _log_stage("read", path=path, mode=ValidationMode.LINT, operation=None, t0=0.0)
        assert "myfile.kicad_sch" in caplog.records[0].getMessage()

    def test_all_modes_accepted(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        path = tmp_path / "p.kicad_sch"
        for mode in ValidationMode:
            with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
                _log_stage("read", path=path, mode=mode, operation=None, t0=0.0)
        assert len(caplog.records) == len(ValidationMode)


# ---------------------------------------------------------------------------
# Integration: schematic pipeline logging
# ---------------------------------------------------------------------------


class TestMutateValidateSchLogging:
    """Verify all expected log stages are emitted for .kicad_sch pipeline."""

    def test_lint_mode_emits_all_stages(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = _sch_copy(tmp_path)
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            mutate_and_validate_sch(path, _noop_sch, mode=ValidationMode.LINT, operation="test-op")
        stages = _stages(caplog)
        assert {"read", "mutate", "serialize", "parse", "validate.lint", "write"} <= stages

    def test_dry_run_skips_write_stage(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = _sch_copy(tmp_path)
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            mutate_and_validate_sch(path, _noop_sch, mode=ValidationMode.LINT, dry_run=True)
        stages = _stages(caplog)
        assert "write" not in stages

    def test_dry_run_still_emits_read_mutate_serialize(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = _sch_copy(tmp_path)

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            mutate_and_validate_sch(path, _noop_sch, mode=ValidationMode.LINT, dry_run=True)
        stages = _stages(caplog)
        assert {"read", "mutate", "serialize"} <= stages

    def test_none_mode_skips_parse_and_validate(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = _sch_copy(tmp_path)
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            mutate_and_validate_sch(path, _noop_sch, mode=ValidationMode.NONE)
        stages = _stages(caplog)
        assert "parse" not in stages
        assert "validate.lint" not in stages
        assert "read" in stages and "write" in stages

    def test_syntax_mode_emits_parse_but_not_lint(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = _sch_copy(tmp_path)
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            mutate_and_validate_sch(path, _noop_sch, mode=ValidationMode.SYNTAX)
        stages = _stages(caplog)
        assert "parse" in stages
        assert "validate.lint" not in stages

    def test_operations_appear_in_extra(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = _sch_copy(tmp_path)
        op = "my-operation"
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            mutate_and_validate_sch(path, _noop_sch, operation=op)
        recs = _kicad_records(caplog)
        assert recs, "expected at least one kicad log record"
        assert all(r.kicad["operation"] == op for r in recs)

    def test_path_in_extra(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        path = _sch_copy(tmp_path)
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            mutate_and_validate_sch(path, _noop_sch)
        recs = _kicad_records(caplog)
        assert all(r.kicad["path"] == str(path) for r in recs)

    def test_mode_in_extra(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        path = _sch_copy(tmp_path)
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            mutate_and_validate_sch(path, _noop_sch, mode=ValidationMode.FULL)
        recs = _kicad_records(caplog)
        assert all(r.kicad["mode"] == "FULL" for r in recs)

    def test_elapsed_ms_positive_on_write(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = _sch_copy(tmp_path)
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            mutate_and_validate_sch(path, _noop_sch)
        write_recs = [r for r in _kicad_records(caplog) if r.kicad["stage"] == "write"]
        assert write_recs, "write stage record expected"
        assert write_recs[0].kicad["elapsed_ms"] >= 0.0

    def test_no_records_below_debug(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Pipeline logs nothing when the effective log level is INFO or higher."""
        path = _sch_copy(tmp_path)
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            mutate_and_validate_sch(path, _noop_sch)
        assert not _kicad_records(caplog)


# ---------------------------------------------------------------------------
# Integration: PCB pipeline logging
# ---------------------------------------------------------------------------


class TestMutateValidatePcbLogging:
    """Verify all expected log stages are emitted for .kicad_pcb pipeline."""

    def test_lint_mode_emits_all_stages(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = _pcb_copy(tmp_path)
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            mutate_and_validate_pcb(path, _noop_pcb, mode=ValidationMode.LINT, operation="test-op")
        stages = _stages(caplog)
        assert {"read", "mutate", "serialize", "parse", "validate.lint", "write"} <= stages

    def test_dry_run_skips_write_stage(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = _pcb_copy(tmp_path)
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            mutate_and_validate_pcb(path, _noop_pcb, mode=ValidationMode.LINT, dry_run=True)
        assert "write" not in _stages(caplog)

    def test_none_mode_skips_parse_and_validate(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = _pcb_copy(tmp_path)

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            mutate_and_validate_pcb(path, _noop_pcb, mode=ValidationMode.NONE)
        stages = _stages(caplog)
        assert "parse" not in stages
        assert "validate.lint" not in stages

    def test_path_and_mode_in_extra(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        path = _pcb_copy(tmp_path)
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            mutate_and_validate_pcb(path, _noop_pcb, mode=ValidationMode.SYNTAX)
        recs = _kicad_records(caplog)
        assert recs
        assert all(r.kicad["path"] == str(path) for r in recs)
        assert all(r.kicad["mode"] == "SYNTAX" for r in recs)

    def test_stage_ordering_read_before_write(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """'read' record must appear before 'write' record."""
        path = _pcb_copy(tmp_path)
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            mutate_and_validate_pcb(path, _noop_pcb)
        recs = _kicad_records(caplog)
        stage_seq = [r.kicad["stage"] for r in recs]
        read_idx = stage_seq.index("read")
        write_idx = stage_seq.index("write")
        assert read_idx < write_idx


# ---------------------------------------------------------------------------
# Structured context completeness checks
# ---------------------------------------------------------------------------


class TestStructuredContextCompleteness:
    """Every emitted record must carry all four required context fields."""

    REQUIRED_KEYS = {"stage", "path", "mode", "operation", "elapsed_ms"}

    def test_sch_all_records_have_required_keys(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = _sch_copy(tmp_path)
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            mutate_and_validate_sch(path, _noop_sch)
        for rec in _kicad_records(caplog):
            missing = self.REQUIRED_KEYS - set(rec.kicad.keys())
            assert not missing, f"stage={rec.kicad['stage']} missing keys: {missing}"

    def test_pcb_all_records_have_required_keys(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = _pcb_copy(tmp_path)
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            mutate_and_validate_pcb(path, _noop_pcb)
        for rec in _kicad_records(caplog):
            missing = self.REQUIRED_KEYS - set(rec.kicad.keys())
            assert not missing, f"stage={rec.kicad['stage']} missing keys: {missing}"

    def test_elapsed_ms_is_float(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        path = _sch_copy(tmp_path)
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            mutate_and_validate_sch(path, _noop_sch)
        for rec in _kicad_records(caplog):
            assert isinstance(rec.kicad["elapsed_ms"], float), (
                f"expected float for elapsed_ms in stage {rec.kicad['stage']}"
            )

    def test_path_is_absolute_string(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = _sch_copy(tmp_path)
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            mutate_and_validate_sch(path, _noop_sch)
        for rec in _kicad_records(caplog):
            assert Path(rec.kicad["path"]).is_absolute(), (
                f"path should be absolute in stage {rec.kicad['stage']}"
            )

    def test_mode_is_string_not_int(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        path = _sch_copy(tmp_path)
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            mutate_and_validate_sch(path, _noop_sch, mode=ValidationMode.LINT)
        for rec in _kicad_records(caplog):
            assert isinstance(rec.kicad["mode"], str), "mode should be a string name, not int"

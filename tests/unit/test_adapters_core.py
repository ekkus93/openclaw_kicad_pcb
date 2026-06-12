"""Unit tests for kicad_pcb.adapters — Phase 2.3."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kicad_pcb.adapters import (
    FakeFs,
    FakeRunner,
    FsProtocol,
    RealFs,
    RunnerProtocol,
    RunResult,
    SubprocessRunner,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PCB = Path("/proj/board.kicad_pcb")
SCH = Path("/proj/board.kicad_sch")
PROJ = Path("/proj")

DRC_JSON = json.dumps({"violations": [{"severity": "error", "description": "Short circuit"}]})
EMPTY_REPORT = json.dumps({"violations": []})
BOM_CSV = "Refs,Value,Footprint,Qty,Datasheet\nR1,10k,,1,\nC1,100nF,,1,\n"
NETLIST_XML = "<netlist><net><ref>R1</ref><value>10k</value></netlist>"
POS_CSV = "Ref,Val,Package,PosX,PosY,Rot,Side\nR1,10k,,50.0,50.0,0,top\n"


def _ok_runner(**extra_responses: RunResult) -> FakeRunner:
    """FakeRunner returning ok=True for all known kicad-cli sub-commands."""
    responses: dict[str, RunResult] = {
        "pcb drc": RunResult(0, "", ""),
        "sch erc": RunResult(0, "", ""),
        "pcb export": RunResult(0, "", ""),
        "sch export": RunResult(0, "", ""),
        "pcb import": RunResult(0, "", ""),
        "--version": RunResult(0, "KiCad 8.0.0", ""),
    }
    responses.update(extra_responses)
    return FakeRunner(responses)


# ---------------------------------------------------------------------------
# RunResult
# ---------------------------------------------------------------------------


class TestRunResult:
    def test_ok_true_on_zero_returncode(self):
        assert RunResult(0, "", "").ok is True

    def test_ok_false_on_nonzero_returncode(self):
        assert RunResult(1, "", "some error").ok is False

    def test_output_text_stdout_only(self):
        r = RunResult(0, "hello", "")
        assert r.output_text() == "hello"

    def test_output_text_stderr_only(self):
        r = RunResult(1, "", "error msg")
        assert r.output_text() == "error msg"

    def test_output_text_both(self):
        r = RunResult(1, "out", "err")
        assert "out" in r.output_text()
        assert "err" in r.output_text()

    def test_output_text_empty(self):
        assert RunResult(0, "", "").output_text() == ""

    def test_frozen_immutable(self):
        r = RunResult(0, "a", "b")
        with pytest.raises((AttributeError, TypeError)):
            r.returncode = 1  # type: ignore[misc]


# ---------------------------------------------------------------------------
# FakeRunner
# ---------------------------------------------------------------------------


class TestFakeRunner:
    def test_records_calls(self):
        runner = FakeRunner()
        runner.run(["kicad-cli", "pcb", "drc", "--format", "json"])
        assert len(runner.calls) == 1
        assert runner.calls[0][1] == "pcb"

    def test_returns_matched_response(self):
        runner = FakeRunner({"pcb drc": RunResult(0, "drc output", "")})
        r = runner.run(["kicad-cli", "pcb", "drc"])
        assert r.ok
        assert r.stdout == "drc output"

    def test_returns_default_on_no_match(self):
        runner = FakeRunner(default_result=RunResult(2, "", "unknown"))
        r = runner.run(["kicad-cli", "unknown", "command"])
        assert r.returncode == 2
        assert r.stderr == "unknown"

    def test_single_arg_key(self):
        """Keys like '--version' (single arg after binary) are matched."""
        runner = FakeRunner({"--version": RunResult(0, "KiCad 8.0", "")})
        r = runner.run(["kicad-cli", "--version"])
        assert r.stdout == "KiCad 8.0"

    def test_implements_runner_protocol(self):
        assert isinstance(FakeRunner(), RunnerProtocol)

    def test_multiple_calls_accumulated(self):
        runner = FakeRunner()
        runner.run(["kicad-cli", "pcb", "drc"])
        runner.run(["kicad-cli", "sch", "erc"])
        assert len(runner.calls) == 2


# ---------------------------------------------------------------------------
# SubprocessRunner
# ---------------------------------------------------------------------------


class TestSubprocessRunner:
    def test_runs_real_command(self):
        runner = SubprocessRunner()
        r = runner.run(["echo", "hello"])
        assert r.ok
        assert "hello" in r.stdout

    def test_failed_command_nonzero(self):
        runner = SubprocessRunner()
        r = runner.run(["false"])
        assert not r.ok

    def test_capture_false_returns_empty_strings(self):
        runner = SubprocessRunner()
        r = runner.run(["echo", "quiet"], capture=False)
        assert r.returncode == 0
        assert r.stdout == ""
        assert r.stderr == ""

    def test_missing_binary_returns_nonzero_result(self):
        runner = SubprocessRunner()
        r = runner.run(["__no_such_binary_exists_xyz__", "--version"])
        assert not r.ok
        assert r.returncode != 0
        assert "__no_such_binary_exists_xyz__" in r.stderr

    def test_missing_binary_capture_false_returns_nonzero_result(self):
        runner = SubprocessRunner()
        r = runner.run(["__no_such_binary_exists_xyz__", "--version"], capture=False)
        assert not r.ok
        assert r.returncode != 0
        assert "__no_such_binary_exists_xyz__" in r.stderr

    def test_implements_runner_protocol(self):
        assert isinstance(SubprocessRunner(), RunnerProtocol)


# ---------------------------------------------------------------------------
# FakeFs
# ---------------------------------------------------------------------------


class TestFakeFs:
    def test_write_and_read(self):
        fs = FakeFs()
        p = Path("/tmp/x.txt")
        fs.write_text(p, "hello")
        assert fs.read_text(p) == "hello"

    def test_exists_true_for_written_file(self):
        fs = FakeFs()
        p = Path("/tmp/y.txt")
        fs.write_text(p, "content")
        assert fs.exists(p)

    def test_exists_false_for_missing(self):
        fs = FakeFs()
        assert not fs.exists(Path("/tmp/missing.txt"))

    def test_exists_true_for_preloaded_dir(self):
        fs = FakeFs(dirs={"/tmp/mydir"})
        assert fs.exists(Path("/tmp/mydir"))

    def test_mkdir_adds_dir(self):
        fs = FakeFs()
        d = Path("/tmp/newdir")
        assert not fs.exists(d)
        fs.mkdir(d)
        assert fs.exists(d)

    def test_unlink_removes_file(self):
        fs = FakeFs()
        p = Path("/tmp/del.txt")
        fs.write_text(p, "bye")
        fs.unlink(p)
        assert not fs.exists(p)

    def test_unlink_missing_raises(self):
        fs = FakeFs()
        with pytest.raises(FileNotFoundError):
            fs.unlink(Path("/tmp/nope.txt"))

    def test_stat_size(self):
        fs = FakeFs()
        p = Path("/tmp/sized.txt")
        content = "hello"
        fs.write_text(p, content)
        assert fs.stat_size(p) == len(content.encode())

    def test_stat_size_missing_raises(self):
        fs = FakeFs()
        with pytest.raises(FileNotFoundError):
            fs.stat_size(Path("/tmp/none.txt"))

    def test_read_missing_raises(self):
        fs = FakeFs()
        with pytest.raises(FileNotFoundError):
            fs.read_text(Path("/tmp/absent.txt"))

    def test_glob_matches_pattern(self):
        fs = FakeFs(
            files={
                "/proj/gerbers/board.gbr": "",
                "/proj/gerbers/board-B.Cu.gbr": "",
                "/proj/other/file.txt": "",
            }
        )
        matches = fs.glob(Path("/proj/gerbers"), "*.gbr")
        assert len(matches) == 2
        assert all(str(p).endswith(".gbr") for p in matches)

    def test_glob_no_match(self):
        fs = FakeFs(files={"/proj/x.txt": ""})
        assert fs.glob(Path("/proj"), "*.gbr") == []

    def test_iterdir(self):
        fs = FakeFs(
            files={
                "/a/one.txt": "",
                "/a/two.txt": "",
                "/b/other.txt": "",
            }
        )
        result = fs.iterdir(Path("/a"))
        assert len(result) == 2
        assert all(p.parent == Path("/a") for p in result)

    def test_preloaded_files(self):
        fs = FakeFs(files={"/data/config.json": '{"key": "val"}'})
        assert fs.exists(Path("/data/config.json"))
        assert "key" in fs.read_text(Path("/data/config.json"))

    def test_implements_fs_protocol(self):
        assert isinstance(FakeFs(), FsProtocol)


# ---------------------------------------------------------------------------
# RealFs
# ---------------------------------------------------------------------------


class TestRealFs:
    def test_write_read_roundtrip(self, tmp_path: Path):
        fs = RealFs()
        p = tmp_path / "test.txt"
        fs.write_text(p, "roundtrip content")
        assert fs.read_text(p) == "roundtrip content"

    def test_exists_true(self, tmp_path: Path):
        fs = RealFs()
        p = tmp_path / "exists.txt"
        p.write_text("x")
        assert fs.exists(p)

    def test_exists_false(self, tmp_path: Path):
        fs = RealFs()
        assert not fs.exists(tmp_path / "missing.txt")

    def test_mkdir(self, tmp_path: Path):
        fs = RealFs()
        d = tmp_path / "newdir"
        fs.mkdir(d)
        assert d.is_dir()

    def test_glob(self, tmp_path: Path):
        fs = RealFs()
        (tmp_path / "a.gbr").write_text("")
        (tmp_path / "b.gbr").write_text("")
        (tmp_path / "c.txt").write_text("")
        result = fs.glob(tmp_path, "*.gbr")
        assert len(result) == 2

    def test_iterdir(self, tmp_path: Path):
        fs = RealFs()
        (tmp_path / "x.txt").write_text("")
        (tmp_path / "y.txt").write_text("")
        result = fs.iterdir(tmp_path)
        assert len(result) == 2

    def test_unlink(self, tmp_path: Path):
        fs = RealFs()
        p = tmp_path / "gone.txt"
        p.write_text("bye")
        fs.unlink(p)
        assert not p.exists()

    def test_stat_size(self, tmp_path: Path):
        fs = RealFs()
        p = tmp_path / "sized.txt"
        content = "hello world"
        p.write_text(content)
        assert fs.stat_size(p) == len(content.encode())

    def test_implements_fs_protocol(self):
        assert isinstance(RealFs(), FsProtocol)


# ---------------------------------------------------------------------------
# KicadCliAdapter — command arg sequences
# ---------------------------------------------------------------------------

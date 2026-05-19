"""Unit tests for kicad_pcb.adapters — Phase 2.3."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kicad_pcb.adapters import (
    FakeFs,
    FakeRunner,
    FsProtocol,
    KicadCliAdapter,
    RealFs,
    RunnerProtocol,
    RunResult,
    SubprocessRunner,
)
from kicad_pcb.compat import KiCadVersion
from kicad_pcb.errors import ToolError

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


class TestKicadCliAdapterArgs:
    """Verify that adapter methods produce the correct kicad-cli arg sequences."""

    def _cli(self, **fs_files: str) -> tuple[KicadCliAdapter, FakeRunner]:
        runner = FakeRunner(
            {
                "pcb drc": RunResult(0, "", ""),
                "sch erc": RunResult(0, "", ""),
                "pcb export": RunResult(0, "", ""),
                "sch export": RunResult(0, "", ""),
                "pcb import": RunResult(0, "", ""),
                "--version": RunResult(0, "KiCad 8.0.0", ""),
            }
        )
        fs = FakeFs(files={k: v for k, v in fs_files.items()})
        return KicadCliAdapter(kicad_cli="kicad-cli", runner=runner, fs=fs), runner

    def test_version_args(self):
        cli, runner = self._cli()
        cli.version()
        assert runner.calls[-1] == ["kicad-cli", "--version"]

    def test_drc_args(self):
        cli, runner = self._cli(**{str(PROJ / "drc_report.json"): EMPTY_REPORT})
        out = PROJ / "drc_report.json"
        cli.drc(PCB, out)
        cmd = runner.calls[-1]
        assert cmd[1:3] == ["pcb", "drc"]
        assert "--format" in cmd
        assert "json" in cmd
        assert str(PCB) in cmd
        assert str(out) in cmd

    def test_erc_args(self):
        cli, runner = self._cli(**{str(PROJ / "erc_report.json"): EMPTY_REPORT})
        out = PROJ / "erc_report.json"
        cli.erc(SCH, out)
        cmd = runner.calls[-1]
        assert cmd[1:3] == ["sch", "erc"]
        assert str(SCH) in cmd

    def test_export_gerbers_args(self):
        cli, runner = self._cli()
        cli.export_gerbers(PCB, PROJ / "gerbers")
        cmd = runner.calls[-1]
        assert cmd[1:3] == ["pcb", "export"]
        assert "gerbers" in cmd

    def test_export_drill_args(self):
        cli, runner = self._cli()
        cli.export_drill(PCB, PROJ / "gerbers")
        cmd = runner.calls[-1]
        assert cmd[1:3] == ["pcb", "export"]
        assert "drill" in cmd
        assert "excellon" in cmd

    def test_export_bom_args(self):
        bom_path = str(PROJ / "bom.csv")
        cli, runner = self._cli(**{bom_path: BOM_CSV})
        cli.export_bom(SCH, PROJ / "bom.csv")
        cmd = runner.calls[-1]
        assert cmd[1:3] == ["sch", "export"]
        assert "bom" in cmd
        assert str(SCH) in cmd

    def test_export_netlist_args(self):
        net_path = str(PROJ / "board.net")
        cli, runner = self._cli(**{net_path: NETLIST_XML})
        cli.export_netlist(SCH, PROJ / "board.net")
        cmd = runner.calls[-1]
        assert cmd[1:3] == ["sch", "export"]
        assert "netlist" in cmd
        assert "kicadxml" in cmd

    def test_export_pos_args(self):
        pos_path = str(PROJ / "board-pos.csv")
        cli, runner = self._cli(**{pos_path: POS_CSV})
        cli.export_pos(PCB, PROJ / "board-pos.csv")
        cmd = runner.calls[-1]
        assert cmd[1:3] == ["pcb", "export"]
        assert "pos" in cmd

    def test_export_step_args(self):
        step_path = str(PROJ / "board.step")
        cli, runner = self._cli(**{step_path: "binary-step-data"})
        cli.export_step(PCB, PROJ / "board.step")
        cmd = runner.calls[-1]
        assert cmd[1:3] == ["pcb", "export"]
        assert "step" in cmd

    def test_export_svg_sch_args(self):
        cli, runner = self._cli()
        cli.export_svg_sch(SCH, PROJ / "preview.svg")
        cmd = runner.calls[-1]
        assert cmd[1:3] == ["sch", "export"]
        assert "svg" in cmd
        assert str(SCH) in cmd

    def test_export_svg_pcb_args(self):
        cli, runner = self._cli()
        cli.export_svg_pcb(PCB, PROJ / "layer.svg", "F.Cu")
        cmd = runner.calls[-1]
        assert cmd[1:3] == ["pcb", "export"]
        assert "svg" in cmd
        assert "F.Cu" in cmd

    def test_export_glb_args(self):
        cli, runner = self._cli()
        cli.export_glb(PCB, PROJ / "board.glb")
        cmd = runner.calls[-1]
        assert cmd[1:3] == ["pcb", "export"]
        assert "glb" in cmd

    def test_export_specctra_dsn_args(self):
        cli, runner = self._cli()
        cli.export_specctra_dsn(PCB, PROJ / "board.dsn")
        cmd = runner.calls[-1]
        assert "specctra" in cmd

    def test_import_specctra_ses_args(self):
        cli, runner = self._cli()
        cli.import_specctra_ses(PROJ / "board.ses", PCB)
        cmd = runner.calls[-1]
        assert "specctra" in cmd
        assert "import" in cmd


# ---------------------------------------------------------------------------
# KicadCliAdapter — return values
# ---------------------------------------------------------------------------


class TestKicadCliAdapterReturnValues:
    def test_drc_returns_parsed_report(self):
        runner = FakeRunner({"pcb drc": RunResult(0, "", "")})
        report_path = PROJ / "drc_report.json"
        fs = FakeFs(files={str(report_path): DRC_JSON})
        cli = KicadCliAdapter(runner=runner, fs=fs)
        result, report = cli.drc(PCB, report_path)
        assert result.ok
        assert report is not None
        assert "violations" in report
        assert len(report["violations"]) == 1

    def test_drc_missing_report_if_success_raises(self):
        runner = FakeRunner({"pcb drc": RunResult(0, "", "")})
        cli = KicadCliAdapter(runner=runner, fs=FakeFs())
        with pytest.raises(ToolError, match="Expected JSON output file was not written"):
            cli.drc(PCB, PROJ / "drc_report.json")

    def test_drc_passes_through_failure_returncode(self):
        runner = FakeRunner({"pcb drc": RunResult(1, "", "fatal error")})
        cli = KicadCliAdapter(runner=runner, fs=FakeFs())
        result, report = cli.drc(PCB, PROJ / "drc_report.json")
        assert not result.ok
        assert result.stderr == "fatal error"

    def test_erc_returns_parsed_report(self):
        runner = FakeRunner({"sch erc": RunResult(0, "", "")})
        report_path = PROJ / "erc_report.json"
        fs = FakeFs(files={str(report_path): EMPTY_REPORT})
        cli = KicadCliAdapter(runner=runner, fs=fs)
        result, report = cli.erc(SCH, report_path)
        assert result.ok
        assert report == {"violations": []}

    def test_export_bom_returns_lines(self):
        bom_path = PROJ / "bom.csv"
        runner = FakeRunner({"sch export": RunResult(0, "", "")})
        fs = FakeFs(files={str(bom_path): BOM_CSV})
        cli = KicadCliAdapter(runner=runner, fs=fs)
        result, lines = cli.export_bom(SCH, bom_path)
        assert result.ok
        assert len(lines) == 3  # header + 2 data rows

    def test_export_bom_empty_lines_on_failure(self):
        bom_path = PROJ / "bom.csv"
        runner = FakeRunner({"sch export": RunResult(1, "", "failed")})
        fs = FakeFs()
        cli = KicadCliAdapter(runner=runner, fs=fs)
        result, lines = cli.export_bom(SCH, bom_path)
        assert not result.ok
        assert lines == []

    def test_export_netlist_returns_xml(self):
        net_path = PROJ / "board.net"
        runner = FakeRunner({"sch export": RunResult(0, "", "")})
        fs = FakeFs(files={str(net_path): NETLIST_XML})
        cli = KicadCliAdapter(runner=runner, fs=fs)
        result, xml = cli.export_netlist(SCH, net_path)
        assert result.ok
        assert "R1" in xml

    def test_export_netlist_empty_on_failure(self):
        net_path = PROJ / "board.net"
        runner = FakeRunner({"sch export": RunResult(1, "", "err")})
        cli = KicadCliAdapter(runner=runner, fs=FakeFs())
        result, xml = cli.export_netlist(SCH, net_path)
        assert not result.ok
        assert xml == ""

    def test_export_gerbers_returns_file_list(self):
        gerber_dir = PROJ / "gerbers"
        runner = FakeRunner({"pcb export": RunResult(0, "", "")})
        fs = FakeFs(
            files={
                str(gerber_dir / "board.gbr"): "",
                str(gerber_dir / "board-B.Cu.gbr"): "",
            }
        )
        cli = KicadCliAdapter(runner=runner, fs=fs)
        result, files = cli.export_gerbers(PCB, gerber_dir)
        assert result.ok
        assert len(files) == 2

    def test_export_gerbers_empty_list_on_failure(self):
        runner = FakeRunner({"pcb export": RunResult(1, "", "fail")})
        cli = KicadCliAdapter(runner=runner, fs=FakeFs())
        result, files = cli.export_gerbers(PCB, PROJ / "gerbers")
        assert not result.ok
        assert files == []

    def test_export_step_returns_size(self):
        step_path = PROJ / "board.step"
        content = "A" * 500
        runner = FakeRunner({"pcb export": RunResult(0, "", "")})
        fs = FakeFs(files={str(step_path): content})
        cli = KicadCliAdapter(runner=runner, fs=fs, version=KiCadVersion(9, 0, 0))
        result, size = cli.export_step(PCB, step_path)
        assert result.ok
        assert size == 500

    def test_export_step_size_zero_if_not_written(self):
        runner = FakeRunner({"pcb export": RunResult(0, "", "")})
        cli = KicadCliAdapter(runner=runner, fs=FakeFs(), version=KiCadVersion(9, 0, 0))
        result, size = cli.export_step(PCB, PROJ / "board.step")
        assert result.ok
        assert size == 0

    def test_export_step_size_zero_on_stat_race_missing_file(self):
        step_path = PROJ / "board.step"
        runner = FakeRunner({"pcb export": RunResult(0, "", "")})

        class _RaceFs(FakeFs):
            def exists(self, path: Path) -> bool:
                if path == step_path:
                    return True
                return super().exists(path)

            def stat_size(self, path: Path) -> int:
                if path == step_path:
                    raise FileNotFoundError("simulated concurrent delete")
                return super().stat_size(path)

        cli = KicadCliAdapter(runner=runner, fs=_RaceFs(), version=KiCadVersion(9, 0, 0))
        result, size = cli.export_step(PCB, step_path)
        assert result.ok
        assert size == 0

    def test_export_step_non_race_stat_error_raises_tool_error(self):
        step_path = PROJ / "board.step"
        runner = FakeRunner({"pcb export": RunResult(0, "", "")})

        class _BrokenStatFs(FakeFs):
            def exists(self, path: Path) -> bool:
                if path == step_path:
                    return True
                return super().exists(path)

            def stat_size(self, path: Path) -> int:
                if path == step_path:
                    raise PermissionError("simulated stat permission denied")
                return super().stat_size(path)

        cli = KicadCliAdapter(runner=runner, fs=_BrokenStatFs(), version=KiCadVersion(9, 0, 0))
        with pytest.raises(ToolError, match="Failed to stat STEP export output"):
            cli.export_step(PCB, step_path)

    def test_export_pos_returns_lines(self):
        pos_path = PROJ / "board-pos.csv"
        runner = FakeRunner({"pcb export": RunResult(0, "", "")})
        fs = FakeFs(files={str(pos_path): POS_CSV})
        cli = KicadCliAdapter(runner=runner, fs=fs)
        result, lines = cli.export_pos(PCB, pos_path)
        assert result.ok
        assert len(lines) == 2  # header + 1 data row

    def test_version_returns_stdout(self):
        runner = FakeRunner({"--version": RunResult(0, "KiCad 8.0.0", "")})
        cli = KicadCliAdapter(runner=runner, fs=FakeFs())
        result = cli.version()
        assert result.ok
        assert "KiCad" in result.stdout

    def test_drc_invalid_json_raises(self):
        """Malformed JSON in output file must raise ToolError (fail-fast)."""
        report_path = PROJ / "drc_report.json"
        runner = FakeRunner({"pcb drc": RunResult(0, "", "")})
        fs = FakeFs(files={str(report_path): "not valid json{"})
        cli = KicadCliAdapter(runner=runner, fs=fs)
        with pytest.raises(ToolError, match="Malformed JSON output"):
            cli.drc(PCB, report_path)

    def test_export_bom_missing_output_if_success_raises(self):
        bom_path = PROJ / "bom.csv"
        runner = FakeRunner({"sch export": RunResult(0, "", "")})
        cli = KicadCliAdapter(runner=runner, fs=FakeFs())
        with pytest.raises(ToolError, match="Expected output file was not written"):
            cli.export_bom(SCH, bom_path)

    def test_export_netlist_missing_output_if_success_raises(self):
        net_path = PROJ / "board.net"
        runner = FakeRunner({"sch export": RunResult(0, "", "")})
        cli = KicadCliAdapter(runner=runner, fs=FakeFs())
        with pytest.raises(ToolError, match="Expected output file was not written"):
            cli.export_netlist(SCH, net_path)

    def test_export_pos_missing_output_if_success_raises(self):
        pos_path = PROJ / "board-pos.csv"
        runner = FakeRunner({"pcb export": RunResult(0, "", "")})
        cli = KicadCliAdapter(runner=runner, fs=FakeFs())
        with pytest.raises(ToolError, match="Expected output file was not written"):
            cli.export_pos(PCB, pos_path)

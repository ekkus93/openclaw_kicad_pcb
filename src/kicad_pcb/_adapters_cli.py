"""KicadCliAdapter — typed, injectable wrapper for all kicad-cli sub-commands."""

from __future__ import annotations

import json
from pathlib import Path

from ._adapters_fs import FsProtocol, RealFs
from ._adapters_runner import RunnerProtocol, RunResult, SubprocessRunner
from .compat import CliCapability, KiCadVersion, parse_version
from .compat import require_capability as _check_capability
from .errors import ToolError

_NETLIST_FORMATS = frozenset({"kicadxml", "kicadsexpr"})


class KicadCliAdapter:
    """Typed, injectable adapter for all ``kicad-cli`` sub-commands.

    Inject *runner* and *fs* for unit testing without a real KiCad
    installation or real filesystem::

        fake_runner = FakeRunner({"pcb drc": RunResult(0, "", "")})
        fake_fs = FakeFs({"/tmp/proj/drc_report.json": '{"violations": []}'})
        cli = KicadCliAdapter(runner=fake_runner, fs=fake_fs)
        result, report = cli.drc(
            Path("/tmp/proj/board.kicad_pcb"),
            Path("/tmp/proj/drc_report.json"),
        )
        assert result.ok
        assert report == {"violations": []}
    """

    def __init__(
        self,
        kicad_cli: str = "kicad-cli",
        *,
        runner: RunnerProtocol | None = None,
        fs: FsProtocol | None = None,
        version: KiCadVersion | None = None,
    ) -> None:
        self._cli = kicad_cli
        self._runner: RunnerProtocol = runner or SubprocessRunner()
        self._fs: FsProtocol = fs or RealFs()
        # Optionally injected for tests; populated lazily on first access otherwise.
        self._detected_version: KiCadVersion | None = version

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self, args: list[str], *, capture: bool = True) -> RunResult:
        return self._runner.run([self._cli, *args], capture=capture)

    def _read_json(self, path: Path, *, required: bool = False) -> dict | None:
        """Read *path* as JSON via the injected fs.

        Returns ``None`` only when the file is optional and missing.
        Raises :class:`ToolError` for malformed/unreadable required JSON.
        """
        if not self._fs.exists(path):
            if required:
                raise ToolError(f"Expected JSON output file was not written: {path}")
            return None
        try:
            parsed = json.loads(self._fs.read_text(path))
        except json.JSONDecodeError as exc:
            raise ToolError(f"Malformed JSON output at {path}: {exc}") from exc
        except OSError as exc:
            raise ToolError(f"Failed to read JSON output at {path}: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ToolError(f"Invalid JSON output at {path}: expected object root")
        return parsed

    def _read_text_output(self, path: Path, *, required: bool = False) -> str:
        """Read *path* as text via the injected fs.

        Returns ``""`` only when the file is optional and missing.
        Raises :class:`ToolError` for missing/unreadable required output.
        """
        if not self._fs.exists(path):
            if required:
                raise ToolError(f"Expected output file was not written: {path}")
            return ""
        try:
            return self._fs.read_text(path)
        except OSError as exc:
            raise ToolError(f"Failed to read output file {path}: {exc}") from exc

    # ------------------------------------------------------------------
    # Version
    # ------------------------------------------------------------------

    @property
    def detected_version(self) -> KiCadVersion | None:
        """Lazily detect and cache the installed kicad-cli version.

        Returns ``None`` when the version output is empty or unparseable.
        Runtime failures while invoking ``kicad-cli --version`` are propagated
        to the caller.
        """
        if self._detected_version is None:
            r = self._run(["--version"])
            output = r.stdout.strip() or r.stderr.strip()
            if output:
                raw = output.splitlines()[0]
                try:
                    self._detected_version = parse_version(raw)
                except ValueError:
                    self._detected_version = None
        return self._detected_version

    def require_capability(self, cap: CliCapability) -> None:
        """Raise :class:`~kicad_pcb.errors.ToolError` if the installed
        kicad-cli does not support *cap*.

        Delegates to :func:`~kicad_pcb.compat.require_capability` with the
        lazily-detected version.
        """
        _check_capability(self.detected_version, cap)

    def version(self) -> RunResult:
        """Return kicad-cli ``--version`` output."""
        return self._run(["--version"])

    # ------------------------------------------------------------------
    # Validation commands
    # ------------------------------------------------------------------

    def drc(self, pcb_file: Path, output_file: Path) -> tuple[RunResult, dict | None]:
        """Run PCB design rules check.

        Returns ``(RunResult, report_dict | None)``.  *report_dict* is the
        parsed JSON report if kicad-cli wrote it; ``None`` otherwise.
        """
        result = self._run(
            [
                "pcb",
                "drc",
                "--format",
                "json",
                "--output",
                str(output_file),
                "--severity-all",
                str(pcb_file),
            ]
        )
        report = self._read_json(output_file, required=result.ok)
        return result, report

    def erc(self, sch_file: Path, output_file: Path) -> tuple[RunResult, dict | None]:
        """Run schematic electrical rules check.

        Returns ``(RunResult, report_dict | None)``.
        """
        result = self._run(
            [
                "sch",
                "erc",
                "--format",
                "json",
                "--output",
                str(output_file),
                "--severity-all",
                str(sch_file),
            ]
        )
        report = self._read_json(output_file, required=result.ok)
        return result, report

    # ------------------------------------------------------------------
    # Export commands
    # ------------------------------------------------------------------

    def export_gerbers(self, pcb_file: Path, output_dir: Path) -> tuple[RunResult, list[Path]]:
        """Export Gerber files.

        Returns ``(RunResult, [written_files])``.  File list is populated via
        the injected :attr:`_fs`, enabling tests to prepopulate fake files.
        """
        self._fs.mkdir(output_dir, exist_ok=True)
        result = self._run(
            [
                "pcb",
                "export",
                "gerbers",
                "--output",
                str(output_dir),
                str(pcb_file),
            ]
        )
        files = self._fs.glob(output_dir, "*") if result.ok else []
        return result, files

    def export_drill(self, pcb_file: Path, output_dir: Path) -> RunResult:
        """Export Excellon drill files."""
        self._fs.mkdir(output_dir, exist_ok=True)
        return self._run(
            [
                "pcb",
                "export",
                "drill",
                "--output",
                str(output_dir),
                "--format",
                "excellon",
                "--excellon-separate-th",
                "--generate-map",
                "--map-format",
                "pdf",
                str(pcb_file),
            ]
        )

    def export_bom(self, sch_file: Path, output_file: Path) -> tuple[RunResult, list[str]]:
        """Export bill of materials as CSV.

        Returns ``(RunResult, csv_lines)``.
        """
        result = self._run(
            [
                "sch",
                "export",
                "bom",
                "--output",
                str(output_file),
                "--fields",
                "Reference,Value,Footprint,${QUANTITY},Datasheet",
                "--labels",
                "Refs,Value,Footprint,Qty,Datasheet",
                "--group-by",
                "Value,Footprint",
                "--sort-field",
                "Reference",
                str(sch_file),
            ]
        )
        lines: list[str] = []
        if result.ok:
            content = self._read_text_output(output_file, required=True)
            if content:
                lines = content.splitlines()
        return result, lines

    def export_netlist(
        self,
        sch_file: Path,
        output_file: Path,
        *,
        netlist_format: str = "kicadxml",
    ) -> tuple[RunResult, str]:
        """Export a KiCad XML or native S-expression netlist.

        Returns ``(RunResult, content)`` where *content* is empty on failure
        or when the file was not written.
        """
        if netlist_format not in _NETLIST_FORMATS:
            raise ValueError(
                f"Unsupported KiCad netlist format {netlist_format!r}; "
                f"expected one of {sorted(_NETLIST_FORMATS)}"
            )
        result = self._run(
            [
                "sch",
                "export",
                "netlist",
                "--output",
                str(output_file),
                "--format",
                netlist_format,
                str(sch_file),
            ]
        )
        content = ""
        if result.ok:
            content = self._read_text_output(output_file, required=True)
        return result, content

    def export_pos(self, pcb_file: Path, output_file: Path) -> tuple[RunResult, list[str]]:
        """Export pick-and-place CSV.

        Returns ``(RunResult, csv_lines)``.
        """
        result = self._run(
            [
                "pcb",
                "export",
                "pos",
                "--output",
                str(output_file),
                "--format",
                "csv",
                "--units",
                "mm",
                "--side",
                "both",
                str(pcb_file),
            ]
        )
        lines: list[str] = []
        if result.ok:
            content = self._read_text_output(output_file, required=True)
            if content:
                lines = content.splitlines()
        return result, lines

    def export_step(self, pcb_file: Path, output_file: Path) -> tuple[RunResult, int]:
        """Export STEP 3D model.

        Returns ``(RunResult, file_size_bytes)`` — *file_size_bytes* is 0
        when the file was not written.

        Requires kicad-cli >= 8.0 (``--no-unspecified`` flag).
        """
        self.require_capability(CliCapability.PCB_EXPORT_STEP_NO_UNSPECIFIED)
        result = self._run(
            [
                "pcb",
                "export",
                "step",
                "--output",
                str(output_file),
                "--force",
                "--no-unspecified",
                str(pcb_file),
            ]
        )
        size = 0
        if result.ok and self._fs.exists(output_file):
            try:
                size = self._fs.stat_size(output_file)
            except FileNotFoundError:
                size = 0
            except OSError as exc:
                raise ToolError(
                    f"Failed to stat STEP export output: {output_file} ({exc})"
                ) from exc
        return result, size

    # ------------------------------------------------------------------
    # Preview / SVG export
    # ------------------------------------------------------------------

    def export_svg_sch(
        self,
        sch_file: Path,
        output_file: Path,
        *,
        plot_one: bool = False,
    ) -> RunResult:
        """Export SVG preview of a schematic."""
        args = ["sch", "export", "svg"]
        if plot_one:
            args.extend(["--pages", "1"])
        args.extend(["--output", str(output_file), str(sch_file)])
        return self._run(args)

    def export_svg_pcb(self, pcb_file: Path, output_file: Path, layer: str) -> RunResult:
        """Export SVG preview for a single PCB layer."""
        return self._run(
            [
                "pcb",
                "export",
                "svg",
                "--output",
                str(output_file),
                "--layers",
                layer,
                str(pcb_file),
            ]
        )

    def export_glb(self, pcb_file: Path, output_file: Path) -> RunResult:
        """Export 3D GLB model.

        Requires kicad-cli >= 8.0.
        """
        self.require_capability(CliCapability.PCB_EXPORT_GLB)
        return self._run(
            [
                "pcb",
                "export",
                "glb",
                "--output",
                str(output_file),
                str(pcb_file),
            ]
        )

    # ------------------------------------------------------------------
    # Specctra (for Freerouting auto-route)
    # ------------------------------------------------------------------

    def export_specctra_dsn(self, pcb_file: Path, output_file: Path) -> RunResult:
        """Export Specctra DSN file for Freerouting."""
        return self._run(
            [
                "pcb",
                "export",
                "specctra",
                "--output",
                str(output_file),
                str(pcb_file),
            ]
        )

    def import_specctra_ses(self, ses_file: Path, output_pcb: Path) -> RunResult:
        """Import a Freerouting SES session file back into the PCB."""
        return self._run(
            [
                "pcb",
                "import",
                "specctra",
                "--output",
                str(output_pcb),
                str(ses_file),
            ]
        )

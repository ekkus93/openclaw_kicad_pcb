"""Phase 0: full pipeline smoke test (integration).

Runs the real kicad_pcb.py CLI against a temp project directory and
verifies the full new → add-component → kicad-cli netlist export pipeline
produces correct output.

Requires: kicad-cli on PATH (tests are skipped if unavailable).

Note: kicad-cli on this system is a Flatpak wrapper and can only access paths
under the user's home directory. The home_tmp fixture (see conftest.py) creates
scratch directories under ~/tmp/kicad-tests/ to satisfy this constraint.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from kicad_pcb.commands.netlist import MANAGED_SHEET_FILE
from kicad_pcb.sch_doc import SchematicDoc

pytestmark = pytest.mark.integration

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "kicad-pcb" / "scripts"
KICAD_PCB_PY = SCRIPTS_DIR / "kicad_pcb.py"
PYTHON = sys.executable


def _run_script(args: list[str], env_home: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = env_home
    return subprocess.run(
        [PYTHON, str(KICAD_PCB_PY), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


requires_kicad = pytest.mark.skipif(
    not shutil.which("kicad-cli"),
    reason="kicad-cli not found on PATH",
)


@requires_kicad
class TestFullPipeline:
    """End-to-end: create project, add components, export netlist and BOM.

    Uses home_tmp so all paths are under ~/tmp/kicad-tests/, which is
    accessible inside the kicad-cli Flatpak sandbox.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, home_tmp: Path) -> None:
        self.home = str(home_tmp)
        self.projects_dir = home_tmp / "kicad-projects"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return _run_script(list(args), self.home)

    def test_new_project_creates_files(self) -> None:
        result = self._run("new", "TestProj")
        assert result.returncode == 0, result.stderr
        project_dir = self.projects_dir / "TestProj"
        assert (project_dir / "TestProj.kicad_sch").exists()
        assert (project_dir / "TestProj.kicad_pro").exists()

    def test_add_resistor_exits_zero(self) -> None:
        self._run("new", "TestProj")
        result = self._run("add-component", "Device:R", "R1", "--value", "10k")
        assert result.returncode == 0, result.stderr

    def test_schematic_loadable_by_kicad_cli(self) -> None:
        """kicad-cli must be able to load the generated schematic (exit 0)."""
        self._run("new", "TestProj")
        self._run("add-component", "Device:R", "R1", "--value", "10k")
        self._run("add-component", "Device:C", "C1", "--value", "100nF")

        sch_path = self.projects_dir / "TestProj" / "TestProj.kicad_sch"
        out_dir = self.projects_dir / "TestProj"
        netlist_path = out_dir / "TestProj.xml"

        assert sch_path.exists(), f"Schematic not created at {sch_path}"

        kicad_cli = shutil.which("kicad-cli") or "/usr/bin/kicad-cli"
        proc = subprocess.run(
            [
                kicad_cli,
                "sch",
                "export",
                "netlist",
                "--format",
                "kicadsexpr",
                "--output",
                str(netlist_path),
                str(sch_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"kicad-cli failed (exit {proc.returncode}):\n{proc.stderr}"

    def test_netlist_contains_both_components(self) -> None:
        """Netlist must list R1 and C1 as components (Bug 4 regression)."""
        self._run("new", "TestProj")
        self._run("add-component", "Device:R", "R1", "--value", "10k")
        self._run("add-component", "Device:C", "C1", "--value", "100nF")

        sch_path = self.projects_dir / "TestProj" / "TestProj.kicad_sch"
        netlist_path = self.projects_dir / "TestProj" / "TestProj.xml"

        kicad_cli = shutil.which("kicad-cli") or "/usr/bin/kicad-cli"
        subprocess.run(
            [
                kicad_cli,
                "sch",
                "export",
                "netlist",
                "--format",
                "kicadsexpr",
                "--output",
                str(netlist_path),
                str(sch_path),
            ],
            check=True,
        )

        netlist = netlist_path.read_text()
        assert '(ref "R1")' in netlist or "(ref R1)" in netlist, "R1 not found in netlist"
        assert '(ref "C1")' in netlist or "(ref C1)" in netlist, "C1 not found in netlist"

    def test_bom_export_shows_two_components(self) -> None:
        """BOM must list both components (Bug 4 regression — empty BOM fix)."""
        self._run("new", "TestProj")
        self._run("add-component", "Device:R", "R1", "--value", "10k")
        self._run("add-component", "Device:C", "C1", "--value", "100nF")

        result = self._run("export-bom")
        assert result.returncode == 0, result.stderr
        # Script prints "N component line(s)" — assert at least 2
        output = result.stdout + result.stderr
        assert "2 component" in output or ("R1" in output and "C1" in output), (
            f"Expected 2 components in BOM, got:\n{output}"
        )

    def test_sub_symbol_names_not_prefixed(self) -> None:
        """Bug 1 regression: sub-symbol names inside lib_symbols must not get lib prefix."""
        self._run("new", "TestProj")
        self._run("add-component", "Device:R", "R1", "--value", "10k")

        sch_path = self.projects_dir / "TestProj" / "TestProj.kicad_sch"
        raw = sch_path.read_text()
        assert '"Device:R_0_1"' not in raw, (
            "Bug 1 regression: sub-symbol 'R_0_1' was renamed to 'Device:R_0_1'"
        )
        assert '"Device:R_1_1"' not in raw, (
            "Bug 1 regression: sub-symbol 'R_1_1' was renamed to 'Device:R_1_1'"
        )

    def test_no_id_property_format(self) -> None:
        """Bug 2 regression: generated schematic must not contain (id N) property format."""
        self._run("new", "TestProj")
        self._run("add-component", "Device:R", "R1", "--value", "10k")

        sch_path = self.projects_dir / "TestProj" / "TestProj.kicad_sch"
        raw = sch_path.read_text()
        assert not re.search(r"\(id\s+\d+\)", raw), (
            "Bug 2 regression: schematic contains old (id N) property format"
        )

    def test_instances_block_present(self) -> None:
        """Bug 4 regression: placed symbols must contain an (instances ...) block."""
        self._run("new", "TestProj")
        self._run("add-component", "Device:R", "R1", "--value", "10k")

        sch_path = self.projects_dir / "TestProj" / "TestProj.kicad_sch"
        raw = sch_path.read_text()
        assert "(instances" in raw, "Bug 4 regression: placed symbol missing (instances ...) block"

    def test_set_board_size_pcb_drc_runs(self) -> None:
        """set-board-size → kicad-cli pcb drc loads and runs without hard error."""
        self._run("new", "TestProj")
        result = self._run("set-board-size", "50x30")
        assert result.returncode == 0, f"set-board-size failed:\n{result.stderr}"

        pcb_path = self.projects_dir / "TestProj" / "TestProj.kicad_pcb"
        assert pcb_path.exists(), "PCB file not found after set-board-size"

        drc_report = self.projects_dir / "TestProj" / "drc_report.json"
        kicad_cli = shutil.which("kicad-cli") or "/usr/bin/kicad-cli"
        proc = subprocess.run(
            [
                kicad_cli,
                "pcb",
                "drc",
                "--output",
                str(drc_report),
                "--format",
                "json",
                str(pcb_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, (
            f"kicad-cli pcb drc failed (exit {proc.returncode}):\n{proc.stderr}"
        )
        assert drc_report.exists(), "DRC report file not created"

    def test_set_board_size_pcb_gerbers_export(self) -> None:
        """set-board-size → kicad-cli pcb export gerbers produces Gerber files."""
        self._run("new", "TestProj")
        result = self._run("set-board-size", "50x30")
        assert result.returncode == 0, f"set-board-size failed:\n{result.stderr}"

        pcb_path = self.projects_dir / "TestProj" / "TestProj.kicad_pcb"
        gerbers_dir = self.projects_dir / "TestProj" / "gerbers"
        kicad_cli = shutil.which("kicad-cli") or "/usr/bin/kicad-cli"
        proc = subprocess.run(
            [
                kicad_cli,
                "pcb",
                "export",
                "gerbers",
                "--output",
                str(gerbers_dir),
                str(pcb_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, (
            f"kicad-cli pcb export gerbers failed (exit {proc.returncode}):\n{proc.stderr}"
        )
        gerber_files = list(gerbers_dir.glob("*.g*"))
        assert gerber_files, f"No Gerber files produced in {gerbers_dir}"

    def test_full_mini_flow_resistor_divider(self) -> None:
        """Full flow: new → 2× add-component → set-board-size → BOM + DRC + Gerbers.

        This is a resistor-divider (R1=10k, R2=4.7k).  The test verifies that
        the complete chain of commands completes without error and that the
        key output artifacts (netlist, BOM, DRC report, Gerber files) are all
        produced and non-empty.
        """
        # --- schematic phase ---
        assert self._run("new", "TestProj").returncode == 0
        assert self._run("add-component", "Device:R", "R1", "--value", "10k").returncode == 0
        assert self._run("add-component", "Device:R", "R2", "--value", "4.7k").returncode == 0

        sch_path = self.projects_dir / "TestProj" / "TestProj.kicad_sch"
        pcb_path = self.projects_dir / "TestProj" / "TestProj.kicad_pcb"
        assert sch_path.exists()
        assert pcb_path.exists()

        # BOM must list both components
        bom_result = self._run("export-bom")
        assert bom_result.returncode == 0, f"export-bom failed:\n{bom_result.stderr}"
        bom_output = bom_result.stdout + bom_result.stderr
        assert "R1" in bom_output and "R2" in bom_output, f"BOM missing R1/R2:\n{bom_output}"

        # --- PCB phase ---
        board_result = self._run("set-board-size", "50x30")
        assert board_result.returncode == 0, f"set-board-size failed:\n{board_result.stderr}"

        kicad_cli = shutil.which("kicad-cli") or "/usr/bin/kicad-cli"

        # kicad-cli must load and export netlist from schematic
        netlist_path = self.projects_dir / "TestProj" / "TestProj.xml"
        net_proc = subprocess.run(
            [
                kicad_cli,
                "sch",
                "export",
                "netlist",
                "--format",
                "kicadsexpr",
                "--output",
                str(netlist_path),
                str(sch_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert net_proc.returncode == 0, (
            f"Netlist export failed (exit {net_proc.returncode}):\n{net_proc.stderr}"
        )
        assert '(ref "R1")' in netlist_path.read_text() or "(ref R1)" in netlist_path.read_text()
        assert '(ref "R2")' in netlist_path.read_text() or "(ref R2)" in netlist_path.read_text()

        # kicad-cli DRC must run without hard error (violations allowed, crash not)
        drc_path = self.projects_dir / "TestProj" / "drc_report.json"
        drc_proc = subprocess.run(
            [
                kicad_cli,
                "pcb",
                "drc",
                "--output",
                str(drc_path),
                "--format",
                "json",
                str(pcb_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert drc_proc.returncode == 0, (
            f"kicad-cli pcb drc crashed (exit {drc_proc.returncode}):\n{drc_proc.stderr}"
        )
        assert drc_path.exists(), "DRC report not created"

        # Gerber export must succeed and produce files
        gerbers_dir = self.projects_dir / "TestProj" / "gerbers"
        ger_proc = subprocess.run(
            [
                kicad_cli,
                "pcb",
                "export",
                "gerbers",
                "--output",
                str(gerbers_dir),
                str(pcb_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert ger_proc.returncode == 0, (
            f"Gerber export failed (exit {ger_proc.returncode}):\n{ger_proc.stderr}"
        )
        assert list(gerbers_dir.glob("*.g*")), "No Gerber files produced"


# ---------------------------------------------------------------------------
# P7.3 helpers
# ---------------------------------------------------------------------------

_SYMBOLS_DIR = Path(__file__).parent.parent / "fixtures" / "symbols"

_MINIMAL_IR: dict = {
    "version": "1",
    "components": [{"ref": "R1", "symbol": "TestLib:R", "value": "10k"}],
    "nets": [{"name": "N1", "pins": [{"ref": "R1", "pin": "1"}]}],
}


def _extract_bindings_from_sch(path: Path) -> list[dict[str, str]]:
    """Parse OpenClaw:bind= markers from a managed schematic file.

    Returns a sorted list of dicts so two runs can be compared for equality.
    """
    doc = SchematicDoc.load(path)
    return sorted(doc.extract_pin_label_bindings(), key=lambda b: (b["ref"], b["pin"]))


# ---------------------------------------------------------------------------
# P7.3: strict-mode new-from-netlist + compile-netlist integration tests
# ---------------------------------------------------------------------------


@requires_kicad
class TestNewFromNetlistKicadMode:
    """P7.3: strict-mode new-from-netlist and compile-netlist with kicad-cli.

    Verifies that:
    - new-from-netlist --mode kicad completes successfully
    - kicad-cli can load the generated main schematic
    - compile-netlist (alias) is wired to the same function and also succeeds
    - Both commands produce identical pin→net binding markers in the managed
      schematic, confirming deterministic output
    """

    @pytest.fixture(autouse=True)
    def _setup(self, home_tmp: Path) -> None:
        self.home = str(home_tmp)
        self.projects_dir = home_tmp / "kicad-projects"
        # IR JSON must live under home_tmp so kicad-cli Flatpak sandbox can
        # reach it if the script ever passes it to kicad-cli (it does not
        # currently, but keep the constraint for defensive correctness).
        self.ir_path = home_tmp / "ir.json"
        self.ir_path.write_text(json.dumps(_MINIMAL_IR), encoding="utf-8")

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return _run_script(list(args), self.home)

    # ------------------------------------------------------------------
    # Test 1: new-from-netlist --mode kicad exits 0 and creates files
    # ------------------------------------------------------------------

    def test_new_from_netlist_kicad_mode_succeeds(self) -> None:
        """new-from-netlist --mode kicad must exit 0 and create all project files."""
        result = self._run(
            "new-from-netlist",
            "--name",
            "TestNetlist",
            "--netlist",
            str(self.ir_path),
            "--symbols-dir",
            str(_SYMBOLS_DIR),
            "--mode",
            "kicad",
        )
        assert result.returncode == 0, (
            f"new-from-netlist --mode kicad failed (exit {result.returncode}):\n{result.stderr}"
        )
        project_dir = self.projects_dir / "TestNetlist"
        assert (project_dir / "TestNetlist.kicad_sch").exists(), "Main schematic not created"
        assert (project_dir / MANAGED_SHEET_FILE).exists(), "Managed schematic not created"

    # ------------------------------------------------------------------
    # Test 2: kicad-cli must be able to load the generated main schematic
    # ------------------------------------------------------------------

    def test_new_from_netlist_kicad_mode_main_sch_loadable(self) -> None:
        """kicad-cli sch export netlist must succeed on the generated main schematic."""
        result = self._run(
            "new-from-netlist",
            "--name",
            "TestNetlist",
            "--netlist",
            str(self.ir_path),
            "--symbols-dir",
            str(_SYMBOLS_DIR),
            "--mode",
            "kicad",
        )
        assert result.returncode == 0, f"new-from-netlist --mode kicad failed:\n{result.stderr}"

        sch_path = self.projects_dir / "TestNetlist" / "TestNetlist.kicad_sch"
        netlist_out = self.projects_dir / "TestNetlist" / "TestNetlist.xml"

        kicad_cli = shutil.which("kicad-cli") or "/usr/bin/kicad-cli"
        proc = subprocess.run(
            [
                kicad_cli,
                "sch",
                "export",
                "netlist",
                "--format",
                "kicadsexpr",
                "--output",
                str(netlist_out),
                str(sch_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, (
            f"kicad-cli sch export netlist failed (exit {proc.returncode}):\n{proc.stderr}"
        )

    # ------------------------------------------------------------------
    # Test 3: compile-netlist alias is wired and succeeds
    # ------------------------------------------------------------------

    def test_compile_netlist_alias_succeeds(self) -> None:
        """compile-netlist (alias for new-from-netlist) must exit 0 and create project files."""
        result = self._run(
            "compile-netlist",
            "--name",
            "TestCompile",
            "--netlist",
            str(self.ir_path),
            "--symbols-dir",
            str(_SYMBOLS_DIR),
            "--mode",
            "kicad",
        )
        assert result.returncode == 0, (
            f"compile-netlist --mode kicad failed (exit {result.returncode}):\n{result.stderr}"
        )
        project_dir = self.projects_dir / "TestCompile"
        assert (project_dir / "TestCompile.kicad_sch").exists(), "Main schematic not created"
        assert (project_dir / MANAGED_SHEET_FILE).exists(), "Managed schematic not created"

    # ------------------------------------------------------------------
    # Test 4: both commands produce identical pin→net binding markers
    # ------------------------------------------------------------------

    def test_both_commands_produce_equivalent_bindings(self) -> None:
        """new-from-netlist and compile-netlist must write identical pin→net bindings.

        This confirms the alias shares the same underlying function and that
        generation is deterministic at the managed-schematic level.
        """
        for cmd, name in (("new-from-netlist", "ProjA"), ("compile-netlist", "ProjB")):
            res = self._run(
                cmd,
                "--name",
                name,
                "--netlist",
                str(self.ir_path),
                "--symbols-dir",
                str(_SYMBOLS_DIR),
                "--mode",
                "kicad",
            )
            assert res.returncode == 0, f"{cmd} failed (exit {res.returncode}):\n{res.stderr}"

        bindings_a = _extract_bindings_from_sch(self.projects_dir / "ProjA" / MANAGED_SHEET_FILE)
        bindings_b = _extract_bindings_from_sch(self.projects_dir / "ProjB" / MANAGED_SHEET_FILE)
        assert bindings_a == bindings_b, (
            f"Binding mismatch between new-from-netlist and compile-netlist:\n"
            f"  ProjA: {bindings_a}\n  ProjB: {bindings_b}"
        )
        assert {"ref": "R1", "pin": "1", "net_name": "N1"} in bindings_a, (
            f"Expected R1 pin 1 → N1 binding not found in: {bindings_a}"
        )

"""Phase 6 — Integration tests: Graphviz end-to-end layout + kicad-cli validation.

6.2  TestKiCadCLIERC: kicad-cli sch erc must exit 0 with no error-level violations
     on generated schematics.  Skipped if ``kicad-cli`` is not on PATH.

6.4  Integration tests
    - TestGraphvizEndToEnd  : full IR → GraphvizLayoutEngine → managed schematic
      pipeline, verifying non-overlapping positions and schema parsability.
      Skipped if the ``dot`` binary is not installed.
    - TestKiCadCLINetlistExport: end-to-end IR → schematic → kicad-cli netlist
      export, confirming the generated file is fully parseable by kicad-cli.
      Skipped if ``kicad-cli`` is not on PATH.

Note: kicad-cli on this system may be a Flatpak wrapper that only sees paths
under the user's home directory.  Tests that invoke kicad-cli must store their
scratch output under ``home_tmp`` (~/tmp/kicad-tests/), not /tmp.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from argparse import Namespace
from pathlib import Path

import kicad_pcb.graphviz_layout as _gv_mod
import pytest
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands.netlist import cmd_new_from_netlist
from kicad_pcb.lint import lint_schematic_layout
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr import parse as _parse_sexpr

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Paths + availability checks
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

_dot_available = _gv_mod.find_dot_binary() is not None
_kicad_available = shutil.which("kicad-cli") is not None

requires_graphviz = pytest.mark.skipif(
    not _dot_available,
    reason="graphviz dot not found on PATH or GRAPHVIZ_DOT",
)
requires_kicad = pytest.mark.skipif(
    not _kicad_available,
    reason="kicad-cli not found on PATH",
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _ir(
    components: list[tuple[str, str]],
    nets: list[tuple[str, list[tuple[str, str]]]],
    *,
    version: str = "1",
) -> CircuitIR:
    comps = [ComponentIR(ref=ref, symbol=sym) for ref, sym in components]
    ir_nets = [
        NetIR(name=name, pins=[PinRefIR(ref=r, pin=p) for r, p in pins]) for name, pins in nets
    ]
    return CircuitIR(version=version, components=comps, nets=ir_nets)


def _write_ir(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _new_from_netlist(tmp_dir: Path, ir_payload: dict, *, name: str, layout: str) -> object:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ir_path = tmp_dir / "ir.json"
    _write_ir(ir_path, ir_payload)
    return cmd_new_from_netlist(
        Namespace(
            name=name,
            out_dir=str(tmp_dir),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(_FIXTURES_DIR),
            mode="internal",
            layout=layout,
        )
    )


# ---------------------------------------------------------------------------
# IR payloads shared between tests
# ---------------------------------------------------------------------------

_CHAIN_IR = {
    "version": "1",
    "components": [
        {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
        {"ref": "R2", "symbol": "TestLib:R", "value": "10k"},
        {"ref": "R3", "symbol": "TestLib:R", "value": "10k"},
    ],
    "nets": [
        {"name": "N1", "pins": [{"ref": "R1", "pin": "1"}, {"ref": "R2", "pin": "1"}]},
        {"name": "N2", "pins": [{"ref": "R2", "pin": "2"}, {"ref": "R3", "pin": "1"}]},
    ],
}

_DIVIDER_IR = {
    "version": "1",
    "components": [
        {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
        {"ref": "R2", "symbol": "TestLib:R", "value": "10k"},
    ],
    "nets": [
        {"name": "VCC", "pins": [{"ref": "R1", "pin": "1"}]},
        {"name": "VMID", "pins": [{"ref": "R1", "pin": "2"}, {"ref": "R2", "pin": "1"}]},
        {"name": "GND", "pins": [{"ref": "R2", "pin": "2"}]},
    ],
}


# ---------------------------------------------------------------------------
# 6.4  TestGraphvizEndToEnd
#
# Runs the full pipeline through GraphvizLayoutEngine (requires dot) and
# verifies the managed schematic is parseable + has distinct positions.
# ---------------------------------------------------------------------------


@requires_graphviz
class TestGraphvizEndToEnd:
    """Full IR → GraphvizLayoutEngine → managed schematic pipeline."""

    def test_graphviz_positions_non_overlapping(self) -> None:
        """Graphviz-placed symbols must not share an (x, y) grid position.

        Constructs positions directly from the engine (no file I/O needed)
        and verifies the LAY003-check passes when those positions are used
        to build a minimal schematic representation.
        """
        dot = _gv_mod.find_dot_binary()
        assert dot is not None

        ir = _ir(
            [("R1", "Device:R"), ("R2", "Device:R"), ("R3", "Device:R")],
            [
                ("N1", [("R1", "1"), ("R2", "1")]),
                ("N2", [("R2", "2"), ("R3", "1")]),
            ],
        )
        engine = _gv_mod.GraphvizLayoutEngine(dot_path=dot, seed=7)
        positions = engine.compute_symbol_positions(ir)

        # Build a minimal kicad_sch from the returned positions so we can
        # run lint_schematic_layout against the same data structure that the
        # full pipeline would produce.
        parts = []
        for i, (ref, (x, y, _)) in enumerate(sorted(positions.items())):
            uid = f"00000000-0000-0000-0000-{i:012d}"
            parts.append(f'(symbol (lib_id "Device:R") (at {x} {y} 0) (uuid "{uid}"))')
        body = "\n  ".join(parts)
        root = _parse_sexpr(
            f"(kicad_sch (version 20230121) (generator test)\n"
            f"  (lib_symbols)\n  {body}\n"
            f'  (sheet_instances (path "/" (page "1")))\n)'
        )

        issues = lint_schematic_layout(root)
        lay003 = [i for i in issues if i.code == "LAY003"]
        assert not lay003, f"LAY003 overlap in Graphviz output: {positions}"

    def test_graphviz_pipeline_managed_schematic_has_all_refs(self, tmp_path: Path) -> None:
        """IR → graphviz layout → schematic must place all component refs."""
        result = _new_from_netlist(tmp_path, _CHAIN_IR, name="GvChain", layout="graphviz")
        assert result.symbols_added == 3

        doc = SchematicDoc.load(result.managed_schematic_path)
        placed = {s["ref"] for s in doc.list_symbols()}
        assert {"R1", "R2", "R3"} <= placed

    def test_graphviz_pipeline_positions_distinct(self, tmp_path: Path) -> None:
        """Graphviz-placed symbols must occupy distinct (x, y) positions."""
        result = _new_from_netlist(tmp_path, _CHAIN_IR, name="GvDistinct", layout="graphviz")
        doc = SchematicDoc.load(result.managed_schematic_path)
        positions = [(s["x"], s["y"]) for s in doc.list_symbols()]
        assert len(set(positions)) == len(positions), (
            f"Duplicate positions in Graphviz output: {positions}"
        )

    def test_graphviz_pipeline_output_stable(self, tmp_path: Path) -> None:
        """Two Graphviz runs on the same IR with the same seed produce
        identical symbol positions (deterministic layout)."""
        result1 = _new_from_netlist(tmp_path / "run1", _CHAIN_IR, name="Gv1", layout="graphviz")
        result2 = _new_from_netlist(tmp_path / "run2", _CHAIN_IR, name="Gv2", layout="graphviz")
        doc1 = SchematicDoc.load(result1.managed_schematic_path)
        doc2 = SchematicDoc.load(result2.managed_schematic_path)
        pos1 = {s["ref"]: (s["x"], s["y"]) for s in doc1.list_symbols()}
        pos2 = {s["ref"]: (s["x"], s["y"]) for s in doc2.list_symbols()}
        assert pos1 == pos2, f"Graphviz layout unstable across runs:\n  run1={pos1}\n  run2={pos2}"


# ---------------------------------------------------------------------------
# 6.4  TestKiCadCLINetlistExport
#
# kicad-cli must be able to export a netlist from the generated schematic.
# This proves the schematic is structurally valid (parseable by kicad-cli).
# Uses home_tmp so paths are inside the Flatpak sandbox boundary.
# ---------------------------------------------------------------------------


@requires_kicad
class TestKiCadCLINetlistExport:
    """Generated schematic is valid per kicad-cli (netlist export succeeds)."""

    def test_divider_schematic_exportable(self, home_tmp: Path) -> None:
        """cmd_new_from_netlist → managed schematic → kicad-cli sch export exits 0."""
        result = _new_from_netlist(home_tmp, _DIVIDER_IR, name="DividerKC", layout="graphviz")
        sch_path = result.managed_schematic_path
        assert sch_path.exists(), f"Managed schematic not created at {sch_path}"

        netlist_out = home_tmp / "exported.xml"
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
            f"kicad-cli failed (exit {proc.returncode}):\n{proc.stderr}\n{proc.stdout}"
        )
        assert netlist_out.exists(), "kicad-cli did not produce output file"

    def test_divider_schematic_lists_components_in_netlist(self, home_tmp: Path) -> None:
        """kicad-cli netlist export from IR contains both R1 and R2."""
        result = _new_from_netlist(home_tmp, _DIVIDER_IR, name="DividerKCL", layout="graphviz")
        sch_path = result.managed_schematic_path

        netlist_out = home_tmp / "netlist.xml"
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
                str(netlist_out),
                str(sch_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        netlist_content = netlist_out.read_text(encoding="utf-8")
        assert "R1" in netlist_content, "R1 not found in kicad-cli netlist"
        assert "R2" in netlist_content, "R2 not found in kicad-cli netlist"

    def test_three_component_chain_exportable(self, home_tmp: Path) -> None:
        """A three-component IR chain passes kicad-cli netlist export."""
        result = _new_from_netlist(home_tmp, _CHAIN_IR, name="ChainKC", layout="graphviz")

        netlist_out = home_tmp / "chain_netlist.xml"
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
                str(result.managed_schematic_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, (
            f"kicad-cli failed on 3-component chain (exit {proc.returncode}):\n{proc.stderr}"
        )


# ---------------------------------------------------------------------------
# 6.2  TestKiCadCLIERC
#
# kicad-cli sch erc must exit 0 on generated schematics, and the JSON report
# must contain zero error-severity violations.
# Uses home_tmp so paths are inside the Flatpak sandbox boundary.
# ---------------------------------------------------------------------------


@requires_kicad
class TestKiCadCLIERC:
    """Generated schematic passes kicad-cli ERC (no error-level violations)."""

    def test_erc_exits_zero_on_divider_schematic(self, home_tmp: Path) -> None:
        """cmd_new_from_netlist + kicad-cli sch erc must exit 0 on divider IR."""
        result = _new_from_netlist(home_tmp, _DIVIDER_IR, name="DividerERC", layout="graphviz")
        sch_path = result.managed_schematic_path
        assert sch_path.exists(), f"Managed schematic not created at {sch_path}"

        erc_out = home_tmp / "erc_report.json"
        kicad_cli = shutil.which("kicad-cli") or "/usr/bin/kicad-cli"
        proc = subprocess.run(
            [
                kicad_cli,
                "sch",
                "erc",
                "--format",
                "json",
                "--severity-error",
                "--output",
                str(erc_out),
                str(sch_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, (
            f"kicad-cli sch erc crashed (exit {proc.returncode}):\n{proc.stderr}\n{proc.stdout}"
        )
        assert erc_out.exists(), "kicad-cli did not produce ERC report"

    def test_erc_no_error_violations_on_divider_schematic(self, home_tmp: Path) -> None:
        """ERC JSON report for divider IR must contain zero error-severity violations."""
        result = _new_from_netlist(home_tmp, _DIVIDER_IR, name="DividerERCJ", layout="graphviz")
        sch_path = result.managed_schematic_path

        erc_out = home_tmp / "erc_violations.json"
        kicad_cli = shutil.which("kicad-cli") or "/usr/bin/kicad-cli"
        proc = subprocess.run(
            [
                kicad_cli,
                "sch",
                "erc",
                "--format",
                "json",
                "--severity-error",
                "--output",
                str(erc_out),
                str(sch_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, (
            f"kicad-cli sch erc failed (exit {proc.returncode}):\n{proc.stderr}"
        )
        report = json.loads(erc_out.read_text(encoding="utf-8"))
        violations = report.get("violations", [])
        errors = [v for v in violations if v.get("severity") == "error"]
        assert not errors, (
            f"kicad-cli ERC found {len(errors)} error-level violation(s):\n"
            + "\n".join(str(e) for e in errors)
        )

    def test_erc_exits_zero_on_chain_schematic(self, home_tmp: Path) -> None:
        """kicad-cli sch erc must exit 0 on a three-component chain IR."""
        result = _new_from_netlist(home_tmp, _CHAIN_IR, name="ChainERC", layout="graphviz")
        sch_path = result.managed_schematic_path

        erc_out = home_tmp / "chain_erc.json"
        kicad_cli = shutil.which("kicad-cli") or "/usr/bin/kicad-cli"
        proc = subprocess.run(
            [
                kicad_cli,
                "sch",
                "erc",
                "--format",
                "json",
                "--severity-error",
                "--output",
                str(erc_out),
                str(sch_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, (
            f"kicad-cli sch erc failed on chain IR (exit {proc.returncode}):\n{proc.stderr}"
        )
        report = json.loads(erc_out.read_text(encoding="utf-8"))
        violations = report.get("violations", [])
        errors = [v for v in violations if v.get("severity") == "error"]
        assert not errors, (
            f"kicad-cli ERC found {len(errors)} error-level violation(s) in chain:\n"
            + "\n".join(str(e) for e in errors)
        )
